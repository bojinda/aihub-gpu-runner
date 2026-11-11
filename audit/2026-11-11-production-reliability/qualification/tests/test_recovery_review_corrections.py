"""Independent-review regression cases. All controls/probes use offline fixtures."""
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

from aihub_gpu_runner.backends import ComfyBackend, Execution, Target, Uncertain
from aihub_gpu_runner.core import Admission, AtomicStore, NativeLock, RecoveryRequired, Runner
from aihub_gpu_runner.recovery import RecoveryManager, RecoveryObserver, authorize_policy
from tests import test_automatic_recovery as f
from tests.test_r1 import FakeBackend


class ObserverInitializationTests(unittest.TestCase):
    setUp = f.ObserverTests.setUp
    run_command = f.ObserverTests.run_command

    def test_real_observer_retries_initializing_system_stats_until_stable(self):
        transport = self.backend.transport
        original, stats = transport.json, [0]
        def initializing(method, route, *args, **kwargs):
            if route == '/system_stats':
                stats[0] += 1
                if stats[0] <= 2:
                    return {}  # Live HTTP endpoint, readiness not initialized yet.
            return original(method, route, *args, **kwargs)
        with patch.object(transport, 'json', side_effect=initializing):
            proof = self.observer.ready(('gpu0', 'gpu1'), time.monotonic() + 1)
        self.assertTrue(proof['backend_ready'])
        self.assertEqual(stats[0], 4)

    def test_prestart_cleanup_omits_only_the_stopped_wallpaper_api(self):
        self.metadata['wallpaper']['State'].update(Running=False, Pid=0)
        with patch.object(self.backend.transport, 'json', side_effect=AssertionError('stopped API queried')):
            proof = self.observer.ready(('gpu0', 'gpu1'), time.monotonic() + 1, skip_wallpaper=True)
        self.assertTrue(proof['cleanup_verified'])
        self.assertEqual(proof['queues'], {})
        self.assertEqual(proof['observations']['service_memory_mb']['music']['gpu0'], 3)


class ReviewCorrectionTests(unittest.TestCase):
    setUp = f.RecoveryTests.setUp
    new_manager = f.RecoveryTests.new_manager
    job = f.RecoveryTests.job
    tick = f.RecoveryTests.tick
    incident = f.RecoveryTests.incident
    assert_held = f.RecoveryTests.assert_held

    def stopped_reboot(self, owner=False):
        if owner:
            self.job()
        self.boot[0] = f.BOOT2
        self.observer.current.update(running=False, pid=0)

    def assert_fenced(self):
        self.assertEqual(self.gate.snapshot()['verified_boot'], f.BOOT1)
        self.assertIsNone(self.gate.try_acquire('blocked', 'h', ('gpu1',), 'ollama'))

    def test_verified_reboot_starts_stopped_wallpaper_without_retained_owner(self):
        self.stopped_reboot()
        self.tick()
        state = self.gate.snapshot()
        self.assertEqual(self.observer.actions, ['start'])
        self.assertEqual(state['verified_boot'], f.BOOT2)
        self.assertEqual(state['boot_startups'][f.BOOT2]['phase'], 'ready')
        self.assertEqual(state.get('recovery_budget', []), [])
        self.assertEqual(state.get('automatic_recovery', {}), {})
        for _ in range(3):
            self.new_manager().tick(force=True)
        self.assertEqual(self.observer.actions, ['start'])

    def test_verified_reboot_starts_stopped_wallpaper_before_releasing_both_owners(self):
        self.stopped_reboot(owner=True)
        self.observer.callback = self.assert_held
        # The final owner-free readiness pass follows paired release.
        original = self.observer.ready
        def ready(resources, deadline, **kwargs):
            if self.gate.snapshot()['owners']:
                self.assert_held()
            self.observer.callback = None
            return original(resources, deadline, **kwargs)
        with patch.object(self.observer, 'ready', side_effect=ready):
            self.tick()
        self.assertEqual(self.observer.actions, ['start'])
        self.assertEqual(self.store.job('owned')['state'], 'failed')
        self.assertEqual(self.store.job('owned')['error_code'], 'guest_reboot_interrupted')
        self.assertEqual(self.gate.snapshot()['owners'], {})
        self.assertEqual(self.gate.snapshot()['verified_boot'], f.BOOT2)
        self.assertEqual(len(self.gate.snapshot()['recoveries']), 1)
        self.assertEqual(self.backend.reconciliations, 0)

    def test_same_boot_owner_free_restart_does_not_start_stopped_container(self):
        self.observer.current.update(running=False, pid=0)
        before = self.store.path('ownership.json').read_bytes()
        self.manager.startup()
        self.new_manager().startup()
        self.assertEqual(self.observer.actions, [])
        self.assertEqual(self.store.path('ownership.json').read_bytes(), before)

    def test_boot_startup_retries_transient_initialization_then_verifies_readiness(self):
        self.stopped_reboot(owner=True)
        self.observer.p, self.observer.poll_interval = self.p, .001
        queues, failures = self.observer.queues, [0]
        def warming(resources, deadline, **kwargs):
            if not kwargs.get('skip_wallpaper') and failures[0] < 2:
                failures[0] += 1
                raise Uncertain('synthetic_backend_initializing')
            return queues(resources, deadline, **kwargs)
        with patch.object(self.observer, 'queues', side_effect=warming), \
                patch.object(self.observer, 'ready', side_effect=lambda *a, **k: RecoveryObserver.ready(self.observer, *a, **k)):
            self.tick()
        self.assertEqual(failures[0], 2)
        self.assertEqual(self.observer.actions, ['start'])
        self.assertEqual(self.gate.snapshot()['verified_boot'], f.BOOT2)
        self.assertEqual(self.gate.snapshot()['owners'], {})

    def test_boot_health_or_identity_failure_preserves_owners_and_has_no_restart_loop(self):
        self.stopped_reboot(owner=True)
        self.observer.fail = 'health'
        self.tick()
        self.assert_held()
        self.assert_fenced()
        self.assertEqual(self.observer.actions, [])
        with patch.object(self.store, 'write', wraps=self.store.write) as writes:
            for _ in range(3):
                self.new_manager().tick(force=True)
            self.assertEqual(writes.call_count, 0)

    def test_boot_exact_identity_failure_never_starts_changed_container(self):
        self.stopped_reboot(owner=True)
        self.observer.current['id'] = 'e' * 64
        self.tick()
        self.assertEqual(self.observer.actions, [])
        self.assert_held()
        self.assert_fenced()
        self.assertEqual(self.gate.snapshot()['boot_recovery_error'], 'boot_wallpaper_identity_changed')

    def test_boot_readiness_race_preserves_retained_owner(self):
        self.stopped_reboot(owner=True)
        ready, calls = self.observer.ready, [0]
        def raced(resources, deadline, **kwargs):
            proof = ready(resources, deadline, **kwargs)
            if not kwargs.get('skip_wallpaper'):
                calls[0] += 1
                if calls[0] == 2:  # After startup readiness, before owner handoff.
                    self.observer.current['started_at'] = 'foreign-restart'
            return proof
        with patch.object(self.observer, 'ready', side_effect=raced):
            self.tick()
        self.assert_held()
        self.assert_fenced()
        self.assertEqual(self.incident()['error'], 'boot_wallpaper_generation_changed_before_handoff')
        self.assertEqual(self.observer.actions, ['start'])

    def test_boot_owner_free_readiness_race_keeps_admission_fenced(self):
        self.stopped_reboot()
        ready, calls = self.observer.ready, [0]
        def raced(resources, deadline, **kwargs):
            proof = ready(resources, deadline, **kwargs)
            if not kwargs.get('skip_wallpaper'):
                calls[0] += 1
                if calls[0] == 2:
                    self.observer.current.update(running=False, pid=0)
            return proof
        with patch.object(self.observer, 'ready', side_effect=raced):
            self.tick()
        self.assert_fenced()
        self.assertEqual(self.gate.snapshot()['boot_recovery_status'], 'operator_required')
        self.new_manager().tick(force=True)
        self.assertEqual(self.observer.actions, ['start'])

    def test_boot_start_effect_survives_runner_interruption_without_duplicate_start(self):
        self.stopped_reboot(owner=True)
        self.observer.boundary = 'after_start'
        with self.assertRaises(f.Crash):
            self.tick()
        self.assert_held()
        self.assert_fenced()
        self.new_manager().tick(force=True)
        self.assertEqual(self.observer.actions, ['start'])
        self.assertEqual(self.gate.snapshot()['owners'], {})
        self.assertEqual(self.gate.snapshot()['verified_boot'], f.BOOT2)

    def test_boot_intent_without_effect_fails_closed_without_reissuing_start(self):
        self.stopped_reboot(owner=True)
        self.observer.boundary = 'before_start'
        with self.assertRaises(f.Crash):
            self.tick()
        self.new_manager().tick(force=True)
        self.assertEqual(self.observer.actions, [])
        self.assert_held()
        self.assert_fenced()
        self.assertEqual(self.gate.snapshot()['boot_startups'][f.BOOT2]['phase'], 'operator_required')

    def test_boot_does_not_restart_again_after_ready_record_survives_crash(self):
        self.stopped_reboot()
        real = self.store.write
        def write(name, value):
            real(name, value)
            if name == 'ownership.json' and value.get('boot_startups', {}).get(f.BOOT2, {}).get('phase') == 'ready':
                raise f.Crash('after boot ready, before admission')
        with patch.object(self.store, 'write', side_effect=write):
            with self.assertRaises(f.Crash):
                self.tick()
        self.observer.current.update(running=False, pid=0)
        self.new_manager().tick(force=True)
        self.assertEqual(self.observer.actions, ['start'])
        self.assert_fenced()

    def test_active_native_supervision_precedes_incident_and_any_recovery_write(self):
        self.job()
        with NativeLock(self.gate.lock_paths['gpu0'], create=False).held(0):
            before = self.store.path('ownership.json').read_bytes()
            with patch.object(self.store, 'write', wraps=self.store.write) as writes, \
                    patch.object(self.observer, 'container', side_effect=AssertionError('healthy worker probed')):
                for _ in range(6):
                    self.new_manager().tick(force=True)
                self.assertEqual(writes.call_count, 0)
            self.assertEqual(self.store.path('ownership.json').read_bytes(), before)
            self.assertNotIn('automatic_recovery', self.gate.snapshot())
            self.assertNotIn('recovery_status', self.store.job('owned'))

    def test_normal_worker_has_zero_tick_writes_and_ordinary_cleanup_still_succeeds(self):
        entered, finish = threading.Event(), threading.Event()
        class HealthyBackend(ComfyBackend):
            def execute(inner, *args):
                entered.set()
                if not finish.wait(3):
                    raise AssertionError('healthy worker fixture not released')
                return Execution({'done': True}, {'prompt_id': 'prompt'})
        backend = HealthyBackend(self.target, f.Transport(), lambda: 3, conditioning_probe=lambda: 2,
                                 poll_interval=.001)
        manager = RecoveryManager(self.gate, self.targets, {'wallpaper': backend}, self.observer)
        runner = Runner(self.gate, self.targets, {'wallpaper': backend}, recovery=manager, poll_interval=.001)
        self.addCleanup(runner.close)
        self.addCleanup(finish.set)
        runner.submit('healthy', 'wallpaper', 'workflow', f.WORKFLOW)
        self.assertTrue(entered.wait(1))
        with patch.object(self.store, 'write', wraps=self.store.write) as writes:
            for _ in range(6):
                manager.tick(force=True)
            self.assertEqual(writes.call_count, 0)
        self.assertNotIn('automatic_recovery', self.gate.snapshot())
        finish.set()
        runner.future('healthy').result(2)
        self.assertEqual(runner.status('healthy')['state'], 'success')
        self.assertEqual(self.gate.snapshot()['owners'], {})
        self.assertEqual(self.observer.actions, [])

    def test_unchanged_operator_required_outcomes_are_written_only_once(self):
        self.job()
        self.observer.fail = 'queue'
        self.tick()
        with patch.object(self.store, 'write', wraps=self.store.write) as writes:
            for _ in range(6):
                self.new_manager().tick(force=True)
            self.assertEqual(writes.call_count, 0)
        self.assert_held()

    def test_completed_worker_released_after_owner_snapshot_is_not_marked_for_recovery(self):
        lease = self.job(state='success')
        self.store.write('results/owned.json', {'value': 'preserved'})
        lease.release({'completion_verified': True, 'cleanup_verified': True})
        before = self.store.job('owned')
        with patch.object(self.store, 'write', wraps=self.store.write) as writes:
            self.manager.recover(lease.owner, ('gpu0', 'gpu1'))
            self.assertEqual(writes.call_count, 0)
        self.assertEqual(self.store.job('owned'), before)
        self.assertEqual(self.store.read('results/owned.json'), {'value': 'preserved'})
        self.assertNotIn('automatic_recovery', self.gate.snapshot())

    def test_unchanged_missing_boot_failure_does_not_rewrite_job_every_tick(self):
        self.job()
        state = self.gate.snapshot()
        for owner in state['owners'].values():
            owner['boot_id'] = None
        self.store.write('ownership.json', state)
        self.tick()
        with patch.object(self.store, 'write', wraps=self.store.write) as writes:
            for _ in range(6):
                self.new_manager().tick(force=True)
            self.assertEqual(writes.call_count, 0)
        self.assert_held()

    def test_recovery_worker_is_joined_before_supervisor_lock_is_released(self):
        manager = self.new_manager()
        runner = Runner(self.gate, self.targets, self.backends, recovery=manager, poll_interval=.001)
        self.addCleanup(runner.close)
        entered, finish, closed = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(finish.set)
        ready = self.observer.ready
        def slow(*args, **kwargs):
            entered.set()
            if not finish.wait(3):
                raise AssertionError('shutdown recovery fixture not released')
            return ready(*args, **kwargs)
        with patch.object(self.observer, 'ready', side_effect=slow):
            self.job()
            manager.notify()
            self.assertTrue(entered.wait(1))
            closer = threading.Thread(target=lambda: (runner.close(), closed.set()))
            closer.start()
            try:
                self.assertFalse(closed.wait(.05))
                other = NativeLock(self.store.path('supervisor.lock'))
                self.assertFalse(other.acquire(0))
                finish.set()
                closer.join(2)
                self.assertTrue(closed.is_set())
                self.assertFalse(manager.thread.is_alive())
                self.assertTrue(other.acquire(0))
                other.close()
            finally:
                finish.set()
                closer.join(3)

    def test_missing_and_invalid_deadlines_stop_once_without_waiting_or_controls(self):
        self.job()
        for value in (None, '', '1700000000', True, False, 0, -1, [], {}, 10 ** 1000):
            with self.subTest(deadline=value):
                state = self.gate.snapshot()
                state.pop('automatic_recovery', None)
                self.store.write('ownership.json', state)
                job = self.store.job('owned')
                if value is None:
                    job.pop('execution_deadline', None)
                else:
                    job['execution_deadline'] = value
                self.store.put_job(job)
                self.tick()
                self.assertEqual(self.incident()['phase'], 'operator_required')
                self.assertEqual(self.store.job('owned')['recovery_error'], 'execution_deadline_missing_or_invalid')
                with patch.object(self.store, 'write', wraps=self.store.write) as writes:
                    self.new_manager().tick(force=True)
                    self.assertEqual(writes.call_count, 0)
                self.assert_held()
        self.assertEqual(self.observer.actions, [])
        self.assertEqual(self.backend.reconciliations, 0)
        job = self.store.job('owned')
        for value in (float('nan'), float('inf')):
            job['execution_deadline'] = value
            with self.assertRaisesRegex(RecoveryRequired, 'execution_deadline_missing_or_invalid'):
                self.manager.wallpaper({}, {}, ('gpu0', 'gpu1'), job, time.monotonic() + 1)

    def test_slow_gpu0_recovery_does_not_block_gpu1_admission_or_duplicate_recovery(self):
        wallpaper = Target('wallpaper', 'comfy', ('gpu0',), 'http://offline.invalid',
                           workflow=f.WORKFLOW, output_node='2', idle_memory_mb=20)
        ollama = Target('ollama', 'ollama', ('gpu1',), 'http://offline.invalid', models=('synthetic',))
        self.targets = {'wallpaper': wallpaper, 'ollama': ollama}
        self.backends = {'wallpaper': self.backend, 'ollama': FakeBackend()}
        manager = self.new_manager()
        runner = Runner(self.gate, self.targets, self.backends, recovery=manager, poll_interval=.001)
        self.addCleanup(runner.close)
        entered, finish = threading.Event(), threading.Event()
        self.addCleanup(finish.set)
        ready = self.observer.ready
        def slow(*args, **kwargs):
            entered.set()
            if not finish.wait(3):
                raise AssertionError('slow recovery fixture not released')
            return ready(*args, **kwargs)
        with patch.object(self.observer, 'ready', side_effect=slow):
            self.job(resources=('gpu0',))
            manager.notify()
            self.assertTrue(entered.wait(1))
            with self.assertRaisesRegex(RuntimeError, 'already_started'):
                manager.start()
            # The same incident cannot acquire the recovery lock in another invocation.
            self.new_manager().tick(force=True)
            runner.submit('independent', 'ollama', 'generate', {'model': 'synthetic', 'prompt': 'offline'})
            runner.future('independent').result(1)
            self.assertFalse(finish.is_set())
            self.assertEqual(runner.status('independent')['state'], 'success')
            self.assertEqual(set(self.gate.snapshot()['owners']), {'gpu0'})
            self.assertEqual(self.observer.actions, ['TERM', 'start'])
            finish.set()
        manager.close()
        self.assertEqual(self.gate.snapshot()['owners'], {})
        self.assertEqual(len(self.gate.snapshot()['recoveries']), 1)

    def test_boot_write_boundaries_never_duplicate_start_or_owner_release(self):
        for cut_at in range(1, 15):
            with self.subTest(cut_at=cut_at):
                root = Path(self.tmp.name) / ('boot-cut-' + str(cut_at))
                store = AtomicStore(root / 'state')
                boot = [f.BOOT1]
                gate = Admission(store, {r: root / (r + '.lock') for r in ('gpu0', 'gpu1')},
                                 self.gate.scope, boot_reader=lambda: boot[0])
                gate.bootstrap({'approval_ref': 'OFFLINE', 'bootstrap_id': gate.snapshot()['bootstrap_id'],
                                 'backend_quiescent': True, 'cleanup_verified': True})
                observer = f.Observer()
                authorize_policy(gate, self.targets, self.backends, self.p, 'OFFLINE', observer)
                lease = gate.try_acquire('cut', 'hash', ('gpu0', 'gpu1'), 'wallpaper')
                store.put_job({'request_id': 'cut', 'request_hash': 'hash', 'target': 'wallpaper',
                    'state': 'running', 'resources': ['gpu0', 'gpu1'], 'boot_id': f.BOOT1, 'error_code': None})
                lease.close()
                boot[0] = f.BOOT2
                observer.current.update(running=False, pid=0)
                manager = RecoveryManager(gate, self.targets, self.backends, observer)
                real, count = store.write, [0]
                def write(name, value):
                    real(name, value)
                    count[0] += 1
                    if count[0] == cut_at:
                        raise f.Crash('boot persistence boundary')
                with patch.object(store, 'write', side_effect=write):
                    try:
                        manager.tick(force=True)
                    except f.Crash:
                        pass
                resumed = RecoveryManager(gate, self.targets, self.backends, observer)
                resumed.tick(force=True)
                resumed.tick(force=True)
                self.assertLessEqual(observer.actions.count('start'), 1)
                self.assertNotIn('TERM', observer.actions)
                self.assertNotIn('KILL', observer.actions)
                state = gate.snapshot()
                self.assertLessEqual(len(state.get('recoveries', [])), 1)
                self.assertEqual(store.job('cut')['state'], 'failed')
                if state['owners']:
                    self.assertEqual(set(state['owners']), {'gpu0', 'gpu1'})
                    self.assertEqual(state['verified_boot'], f.BOOT1)
                else:
                    self.assertEqual(state['verified_boot'], f.BOOT2)


if __name__ == '__main__':
    unittest.main()
