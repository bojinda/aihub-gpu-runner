"""Offline utilization settling under one existing owned-cleanup deadline."""
import errno
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from aihub_gpu_runner.backends import CleanupPending, Uncertain
from aihub_gpu_runner.core import AtomicStore, Runner, RecoveryRequired
from aihub_gpu_runner.host_stage import HostStage
from aihub_gpu_runner.ollama_cleanup import OllamaCleanupGuard
from tests import test_ollama_physical_cleanup as fixture
from tests import test_ollama_exit_races as exit_fixture


class UtilizationSettlingTests(unittest.TestCase):
    def setUp(self):
        self.c = fixture.PhysicalCleanupTests('runTest')
        self.c.setUp()
        self.addCleanup(self.c.doCleanups)
        self.c.target.cleanup_timeout = 60
        self.start = self.c.clock.t
        self.deadline = self.start + 60
        self.utilization = lambda: 0
        self.c.memory = lambda: 1
        self.samples, self.sleeps = [], []
        self.on_sleep = None
        original = self.c.command

        def command(argv, deadline):
            value = original(argv, deadline)
            if argv[1].startswith('--id='):
                self.samples.append(self.c.clock.t)
                return f'{fixture.GPU1},{self.c.memory()},{self.utilization()},30,0,No'
            return value
        self.c.observer.command.side_effect = command

        def sleep(duration):
            self.sleeps.append(duration)
            self.c.clock.sleep(duration)
            if self.on_sleep: self.on_sleep()
        patch('time.sleep', sleep).start()

    def observations(self):
        return [r['ollama_cleanup_observation'] for r in self.c.records if 'ollama_cleanup_observation' in r]

    def prepare(self):
        self.c.guard.prepare(self.deadline)

    def verify(self):
        return self.c.guard.verify(self.deadline)

    def sequence(self, values):
        readings = iter(values)
        self.utilization = lambda: next(readings)

    def reject(self, reason, kind=RecoveryRequired):
        with self.assertRaisesRegex(kind, reason): self.verify()
        self.assertIn('gpu1', self.c.state['owners'])

    def worker(self):
        self.c.clock.t = 1
        self.start, self.deadline = 1, 61
        return self.c.worker()

    def test_transient_spike_is_recorded_then_three_complete_idle_samples(self):
        self.prepare(); self.sequence([75, 0, 0, 0])
        proof = self.verify()
        self.assertEqual(proof['stable_samples'], 3)
        self.assertEqual([o['physical_gpus']['gpu1']['utilization_pct'] for o in self.observations()], [75, 0, 0, 0])
        self.assertEqual(self.sleeps[0], 1)
        self.assertEqual(len(self.observations()), 4)
        self.assertEqual(self.c.observer.driver_health.call_count, 4)
        self.assertEqual(len([c for c in self.c.calls if c[:2] == ('GET', '/api/ps')]), 4)

    def test_variable_settling_times_use_same_deadline(self):
        for settling in (.2, 2.4, 17.2, 57):
            with self.subTest(seconds=settling):
                self.start = self.c.clock.t; self.deadline = self.start + 60
                self.c.records.clear(); self.samples.clear(); self.sleeps.clear()
                self.c.guard = self.c.new_guard()
                self.utilization = lambda: 45 if self.c.clock.t - self.start < settling else 0
                self.prepare(); self.assertEqual(self.verify()['stable_samples'], 3)
                self.assertGreaterEqual(self.c.clock.t - self.start, settling)
                self.assertLess(self.c.clock.t, self.deadline)
                self.assertEqual([o['physical_gpus']['gpu1']['utilization_pct'] for o in self.observations()][-3:], [0, 0, 0])
                self.assertTrue(all(s == 1 for s in self.sleeps[:-2]))

    def test_spike_after_two_idle_samples_resets_consecutive_count(self):
        self.prepare(); self.sequence([0, 0, 50, 0, 0, 0])
        self.assertEqual(self.verify()['stable_samples'], 3)
        self.assertEqual(len(self.observations()), 6)
        self.assertEqual(self.sleeps[2], 1)

    def test_threshold_is_unchanged_and_inclusive(self):
        self.prepare(); self.sequence([5.01, 5, 5, 5])
        self.verify()
        self.assertEqual(self.sleeps[0], 1)
        self.assertEqual(self.c.guard.p['idle_utilization_pct'], 5)

    def test_persistent_utilization_expires_at_original_deadline(self):
        self.prepare(); self.utilization = lambda: 80
        self.reject('ollama_cleanup_utilization_pending', CleanupPending)
        self.assertAlmostEqual(self.c.clock.t, self.deadline)
        self.assertTrue(all(s <= 1 for s in self.sleeps))
        self.assertGreaterEqual(len(self.observations()), 59)
        self.assertEqual(self.c.records[-1]['ollama_cleanup_failure']['reason'], 'ollama_cleanup_utilization_pending')
        self.assertEqual(self.c.records[-1]['ollama_cleanup_failure']['phase'], 'deadline')

    def test_sleep_is_capped_by_remaining_shared_budget(self):
        self.deadline = self.start + .25
        self.prepare(); self.utilization = lambda: 80
        self.reject('ollama_cleanup_utilization_pending', CleanupPending)
        self.assertEqual(len(self.sleeps), 1)
        self.assertAlmostEqual(self.sleeps[0], .25)
        self.assertAlmostEqual(self.c.clock.t, self.deadline)

    def test_memory_breach_after_utilization_settles_resets_stability(self):
        self.prepare(); self.sequence([80, 0, 0, 0, 0, 0, 0])
        self.c.apps = lambda: fixture.GPU0 + ',202,2000\n'
        self.c.memory = lambda: 96 if len(self.samples) == 4 else 1
        self.verify()
        obs = self.observations()
        self.assertEqual(len(obs), 7)
        self.assertEqual(obs[3]['physical_gpus']['gpu1']['memory_mb'], 96)
        self.assertEqual([o['physical_gpus']['gpu1']['memory_mb'] for o in obs[-3:]], [1, 1, 1])

    def test_two_idle_samples_before_deadline_are_not_a_complete_proof(self):
        self.deadline = self.start + 1.15
        self.prepare(); self.sequence([80, 0, 0])
        self.reject('ollama_cleanup_utilization_pending', CleanupPending)
        self.assertEqual(len(self.observations()), 3)
        self.assertAlmostEqual(self.c.clock.t, self.deadline)

    def test_invalid_utilization_is_not_a_settling_observation(self):
        self.prepare()
        for value in ('NaN', 'inf', -1, 101, 'invalid'):
            with self.subTest(value=value):
                self.utilization = lambda: value
                self.reject('invalid_gpu_number|gpu_health_unverified')
        self.assertEqual(self.sleeps, [])

    def test_unknown_gpu_activity_is_not_utilization_settling(self):
        self.prepare(); self.utilization = lambda: 80
        self.c.apps = lambda: fixture.GPU1 + ',999,1\n'
        self.reject('unknown_or_duplicate_gpu_process')
        self.assertEqual(self.sleeps, [])

    def test_foreign_activity_arriving_during_settling_stops_polling(self):
        self.prepare(); self.utilization = lambda: 80
        self.on_sleep = lambda: setattr(self.c, 'apps', lambda: fixture.GPU1 + ',999,1\n')
        self.reject('unknown_or_duplicate_gpu_process')
        self.assertEqual(self.sleeps, [1])

    def test_known_other_service_on_gpu1_is_not_utilization_settling(self):
        self.prepare(); self.utilization = lambda: 80
        self.c.apps = lambda: fixture.GPU1 + ',202,1\n'
        self.reject('gpu_activity_unexplained')
        self.assertEqual(self.sleeps, [])

    def test_pid_reuse_during_wait_is_rejected(self):
        self.prepare(); self.utilization = lambda: 80
        self.on_sleep = lambda: self.c.ollama_pids.update({'304': 'reused'})
        self.reject('new_or_reused_process')
        self.assertEqual(self.sleeps, [1])

    def test_birth_change_after_gpu_observation_is_rejected_before_retry(self):
        self.prepare(); self.utilization = lambda: 80
        self.c.apps = lambda: fixture.GPU1 + ',304,1\n'
        self.c.births['304'] = 'changed'
        self.reject('process_birth_changed')
        self.assertEqual(self.sleeps, [])

    def test_container_restart_during_wait_is_rejected(self):
        self.prepare(); self.utilization = lambda: 80
        self.on_sleep = lambda: self.c.metas['ollama']['State'].update(StartedAt='restarted')
        self.reject('generation_changed')
        self.assertEqual(self.sleeps, [1])

    def test_gpu0_music_coexists_and_is_never_forced_idle(self):
        self.prepare(); self.sequence([80, 0, 0, 0])
        self.c.apps = lambda: fixture.GPU0 + ',202,2000\n'
        proof = self.verify()
        self.assertEqual(set(proof['observation']['physical_gpus']), {'gpu1'})
        self.assertFalse(any('--id=' + fixture.GPU0 in c for c in self.c.commands))

    def test_unverified_residency_during_wait_is_rejected(self):
        self.prepare(); self.utilization = lambda: 80
        self.on_sleep = lambda: self.c.models.append({'name': 'foreign'})
        self.reject('unexplained_backend_activity')
        self.assertEqual(self.sleeps, [1])

    def test_driver_fault_during_wait_is_rejected(self):
        self.prepare(); self.utilization = lambda: 80
        self.on_sleep = lambda: setattr(self.c.observer.driver_health, 'side_effect',
            RecoveryRequired('kernel_gpu_driver_fault_requires_operator'))
        self.reject('kernel_gpu_driver_fault')
        self.assertEqual(self.sleeps, [1])

    def test_incomplete_observation_is_not_an_idle_sample_or_retry(self):
        self.prepare(); self.sequence([80, 0])
        self.on_sleep = lambda: setattr(self.c.observer.driver_health, 'side_effect', Uncertain('driver_observation_lost'))
        self.reject('driver_observation_lost', Uncertain)
        self.assertEqual(len(self.observations()), 1)
        self.assertEqual(self.sleeps, [1])

    def test_storage_uncertainty_is_not_utilization_settling(self):
        self.prepare(); self.utilization = lambda: 80
        record = self.c.guard.record
        def write(**fields):
            if 'ollama_cleanup_observation' in fields: raise OSError(errno.ENOSPC, 'PRIVATE')
            record(**fields)
        self.c.guard.record = write
        self.reject('PRIVATE', OSError)
        self.assertEqual(self.sleeps, [])
        self.assertEqual(self.c.records[-1]['ollama_cleanup_failure']['reason'], 'ollama_cleanup_persistence_error')

    def test_without_completed_owned_work_high_utilization_remains_rejected(self):
        job = self.c.store.job('owned-job')
        job.update(host_stage=True, host_exit_code=0, host_requests=[]); self.c.store.put_job(job)
        self.prepare(); self.utilization = lambda: 80
        self.reject('gpu_activity_unexplained')
        self.assertEqual(self.sleeps, [])

    def test_worker_unloads_once_and_releases_only_after_three_idle_samples(self):
        runner, lease, payload = self.worker(); self.sequence([80, 0, 0, 0])
        runner._work('owned-job', lease)
        job = self.c.store.job('owned-job')
        self.assertEqual(job['state'], 'success')
        self.assertEqual(job['cleanup_proof']['physical_ollama_idle']['stable_samples'], 3)
        lease.release.assert_called_once()
        posts = [c for c in self.c.calls if c[0] == 'POST']
        self.assertEqual(len(posts), 2)
        self.assertEqual(sum(p[2].get('keep_alive') == 0 for p in posts), 1)
        count = len(self.c.calls)
        runner.submit('owned-job', 'ollama', 'generate', payload)
        self.assertEqual(len(self.c.calls), count)

    def test_worker_deadline_retains_owner_and_specific_pending_code_without_replay(self):
        runner, lease, payload = self.worker(); self.utilization = lambda: 80
        runner._work('owned-job', lease)
        job = self.c.store.job('owned-job')
        self.assertEqual(job['state'], 'cleanup_pending')
        self.assertEqual(job['error_code'], 'ollama_cleanup_utilization_pending')
        self.assertEqual(job['ollama_cleanup_failure']['reason'], job['error_code'])
        self.assertNotIn('cleanup_proof', job)
        self.assertNotIn('results/owned-job.json', self.c.store.values)
        self.assertAlmostEqual(self.c.clock.t, 61)
        lease.release.assert_not_called()
        self.assertIn('gpu1', self.c.state['owners'])
        count = len(self.c.calls)
        runner.submit('owned-job', 'ollama', 'generate', payload)
        self.assertEqual(len(self.c.calls), count)

    def test_owned_unload_time_consumes_the_same_sixty_second_budget(self):
        runner, lease, _ = self.worker(); self.utilization = lambda: 80
        original = self.c.transport.json
        def request(method, route, payload=None, timeout=1):
            value = original(method, route, payload, timeout)
            if method == 'POST' and payload and payload.get('keep_alive') == 0:
                self.c.clock.t += 45
            return value
        self.c.transport.json = request
        runner._work('owned-job', lease)
        self.assertAlmostEqual(self.c.clock.t, 61)
        self.assertLessEqual(len(self.samples), 15)
        self.assertEqual(self.c.store.job('owned-job')['error_code'], 'ollama_cleanup_utilization_pending')
        lease.release.assert_not_called()

    def test_restart_keeps_durable_owner_diagnostic_and_does_not_replay(self):
        runner, lease, payload = self.worker(); self.utilization = lambda: 80
        runner._work('owned-job', lease)
        # Reopen an actual local durable store and exercise Runner startup. No
        # scheduler/recovery thread is allowed to run or contact a backend.
        with tempfile.TemporaryDirectory() as tmp:
            disk = AtomicStore(Path(tmp) / 'state')
            disk.put_job(self.c.store.job('owned-job'))
            gate = types.SimpleNamespace(store=disk, snapshot=self.c.a.snapshot)
            recovery = Mock()
            with patch('aihub_gpu_runner.core.threading.Thread.start'):
                restarted = Runner(gate, {'ollama': self.c.target}, {'ollama': self.c.backend}, recovery=recovery)
            try:
                recovery.startup.assert_called_once()
                count = len(self.c.calls)
                job = restarted.submit('owned-job', 'ollama', 'generate', payload)
                self.assertEqual(job['state'], 'reconciling')
                self.assertEqual(job['ollama_cleanup_failure']['reason'], 'ollama_cleanup_utilization_pending')
                self.assertEqual(len(self.c.calls), count)
                self.assertIn('gpu1', gate.snapshot()['owners'])
                lease.release.assert_not_called()
            finally:
                restarted.pool.shutdown()
                restarted.service_lock.close()

    def test_host_stage_uses_same_utilization_guard_without_extra_unload(self):
        job = self.c.store.job('owned-job')
        job.update(host_stage=True, host_exit_code=0, host_requests=[{'state': 'completed', 'model': 'synthetic'}])
        self.c.store.put_job(job); self.sequence([80, 0, 0, 0])
        stage = types.SimpleNamespace(store=self.c.store, job_id='owned-job', is_ollama=True,
            dual=False, admission=self.c.a, target=self.c.target,
            lease=types.SimpleNamespace(owner=self.c.owner),
            transport_factory=lambda url: self.c.transport, update=lambda **fields: None)
        original = OllamaCleanupGuard.__init__
        def init(guard, *args, **kwargs):
            kwargs['birth_reader'] = lambda pid: self.c.births[pid]
            original(guard, *args, **kwargs)
        with patch('aihub_gpu_runner.ollama_cleanup.RecoveryObserver', return_value=self.c.observer), \
                patch.object(OllamaCleanupGuard, '__init__', init):
            proof = HostStage._cleanup(stage)
        self.assertEqual(proof['physical_ollama_idle']['stable_samples'], 3)
        self.assertEqual(len([c for c in self.c.calls if c[0] == 'POST']), 1)


class UtilizationExitProtectionTests(unittest.TestCase):
    def test_verified_child_exit_and_utilization_spike_require_fresh_idle_proof(self):
        race = exit_fixture.ExitRaceTests('runTest'); race.setUp()
        self.addCleanup(race.doCleanups)
        race.c.target.cleanup_timeout = 60
        command = race.c.observer.command.side_effect
        readings = iter([80, 0, 0, 0])
        def observe(argv, deadline):
            result = command(argv, deadline)
            if argv[1].startswith('--id='): return f'{fixture.GPU1},1,{next(readings)},30,0,No'
            return result
        race.c.observer.command.side_effect = observe
        race.c.prepare(); race.arm_exit()
        proof = race.c.guard.verify(race.c.clock.t + 60)
        self.assertEqual(proof['stable_samples'], 3)
        obs = race.observations()
        self.assertEqual([o['process_snapshot_discarded'] for o in obs], [True, False, False, False])
        self.assertEqual(obs[-1]['exited_ollama_processes'], {'304': '40'})
        self.assertGreaterEqual(race.c.clock.t, 1)


if __name__ == '__main__': unittest.main()
