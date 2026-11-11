"""Real top/proc parser, synthetic clock and backends; no live GPU access."""
import errno
import unittest
from unittest.mock import patch

from aihub_gpu_runner.backends import CleanupPending
from aihub_gpu_runner.core import RecoveryRequired
from tests import test_ollama_exit_races as races
from tests.test_ollama_physical_cleanup import GPU0, GPU1


class ChildSettlingTests(unittest.TestCase):
    def setUp(self):
        self.r = races.ExitRaceTests('runTest'); self.r.setUp()
        self.addCleanup(self.r.doCleanups)
        self.c = self.r.c
        self.on_sleep = lambda: None
        def sleep(duration):
            self.c.clock.sleep(duration); self.on_sleep()
        sleep_patch = patch('time.sleep', sleep)
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def spawn(self, pid='305', birth='50'):
        self.r.live[pid] = birth
        self.r.tops['ollama'].append(pid)

    def settle(self):
        self.on_sleep = lambda: self.r.exit('305') if self.c.clock.t >= .3 and '305' not in self.c.guard.exited else None

    def assert_fresh(self, proof):
        self.assertEqual(proof['stable_samples'], 3)
        observations = self.r.observations()
        self.assertTrue(any(o['process_snapshot_discarded'] for o in observations))
        self.assertEqual([o['process_snapshot_discarded'] for o in observations[-3:]], [False]*3)
        self.assertNotIn('305', self.c.guard.context['ollama_processes'])

    def test_successful_no_turnover_path_keeps_original_timing_and_unload_count(self):
        self.c.prepare(); self.c.backend.cleanup_models(['synthetic'], 2)
        proof = self.r.verify()
        self.assertEqual(proof['stable_samples'], 3)
        self.assertAlmostEqual(self.c.clock.t, .236442)
        self.assertEqual(len(self.r.observations()), 3)
        self.assertEqual(len([c for c in self.c.calls if c[0]=='POST']), 1)
        self.assertEqual(self.c.guard.settling, {})

    def test_transient_noncompute_child_exits_then_three_fresh_samples(self):
        self.c.prepare(); self.c.backend.cleanup_models(['synthetic'], 2)
        self.spawn(); self.settle()
        proof = self.r.verify(); self.assert_fresh(proof)
        self.assertGreaterEqual(self.c.clock.t, .6)
        self.assertEqual(self.c.guard.exited['305'], '50')
        self.assertEqual(len([c for c in self.c.calls if c[0]=='POST']), 1)

    def test_turnover_never_accepts_fewer_than_three_fresh_samples(self):
        from aihub_gpu_runner.recovery import digest
        self.c.p['samples'] = 2
        self.c.state['recovery_policy']['policy']['samples'] = 2
        self.c.state['recovery_policy']['policy_id'] = digest(self.c.p)
        self.c.guard = self.c.new_guard()
        self.c.guard.birth_reader = races.process_birth
        self.c.prepare(); self.spawn(); self.settle()
        self.assert_fresh(self.r.verify())

    def test_turnover_after_two_good_samples_discards_prior_stability(self):
        self.c.prepare(); record = self.c.guard.record
        def write(**fields):
            record(**fields)
            if len(self.r.observations()) == 2: self.spawn()
        self.c.guard.record = write
        self.on_sleep = lambda: self.r.exit('305') if self.c.clock.t >= .5 else None
        self.assert_fresh(self.r.verify())
        self.assertGreaterEqual(len(self.r.observations()), 7)

    def test_persistent_child_expires_at_unchanged_deadline_with_owner_held(self):
        self.c.prepare(); self.spawn()
        with self.assertRaisesRegex(CleanupPending, 'process_settling_pending'):
            self.c.guard.verify(.35)
        self.assertAlmostEqual(self.c.clock.t, .35)
        self.assertIn('gpu1', self.c.state['owners'])

    def test_without_completed_owned_requests_new_children_remain_fatal(self):
        job=self.c.store.job('owned-job')
        job.update(host_stage=True,host_exit_code=0,host_requests=[])
        self.c.store.put_job(job)
        self.c.prepare(); self.spawn()
        self.r.rejected('new_or_reused_process')

    def test_two_fresh_samples_after_exit_cannot_release_at_deadline(self):
        self.c.prepare(); self.spawn(); self.settle()
        with self.assertRaises(CleanupPending): self.c.guard.verify(.55)
        self.assertEqual([o['process_snapshot_discarded'] for o in self.r.observations()][-2:], [False, False])

    def test_new_child_compute_is_fatal_on_either_gpu_even_zero_memory(self):
        self.c.prepare(); self.spawn()
        for gpu in (GPU0, GPU1):
            with self.subTest(gpu=gpu):
                self.c.apps = lambda: gpu+',305,0\n'
                self.r.rejected('new_or_reused_process')
                detail = self.c.store.job('owned-job')['ollama_cleanup_failure']
                self.assertEqual((detail['phase'],detail['pid']), ('compute_apps','305'))

    def test_birth_change_of_new_child_is_fatal(self):
        self.c.prepare(); self.spawn()
        self.on_sleep = lambda: self.r.live.update({'305':'51'})
        self.r.rejected('new_or_reused_process')

    def test_reuse_of_retired_child_outside_container_remains_fatal(self):
        self.c.prepare(); self.spawn(); self.settle()
        record = self.c.guard.record
        def write(**fields):
            record(**fields)
            if '305' in self.c.guard.exited: self.r.live['305'] = '51'
        self.c.guard.record = write
        self.r.rejected('process_birth_changed')

    def test_missing_before_first_birth_and_permission_errors_are_not_settling(self):
        self.c.prepare(); self.spawn(); self.r.arm_exit('305')
        self.r.rejected('unverified_process_disappearance')
        self.r.top_hook = None; self.spawn()
        def denied(pid):
            if pid=='305': raise PermissionError(errno.EACCES, 'PRIVATE')
        self.r.stat_hook = denied
        with self.assertRaises(PermissionError): self.r.verify()
        self.assertIn('gpu1', self.c.state['owners'])

    def test_new_child_during_physical_sample_discards_sample(self):
        self.c.prepare(); fired = False
        def spawn_once():
            nonlocal fired
            if not fired: self.spawn(); fired = True
        self.r.gpu_hook = spawn_once; self.settle()
        self.assert_fresh(self.r.verify())

    def test_late_identified_unleased_compute_child_cannot_escape_rejection(self):
        self.c.prepare(); self.c.apps = lambda: GPU0+',305,0\n'
        self.r.gpu_hook = self.spawn
        self.r.rejected('new_or_reused_process')

    def test_continuous_new_lifetimes_are_capped_without_extending_deadline(self):
        self.c.prepare()
        def churn():
            pid=str(305+len(self.c.guard.settling)); self.spawn(pid,pid)
            for old in list(self.r.tops['ollama']):
                if old not in ('303','304',pid): self.r.exit(old)
        churn(); self.on_sleep = churn
        with self.assertRaisesRegex(RecoveryRequired, 'process_turnover_limit'):
            self.c.guard.verify(10)
        self.assertEqual(len(self.c.guard.settling), 64)
        self.assertLess(self.c.clock.t, 10)

    def test_worker_releases_once_only_after_turnover_and_fresh_proof(self):
        self.r.after_capture = self.spawn
        runner, lease = self.r.worker()
        self.on_sleep = lambda: self.r.exit('305')
        runner._work('owned-job', lease)
        self.assertEqual(self.c.store.job('owned-job')['state'], 'success')
        lease.release.assert_called_once()
        self.assertEqual(len([c for c in self.c.calls if c[0]=='POST']), 2)

    def test_worker_uncertainty_keeps_owner_and_does_not_repeat_unload(self):
        self.r.after_capture = self.spawn
        runner, lease = self.r.worker()
        runner._work('owned-job', lease)
        job = self.c.store.job('owned-job')
        self.assertEqual(job['state'], 'cleanup_pending')
        self.assertEqual(job['error_code'], 'ollama_cleanup_process_settling_pending')
        lease.release.assert_not_called()
        self.assertIn('gpu1', self.c.state['owners'])
        self.assertEqual(len([c for c in self.c.calls if c[0]=='POST']), 2)

    def test_storage_failure_during_turnover_cannot_release(self):
        self.c.prepare(); self.spawn()
        def fail(**fields):
            if 'ollama_cleanup_observation' in fields: raise OSError(errno.ENOSPC, 'PRIVATE')
        self.c.guard.record = fail
        with self.assertRaises(OSError): self.r.verify()
        self.assertIn('gpu1', self.c.state['owners'])


if __name__ == '__main__': unittest.main()
