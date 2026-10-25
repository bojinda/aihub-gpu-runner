"""On-demand lifecycle: synthetic Docker/HTTP/GPU, real journals and native locks."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from aihub_gpu_runner.api import Application
from aihub_gpu_runner.backends import ComfyBackend, Target, Uncertain
from aihub_gpu_runner.core import Admission, AtomicStore, Conflict, NativeLock, RecoveryRequired, Runner
from aihub_gpu_runner.recovery import RecoveryManager, authorize_policy, digest
from aihub_gpu_runner.recovery_summary import summarize_recovery
from tests import test_automatic_recovery as f
from tests.test_wallpaper_conditioning import WORKFLOW, Transport, GPU0, GPU1


class Observer(f.Observer):
    def __init__(self):
        super().__init__()
        self.current.update(running=False, pid=0, started_at='cold')
        self.generations, self.on_action, self.stopped_calls = 0, None, 0
    def start(self, identity, deadline):
        self.boundary_at('before_start')
        if self.on_action:
            self.on_action('start')
        self.actions.append('start')
        if self.fail == 'start':
            raise Uncertain('start_response_lost_without_effect')
        self.generations += 1
        self.current.update(running=True, pid=101, started_at='run-' + str(self.generations))
        self.boundary_at('after_start')
    def signal(self, identity, signal, deadline):
        if self.on_action:
            self.on_action(signal)
        super().signal(identity, signal, deadline)
    def placement(self, identity, resources, deadline):
        if self.fail == 'placement':
            raise RecoveryRequired('wallpaper_physical_placement_unverified')
        return {'physical_gpu_uuids': {'gpu0': GPU0, 'gpu1': GPU1}}
    def ready(self, resources, deadline, **kwargs):
        if self.fail == 'startup':
            # A failed bounded initialization does not consume execution time.
            raise Uncertain('startup_readiness_deadline')
        return super().ready(resources, deadline)
    def stopped_ready(self, resources, deadline, processes=None):
        assert not self.current['running'] and self.current['pid'] == 0
        self.stopped_calls += 1
        self.gone(processes or {})
        self.queues(resources, deadline, skip_wallpaper=True)
        sample = self.gpu_sample(resources, deadline)
        return {'idle_ready': True, 'backend_stopped': True, 'cleanup_verified': True,
                'stopped_container': dict(self.current), 'music_idle_context': True,
                'original_execution_gone': True, 'observations': sample}


class OwnedTransport(Transport):
    def __init__(self, observer):
        super().__init__()
        self.observer, self.on_submit = observer, None
    def json(self, method, route, payload=None, timeout=5):
        assert self.observer.current['running'], 'HTTP against intentionally stopped wallpaper'
        value = super().json(method, route, payload, timeout)
        if route == '/prompt' and self.on_submit:
            self.on_submit()
        return value


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='offline-on-demand-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = AtomicStore(self.root / 'state')
        self.boot = [f.BOOT1]
        self.gate = Admission(self.store, {r: self.root / (r + '.lock') for r in ('gpu0', 'gpu1')},
                              'synthetic-on-demand', boot_reader=lambda: self.boot[0])
        self.gate.bootstrap({'approval_ref': 'OFFLINE', 'bootstrap_id': self.gate.snapshot()['bootstrap_id'],
                             'backend_quiescent': True, 'cleanup_verified': True})
        self.target = Target('wallpaper', 'comfy', ('gpu0', 'gpu1'), 'http://offline.invalid',
            workflow=WORKFLOW, output_node='2', idle_memory_mb=20, conditioning_idle_memory_mb=10,
            lifecycle='on_demand', startup_timeout=2, shutdown_timeout=2,
            execution_timeout=.05, acquisition_timeout=.08)
        self.observer = Observer()
        self.transport = OwnedTransport(self.observer)
        self.backend = ComfyBackend(self.target, self.transport, lambda: 1,
                                    conditioning_probe=lambda: 1, poll_interval=.001)
        self.targets, self.backends = {'wallpaper': self.target}, {'wallpaper': self.backend}
        self.p = f.policy(self.gate)
        self.p.update(boot_startup='stopped_idle', stopped_idle_memory_mb={'gpu0': 8, 'gpu1': 4})
        authorize_policy(self.gate, self.targets, self.backends, self.p, 'OFFLINE', self.observer)
        self.manager = self.new_manager()
        self.runners = []
        self.addCleanup(patch.stopall)
        patch('subprocess.run', side_effect=AssertionError('real control forbidden')).start()
        patch('subprocess.check_output', side_effect=AssertionError('real GPU forbidden')).start()
    def tearDown(self):
        for runner in self.runners:
            runner.close()
    def new_manager(self):
        return RecoveryManager(self.gate, self.targets, self.backends, self.observer)
    def runner(self):
        # Tests explicitly drive the same supervised recovery path at boundaries.
        with patch.object(self.manager, 'start'):
            runner = Runner(self.gate, self.targets, self.backends, recovery=self.manager, poll_interval=.001)
        self.runners.append(runner)
        return runner
    def submit(self, runner, name='job'):
        runner.submit(name, 'wallpaper', 'workflow', WORKFLOW)
        runner.future(name).result(4)
        return runner.status(name)
    def tick(self, count=1):
        time.sleep(.005)  # Let the inference worker relinquish its native descriptors.
        for _ in range(count):
            self.new_manager().tick(force=True)
    def held(self):
        state = self.gate.snapshot()
        self.assertEqual(set(state['owners']), {'gpu0', 'gpu1'})
        self.assertEqual(state['owners']['gpu0']['lease_id'], state['owners']['gpu1']['lease_id'])
    def summary(self):
        return summarize_recovery(self.gate.snapshot())
    def expire(self, name='job'):
        job = self.store.job(name)
        record = dict(job.get('wallpaper_lifecycle', {}))
        if record:
            record['startup_deadline'] = time.time() - 5
            self.manager.update_job(name, wallpaper_lifecycle=record, execution_deadline=time.time() - 5)

    def test_two_jobs_start_save_stop_release_and_do_not_count_or_spend_recovery(self):
        runner = self.runner()
        events = []
        def control(action):
            self.held()
            if action == 'TERM':
                name = 'one' if self.transport.submissions == 1 else 'two'
                self.assertTrue(self.store.path('results/' + name + '.bin').is_file())
                self.assertEqual(self.store.job(name)['state'], 'cleanup_pending')
                with self.assertRaises(Conflict):
                    runner.result(name)
                events.append('saved')
            events.append(action)
        self.observer.on_action = control
        for name in ('one', 'two'):
            self.assertEqual(self.submit(runner, name)['state'], 'success')
            self.assertFalse(self.observer.current['running'])
            self.assertEqual(self.gate.snapshot()['owners'], {})
            self.assertTrue(runner.artifact(name)[0].startswith(b'\x89PNG'))
        self.tick(3)
        self.assertEqual(events, ['start', 'saved', 'TERM', 'start', 'saved', 'TERM'])
        self.assertEqual(self.transport.submissions, 2)
        self.assertEqual(self.transport.frees, 0)
        state = self.gate.snapshot()
        self.assertEqual(state.get('automatic_recovery', {}), {})
        self.assertEqual(state.get('recoveries', []), [])
        self.assertEqual(state.get('recovery_budget', []), [])
        for name in ('one', 'two'):
            self.assertNotIn('recovery_status', self.store.job(name))
            self.assertNotIn('recovery_incident', self.store.job(name))
        self.assertEqual(len(state['wallpaper_lifecycle_receipts']), 2)
        self.assertEqual(self.summary()['incidents_7d'], 0)
        self.assertFalse(self.summary()['operator_required'])

    def test_no_start_or_partial_reservation_while_either_native_gpu_is_busy(self):
        for resource in ('gpu0', 'gpu1'):
            with self.subTest(resource=resource):
                lock = NativeLock(self.gate.lock_paths[resource], create=False)
                self.assertTrue(lock.acquire(0))
                runner = self.runner()
                try:
                    self.assertEqual(self.submit(runner, resource)['state'], 'failed')
                    self.assertEqual(self.gate.snapshot()['owners'], {})
                    self.assertEqual(self.observer.actions, [])
                finally:
                    lock.close()
                    runner.close()

    def test_startup_does_not_consume_execution_deadline(self):
        original = self.observer.ready
        def slow(*args, **kwargs):
            self.assertNotIn('execution_deadline', self.store.job('job'))
            time.sleep(.08)  # Longer than the entire execution budget.
            return original(*args, **kwargs)
        with patch.object(self.observer, 'ready', side_effect=slow):
            self.assertEqual(self.submit(self.runner())['state'], 'success')
        self.assertEqual(self.transport.submissions, 1)

    def test_startup_failure_has_no_submission_and_recovers_stopped_only_after_deadline(self):
        self.observer.fail = 'startup'
        self.submit(self.runner())
        self.tick()
        self.assertEqual(self.observer.actions, ['start'])
        self.held()
        self.assertNotIn('execution_deadline', self.store.job('job'))
        self.expire()
        self.observer.fail = None
        self.tick(2)
        self.assertEqual(self.observer.actions, ['start', 'TERM'])
        self.assertEqual(self.store.job('job')['state'], 'failed')
        self.assertEqual(self.transport.submissions, 0)
        self.assertFalse(self.observer.current['running'])

    def test_unknown_history_connection_error_waits_for_execution_deadline_then_fails_stopped(self):
        self.transport.history = 'uncertain'
        self.target.execution_timeout = .3
        # Fail the worker early, while the durable execution deadline is future.
        original = self.backend.execute
        def uncertain(operation, payload, deadline, progress):
            self.transport.json('POST', '/prompt', {'prompt': payload})
            progress('running', {'prompt_id': 'synthetic-owned-prompt'})
            raise Uncertain('connection_error_alone')
        with patch.object(self.backend, 'execute', side_effect=uncertain):
            self.submit(self.runner())
        self.tick(2)
        self.assertEqual(self.observer.actions, ['start'])
        self.held()
        self.expire()
        self.tick(2)
        self.assertEqual(self.observer.actions, ['start', 'TERM'])
        self.assertEqual(self.store.job('job')['state'], 'failed')
        self.assertEqual(self.transport.submissions, 1)
        self.assertEqual(len(self.gate.snapshot()['recoveries']), 1)
        self.assertEqual(len(self.gate.snapshot()['recovery_budget']), 1)

    def test_owned_terminal_error_finishes_failed_stopped_without_replay(self):
        self.transport.history = 'error'
        self.submit(self.runner())
        self.tick(2)
        self.assertEqual(self.store.job('job')['state'], 'failed')
        self.assertEqual(self.observer.actions, ['start', 'TERM'])
        self.assertEqual(self.transport.submissions, 1)
        self.assertEqual(self.summary()['incidents_7d'], 1)

    def test_artifact_write_failure_keeps_backend_and_both_owners_until_saved(self):
        original = self.store.write_bytes
        def disk_full(name, data):
            if name.startswith('results/'):
                raise OSError('synthetic disk full')
            return original(name, data)
        with patch.object(self.store, 'write_bytes', side_effect=disk_full):
            self.submit(self.runner())
            self.tick()
            self.held()
            self.assertEqual(self.observer.actions, ['start'])
        self.tick(2)
        self.assertEqual(self.store.job('job')['state'], 'success')
        self.assertEqual(self.observer.actions, ['start', 'TERM'])
        self.assertEqual(self.transport.submissions, 1)

    def test_invalid_retrieved_artifact_is_not_destroyed_by_a_stop(self):
        self.transport.invalid_artifact = True
        self.submit(self.runner())
        self.tick(2)
        self.held()
        self.assertEqual(self.observer.actions, ['start'])
        self.assertEqual(self.store.job('job')['recovery_status'], 'operator_required')

    def test_repeated_artifact_write_failure_stops_automatic_attempts_without_stopping_backend(self):
        original = self.store.write_bytes
        def disk_full(name, data):
            if name.startswith('results/'):
                raise OSError('synthetic disk full')
            return original(name, data)
        with patch.object(self.store, 'write_bytes', side_effect=disk_full):
            self.submit(self.runner())
            self.tick(4)
        self.held()
        self.assertEqual(self.store.job('job')['recovery_status'], 'operator_required')
        self.assertEqual(self.observer.actions, ['start'])
        self.assertEqual(self.gate.snapshot().get('recovery_budget', []), [])

    def test_persistent_recovery_rate_limit_does_not_block_normal_lifecycle(self):
        runner = self.runner()
        self.transport.history = 'missing'
        self.submit(runner, 'failed-one')
        self.tick(2)
        self.assertEqual(len(self.gate.snapshot()['recovery_budget']), 1)
        self.transport.history = 'success'
        self.assertEqual(self.submit(runner, 'healthy')['state'], 'success')
        self.assertEqual(len(self.gate.snapshot()['recovery_budget']), 1)
        self.assertEqual(self.summary()['incidents_7d'], 1)
        self.transport.history = 'missing'
        self.submit(runner, 'failed-two')
        self.tick(3)
        self.held()
        self.assertEqual(self.store.job('failed-two')['recovery_error'], 'persistent_recovery_rate_limit')
        self.assertEqual(self.observer.actions, ['start', 'TERM', 'start', 'TERM', 'start'])

    def test_same_boot_runner_restart_preserves_surviving_backend_until_its_deadline(self):
        self.transport.history = 'uncertain'
        self.target.execution_timeout = .05
        runner = self.runner()
        self.submit(runner)
        # Extend the persisted valid deadline to emulate a surviving supervised request.
        self.manager.update_job('job', execution_deadline=time.time() + 10)
        runner.close()
        restarted = self.runner()
        self.held()
        self.assertTrue(self.observer.current['running'])
        self.assertEqual(self.observer.actions, ['start'])
        self.assertEqual(self.transport.submissions, 1)
        self.assertNotEqual(restarted.status('job')['state'], 'success')

    def test_same_boot_independent_host_stage_is_untouched(self):
        lease = self.gate.try_acquire('host', 'host-hash', ('gpu0',), 'host-whisperx')
        self.store.put_job({'request_id': 'host', 'request_hash': 'host-hash', 'target': 'host-whisperx',
            'resources': ['gpu0'], 'state': 'running', 'host_stage': True, 'boot_id': f.BOOT1})
        lease.close()
        before = self.store.job('host')
        self.tick(3)
        self.assertEqual(before, self.store.job('host'))
        self.assertEqual(self.observer.actions, [])
        self.assertEqual(set(self.gate.snapshot()['owners']), {'gpu0'})
        self.assertEqual(self.summary()['incidents_7d'], 0)

    def test_on_demand_reboot_cannot_bypass_dual_meeting_restoration(self):
        from aihub_gpu_runner.backends import CleanupPending
        from aihub_gpu_runner.dual_gpu import switch_binding
        from tests.test_dual_gpu import fixture
        root = self.root / 'dual'
        root.mkdir()
        settings = fixture(root)[2]
        settings['mode_switch']['gpu_uuids'] = self.p['gpu_uuids']
        target = Target(name='meeting-dual', **dict(settings, resources=('gpu0', 'gpu1'), models=('synthetic',)))
        self.targets[target.name] = target
        lease = self.gate.try_acquire('meeting', 'meeting-hash', ('gpu0', 'gpu1'), target.name)
        self.store.put_job({'request_id': 'meeting', 'request_hash': 'meeting-hash', 'target': target.name,
            'resources': ['gpu0', 'gpu1'], 'state': 'running', 'host_stage': True, 'dual_gpu_session': True,
            'switch_binding': switch_binding(target.mode_switch), 'boot_id': f.BOOT1})
        lease.close()
        self.boot[0] = f.BOOT2
        with patch('aihub_gpu_runner.dual_gpu.ComposeOllamaSwitch.verify',
                   side_effect=CleanupPending('placement_unverified')):
            self.tick(2)
        self.held()
        self.assertEqual(self.observer.actions, [])
        self.assertEqual(self.gate.snapshot()['verified_boot'], f.BOOT1)

    def test_on_demand_requires_policy_authorization_before_runner_can_serve(self):
        store = AtomicStore(self.root / 'unauthorized-state')
        gate = Admission(store, {r: self.root / ('unapproved-' + r + '.lock') for r in ('gpu0', 'gpu1')},
                         'unapproved', boot_reader=lambda: f.BOOT1)
        with self.assertRaisesRegex(RecoveryRequired, 'authorized_scope_bound_policy'):
            Runner(gate, self.targets, self.backends)
        self.assertEqual(self.observer.actions, [])

    def test_placement_or_gpu_cleanup_failure_retains_both_reservations(self):
        for failure in ('placement', 'gpu0', 'gpu1', 'cleanup'):
            with self.subTest(failure=failure):
                case = LifecycleTests('run')
                case.setUp()
                try:
                    if failure == 'placement':
                        case.observer.fail = failure
                    else:
                        case.observer.on_action = lambda action: setattr(case.observer, 'fail', failure) if action == 'TERM' else None
                    case.submit(case.runner())
                    case.tick(2)
                    case.held()
                    if failure == 'placement':
                        self.assertEqual(case.transport.submissions, 0)
                    self.assertNotEqual(case.store.job('job')['state'], 'success')
                finally:
                    case.tearDown()
                    case.doCleanups()

    def test_force_is_a_single_budgeted_recovery_action_only_when_policy_allows(self):
        original_policy = f.policy
        for allow in (False, True):
            with self.subTest(allow_force=allow):
                def configured(gate):
                    value = original_policy(gate)
                    value['allow_force'] = allow
                    return value
                case = LifecycleTests('run')
                with patch.object(f, 'policy', side_effect=configured):
                    case.setUp()
                try:
                    case.observer.stubborn = True
                    case.submit(case.runner())
                    case.tick(3)
                    if allow:
                        self.assertEqual(case.observer.actions, ['start', 'TERM', 'KILL'])
                        self.assertEqual(case.store.job('job')['state'], 'success')
                        self.assertEqual(len(case.gate.snapshot()['recovery_budget']), 1)
                        self.assertEqual(case.gate.snapshot()['owners'], {})
                    else:
                        case.held()
                        self.assertEqual(case.observer.actions, ['start', 'TERM'])
                        self.assertEqual(case.store.job('job')['recovery_status'], 'operator_required')
                finally:
                    case.tearDown()
                    case.doCleanups()

    def test_backend_control_effect_and_submission_reply_interruptions_never_replay(self):
        for boundary in ('before_start', 'after_start', 'before_TERM', 'after_TERM', 'submit_reply'):
            with self.subTest(boundary=boundary):
                case = LifecycleTests('run')
                case.setUp()
                try:
                    runner = case.runner()
                    if boundary == 'submit_reply':
                        case.transport.on_submit = lambda: (_ for _ in ()).throw(f.Crash('lost prompt reply'))
                    else:
                        case.observer.boundary = boundary
                    with self.assertRaises(f.Crash):
                        case.submit(runner)
                    case.expire()
                    record = case.store.job('job').get('wallpaper_lifecycle', {})
                    if record.get('term_intent'):
                        case.manager.lifecycle.save('job', term_intent='2000-01-01T00:00:00+00:00')
                    with patch('aihub_gpu_runner.recovery.RecoveryManager.running', wraps=case.manager.running) as running:
                        if boundary == 'before_start':
                            running.side_effect = Uncertain('start_effect_unverified')
                        case.tick(3)
                    self.assertLessEqual(case.observer.actions.count('start'), 1)
                    self.assertLessEqual(case.observer.actions.count('TERM'), 1)
                    self.assertLessEqual(case.observer.actions.count('KILL'), 1)
                    self.assertLessEqual(case.transport.submissions, 1)
                    if boundary == 'before_start':
                        case.held()
                        self.assertEqual(case.store.job('job')['recovery_status'], 'operator_required')
                    else:
                        self.assertEqual(case.gate.snapshot()['owners'], {})
                        self.assertFalse(case.observer.current['running'])
                finally:
                    case.tearDown()
                    case.doCleanups()

    def test_shutdown_failure_never_releases_one_gpu_or_exposes_success(self):
        self.observer.fail = 'stop'
        runner = self.runner()
        self.submit(runner)
        self.tick(2)
        self.held()
        self.assertTrue(self.observer.current['running'])
        self.assertNotEqual(self.store.job('job')['state'], 'success')
        with self.assertRaises(Conflict):
            runner.artifact('job')
        app = Application(runner, 'synthetic-token-00000000')
        value = json.loads(app.handle('GET', '/jobs/job', {'Authorization': 'Bearer ' + app.token})[2])
        self.assertFalse(value['resources_released'])
        self.assertNotEqual(value['state'], 'success')

    def test_container_restart_after_stopped_readiness_cannot_escape_handoff_guard(self):
        original = self.observer.stopped_ready
        def drift(resources, deadline, processes=None):
            proof = original(resources, deadline, processes)
            if processes:  # Post-stop, after process/physical cleanup observations.
                self.observer.current.update(running=True, pid=999, started_at='foreign-restart')
            return proof
        with patch.object(self.observer, 'stopped_ready', side_effect=drift):
            self.submit(self.runner())
        self.tick(2)
        self.held()
        self.assertEqual(self.observer.actions, ['start', 'TERM'])
        self.assertEqual(self.store.job('job')['recovery_status'], 'operator_required')
        self.assertEqual(self.gate.snapshot().get('wallpaper_lifecycle_receipts', {}), {})

    def test_reboot_intentionally_stopped_with_and_without_retained_owner_never_starts(self):
        for owner in (False, True):
            with self.subTest(owner=owner):
                if owner:
                    lease = self.gate.try_acquire('old', 'hash', self.target.resources, 'wallpaper')
                    self.store.put_job({'request_id': 'old', 'request_hash': 'hash', 'state': 'running',
                        'target': 'wallpaper', 'resources': list(self.target.resources), 'error_code': None})
                    lease.close()
                self.boot[0] = f.BOOT2 if self.boot[0] == f.BOOT1 else f.BOOT1
                self.tick(2)
                self.assertEqual(self.observer.actions, [])
                self.assertEqual(self.gate.snapshot()['owners'], {})
                if owner:
                    self.assertEqual(self.store.job('old')['error_code'], 'guest_reboot_interrupted')
        self.assertEqual(self.summary()['incidents_7d'], 2)

    def test_running_unowned_container_is_not_adopted_or_blindly_stopped(self):
        self.observer.current.update(running=True, pid=101)
        self.submit(self.runner())
        self.tick(2)
        self.held()
        self.assertEqual(self.observer.actions, [])
        self.assertEqual(self.transport.submissions, 0)

    def test_missing_execution_deadline_fails_closed_without_teardown(self):
        self.transport.history = 'missing'
        runner = self.runner()
        self.submit(runner)
        self.manager.update_job('job', execution_deadline=None)
        self.tick(2)
        self.held()
        self.assertEqual(self.observer.actions, ['start'])
        self.assertIn('execution_deadline_missing_or_invalid', self.store.job('job')['recovery_error'])

    def test_unverified_lifecycle_phase_or_changed_request_binding_cannot_control_backend(self):
        for field in ('phase', 'identity'):
            with self.subTest(field=field):
                case = LifecycleTests('run')
                case.setUp()
                try:
                    case.transport.history = 'missing'
                    case.submit(case.runner())
                    record = case.store.job('job')['wallpaper_lifecycle']
                    if field == 'phase':
                        record['phase'] = 'invented'
                    else:
                        record['identity']['request_hash'] = 'changed'
                    case.manager.update_job('job', wallpaper_lifecycle=record)
                    case.tick(2)
                    case.held()
                    self.assertEqual(case.observer.actions, ['start'])
                    self.assertEqual(case.store.job('job')['recovery_status'], 'operator_required')
                finally:
                    case.tearDown()
                    case.doCleanups()

    def test_ambiguous_start_without_effect_never_starts_again(self):
        self.observer.fail = 'start'
        # Bound fake asynchronous observation rather than waiting through its whole window.
        with patch.object(self.manager, 'running', side_effect=Uncertain('start_effect_unverified')):
            self.submit(self.runner())
        with patch('aihub_gpu_runner.recovery.RecoveryManager.running',
                   side_effect=Uncertain('start_effect_unverified')):
            self.tick(3)
        self.held()
        self.assertEqual(self.observer.actions, ['start'])
        self.assertEqual(self.store.job('job')['recovery_status'], 'operator_required')

    def test_lifecycle_control_and_persistence_boundaries_resume_without_duplicate_effects(self):
        phases = ('prepared', 'starting', 'ready', 'submit_intent', 'terminal',
                  'artifact_saved', 'stopping', 'stopped', 'verified', 'released', 'handoff')
        # Each boundary runs in its own private journal; test bodies and runtime
        # controls are identical, with process death before/after one durable write.
        for phase in phases:
            for after in (False, True):
                with self.subTest(phase=phase, after=after):
                    case = LifecycleTests('run')
                    case.setUp()
                    try:
                        runner = case.runner()
                        real, hit = case.store.write, [False]
                        def interrupted(name, value):
                            record = value.get('wallpaper_lifecycle', {}) if isinstance(value, dict) else {}
                            match = (record.get('phase') == phase if phase != 'handoff' else
                                     name == 'ownership.json' and bool(value.get('wallpaper_lifecycle_receipts')))
                            if not hit[0] and match:
                                hit[0] = True
                                if after:
                                    real(name, value)
                                raise f.Crash('synthetic boundary')
                            return real(name, value)
                        with patch.object(case.store, 'write', side_effect=interrupted):
                            with self.assertRaises(f.Crash):
                                case.submit(runner)
                        self.assertTrue(hit[0])
                        case.expire()
                        with patch('aihub_gpu_runner.recovery.RecoveryManager.running',
                                   wraps=case.manager.running) as running:
                            if phase == 'starting' and after:
                                running.side_effect = Uncertain('ambiguous_start_without_effect')
                            case.tick(3)
                        actions = list(case.observer.actions)
                        case.tick(2)
                        self.assertEqual(case.observer.actions, actions)
                        self.assertLessEqual(actions.count('start'), 1)
                        self.assertLessEqual(actions.count('TERM'), 1)
                        self.assertLessEqual(actions.count('KILL'), 1)
                        self.assertLessEqual(case.transport.submissions, 1)
                        state = case.gate.snapshot()
                        self.assertLessEqual(len(state.get('recoveries', [])), 1)
                        self.assertLessEqual(len(state.get('wallpaper_lifecycle_receipts', {})), 1)
                        if state['owners']:
                            case.held()
                            self.assertEqual(case.store.job('job')['recovery_status'], 'operator_required')
                        else:
                            self.assertIn(case.store.job('job')['state'], ('success', 'failed'))
                            self.assertFalse(case.observer.current['running'])
                    finally:
                        case.tearDown()
                        case.doCleanups()


class StoppedObserverTests(unittest.TestCase):
    setUp = f.ObserverTests.setUp
    run_command = f.ObserverTests.run_command
    def test_stopped_cleanup_allows_known_music_and_never_queries_wallpaper_http(self):
        self.metadata['wallpaper']['State'].update(Running=False, Pid=0)
        self.p['stopped_idle_memory_mb'] = {'gpu0': 4, 'gpu1': 4}
        with patch.object(self.backend.transport, 'json', side_effect=AssertionError('stopped HTTP forbidden')):
            proof = self.observer.stopped_ready(('gpu0', 'gpu1'), time.monotonic() + 1)
        self.assertTrue(proof['idle_ready'])
        self.assertNotIn('backend_ready', proof)
        self.assertEqual(proof['observations']['service_memory_mb']['music']['gpu0'], 3)
    def test_either_gpu_stopped_calibration_or_identity_failure_is_not_cleanup_proof(self):
        self.metadata['wallpaper']['State'].update(Running=False, Pid=0)
        self.p['stopped_idle_memory_mb'] = {'gpu0': 4, 'gpu1': 4}
        for uuid in (GPU0, GPU1):
            for row in (uuid + ',5,0,40,0,No',
                        (GPU1 if uuid == GPU0 else GPU0) + ',3,0,40,0,No'):
                with self.subTest(uuid=uuid, row=row):
                    self.gpu_override = {uuid: row}
                    with self.assertRaises((Uncertain, RecoveryRequired)):
                        self.observer.stopped_ready(('gpu0', 'gpu1'), time.monotonic() + .01)

    def test_placement_requires_exact_full_uuid_order_and_unchanged_container(self):
        original = self.observer.command
        for rows in ('0, ' + GPU0 + '\n1, ' + GPU1,
                     '0, ' + GPU1 + '\n1, ' + GPU0,
                     '0, ' + GPU0):
            def command(argv, deadline):
                if argv[1] == 'exec':
                    self.assertEqual(argv[2], self.p['wallpaper'])
                    return rows
                return original(argv, deadline)
            with self.subTest(rows=rows), patch.object(self.observer, 'command', side_effect=command):
                identity = self.observer.container('wallpaper', time.monotonic() + 1)
                if rows.endswith(GPU1):
                    self.observer.placement(identity, ('gpu0', 'gpu1'), time.monotonic() + 1)
                else:
                    with self.assertRaises(RecoveryRequired):
                        self.observer.placement(identity, ('gpu0', 'gpu1'), time.monotonic() + 1)


if __name__ == '__main__':
    unittest.main()
