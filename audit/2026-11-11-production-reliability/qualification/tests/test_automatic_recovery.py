"""Offline recovery fault injection: private journals, real locks, fake controls only."""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from aihub_gpu_runner.backends import CleanupPending, Execution, Target, Uncertain
from aihub_gpu_runner.core import Admission, AtomicStore, NativeLock, RecoveryRequired, Runner, timestamp
from aihub_gpu_runner.recovery import (RecoveryManager, RecoveryObserver, authorize_policy,
                                       digest, valid_boot)
from tests.test_wallpaper_conditioning import GPU0, GPU1, WORKFLOW, Transport
from aihub_gpu_runner.backends import ComfyBackend

BOOT1 = '11111111-1111-4111-8111-111111111111'
BOOT2 = '22222222-2222-4222-8222-222222222222'


class Crash(BaseException):
    pass


def policy(gate):
    def service(name, char):
        return {'name': name, 'id': char * 64, 'image': 'sha256:' + 'c' * 64,
                'identity_mode': 'exact' if name == 'comfyui-wallpaper' else 'contract',
                'contract_sha256': 'd' * 64, 'idle_memory_mb': {'gpu0': 10, 'gpu1': 5}}
    return {'scope': gate.scope, 'docker': str(Path('/usr/bin/docker').resolve()),
            'boot_startup': 'verified_reboot_once',
            'journalctl': str(Path('/usr/bin/journalctl').resolve()),
            'nvidia_smi': str(Path('/usr/bin/nvidia-smi').resolve()), 'wallpaper': 'a' * 64,
            'gpu_uuids': {'gpu0': GPU0, 'gpu1': GPU1},
            'services': {'wallpaper': service('comfyui-wallpaper', 'a'),
                         'music': service('comfyui-music', 'b')},
            'idle_memory_mb': {'gpu0': 20, 'gpu1': 10}, 'max_temperature_c': 90,
            'idle_utilization_pct': 0, 'samples': 2, 'observation_timeout': 1,
            'grace_seconds': 1, 'allow_force': True, 'max_incidents': 1,
            'window_seconds': 3600, 'exclusive_wallpaper_access': True}


class Observer:
    def __init__(self):
        self.current = {'id': 'a' * 64, 'started_at': 'original', 'running': True,
                        'pid': 101, 'contract_sha256': 'd' * 64}
        self.actions, self.observations = [], []
        self.fail, self.boundary, self.stubborn = None, None, False
        self.callback = None
    def boundary_at(self, name):
        if self.boundary == name:
            self.boundary = None
            raise Crash(name)
    def container(self, service, deadline):
        self.boundary_at('inspect')
        return dict(self.current)
    def queues(self, resources, deadline, **kwargs):
        if self.fail == 'queue':
            raise RecoveryRequired('unexplained_backend_activity_music')
        return {'wallpaper': 'empty', 'music': 'idle'}
    def gpu_sample(self, resources, deadline, executing=False):
        self.observations.append((tuple(resources), executing))
        if self.fail in resources or self.fail == 'health':
            raise RecoveryRequired('physical_gpu_identity_unverified_' + self.fail)
        if self.fail == 'cleanup' and not executing:
            raise CleanupPending('unverifiable_cleanup')
        return {'gpu0': 3, 'gpu1': 2, 'music_idle_context': True}
    def ready(self, resources, deadline, skip_wallpaper=False):
        self.boundary_at('ready')
        if self.callback:
            self.callback()
        self.queues(resources, deadline)
        return {'cleanup_verified': True, 'backend_ready': True,
                'resources': list(resources), 'samples': self.gpu_sample(resources, deadline)}
    def processes(self, ident, deadline):
        return {'101': '1000'} if ident['running'] else {}
    def gone(self, processes):
        if self.fail == 'survivor':
            raise RecoveryRequired('original_container_process_survives')
    def signal(self, ident, signal, deadline):
        self.boundary_at('before_' + signal)
        self.actions.append(signal)
        if self.fail == 'stop':
            raise RecoveryRequired('synthetic_stop_failure')
        if not self.stubborn or signal == 'KILL':
            self.current.update(running=False, pid=0)
        self.boundary_at('after_' + signal)
    def start(self, ident, deadline):
        self.boundary_at('before_start')
        self.actions.append('start')
        if self.fail == 'start':
            raise RecoveryRequired('synthetic_start_failure')
        self.current.update(running=True, pid=202, started_at='restarted')
        self.boundary_at('after_start')


class Backend:
    def __init__(self):
        self.execution, self.reconciliations, self.cleanups = None, 0, 0
        self.cleanup_fail = False
    def reconcile(self, prompt, deadline):
        self.reconciliations += 1
        if self.execution == 'api_error':
            raise Uncertain('connection_error')
        return self.execution
    def cleanup(self, execution, deadline):
        self.cleanups += 1
        if self.cleanup_fail:
            raise CleanupPending('synthetic_cleanup_failed')
        return {'completion_verified': True, 'cleanup_verified': True}


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='offline-recovery-')
        self.addCleanup(self.tmp.cleanup)
        self.store = AtomicStore(Path(self.tmp.name) / 'state')
        self.boot = [BOOT1]
        self.gate = Admission(self.store, {r: Path(self.tmp.name) / (r + '.lock')
            for r in ('gpu0', 'gpu1')}, 'offline-combined-candidate', boot_reader=lambda: self.boot[0])
        self.gate.bootstrap({'approval_ref': 'OFFLINE', 'bootstrap_id': self.gate.snapshot()['bootstrap_id'],
                             'backend_quiescent': True, 'cleanup_verified': True})
        self.target = Target('wallpaper', 'comfy', ('gpu0', 'gpu1'), 'http://offline.invalid',
            workflow=WORKFLOW, output_node='2', idle_memory_mb=20, conditioning_idle_memory_mb=10)
        self.targets, self.backend, self.observer = {'wallpaper': self.target}, Backend(), Observer()
        self.backends = {'wallpaper': self.backend}
        self.p = policy(self.gate)
        authorize_policy(self.gate, self.targets, self.backends, self.p, 'OFFLINE', self.observer)
        self.manager = self.new_manager()
        self.addCleanup(patch.stopall)
        patch('subprocess.run', side_effect=AssertionError('real backend control forbidden')).start()
        patch('subprocess.check_output', side_effect=AssertionError('real GPU probes forbidden')).start()
    def new_manager(self):
        return RecoveryManager(self.gate, self.targets, self.backends, self.observer)
    def job(self, name='owned', *, host=False, target='wallpaper', resources=('gpu0', 'gpu1'), state='running'):
        lease = self.gate.try_acquire(name, 'hash-' + name, resources, target)
        self.assertIsNotNone(lease)
        self.store.put_job({'request_id': name, 'request_hash': 'hash-' + name, 'target': target,
            'state': state, 'resources': list(resources), 'host_stage': host, 'boot_id': self.boot[0],
            'lease_id': lease.owner['lease_id'], 'backend_detail': {'prompt_id': 'prompt'},
            'execution_container': dict(self.observer.current), 'execution_deadline': time.time() - 10,
            'error_code': None, 'updated_at': timestamp()})
        lease.close()
        return lease
    def tick(self):
        self.manager.tick(force=True)
    def incident(self):
        return next(iter(self.gate.snapshot()['automatic_recovery'].values()))
    def assert_held(self):
        self.assertEqual(set(self.gate.snapshot()['owners']), {'gpu0', 'gpu1'})
    def test_previous_boot_owner_and_waiting_jobs_terminalized_without_replay(self):
        self.job(host=True, target='host-whisperx')
        for host in (False, True):
            self.store.put_job({'request_id': 'waiting-' + str(host), 'request_hash': 'queued',
                'state': 'waiting', 'boot_id': BOOT1, 'host_stage': host})
        self.boot[0] = BOOT2
        self.assertIsNone(self.gate.try_acquire('new', 'new', ('gpu0',), 'wallpaper'))
        self.manager.startup()
        self.assertTrue(all(j['state'] == 'failed' for j in self.store.jobs()))
        self.assertEqual(self.store.job('owned')['error_code'], 'guest_reboot_interrupted')
        self.assertEqual(self.gate.snapshot()['owners'], {})
        self.assertEqual(self.gate.snapshot()['verified_boot'], BOOT2)
        self.assertEqual(self.observer.actions, [])
    def test_same_boot_host_stage_and_waiting_host_survive(self):
        self.job(host=True, target='host-whisperx')
        self.store.put_job({'request_id': 'waiting', 'request_hash': 'queued', 'state': 'waiting',
                            'boot_id': BOOT1, 'host_stage': True})
        before = self.store.jobs()
        self.manager.startup()
        self.assertEqual(self.store.jobs(), before)
        self.assert_held()
        self.assertEqual(self.observer.actions, [])
    def test_same_boot_backend_keeps_running_until_runner_deadline(self):
        self.job()
        self.manager.update_job('owned', execution_deadline=time.time() + 300)
        self.backend.execution = 'api_error'
        for _ in range(4):
            self.tick()
        self.assertEqual(self.backend.reconciliations, 2)
        self.assertEqual(self.observer.actions, [])
        self.assert_held()
    def test_completed_job_results_and_history_preserved_on_reboot(self):
        self.job(state='success')
        self.store.write('results/owned.json', {'value': 'preserve'})
        self.store.write_bytes('results/owned.bin', b'preserve artifact')
        state = self.gate.snapshot()
        state['recoveries'] = [{'legacy': 'preserve'}]
        self.store.write('ownership.json', state)
        self.boot[0] = BOOT2
        self.tick()
        self.assertEqual(self.store.job('owned')['state'], 'success')
        self.assertEqual(self.store.read('results/owned.json'), {'value': 'preserve'})
        self.assertEqual(self.store.path('results/owned.bin').read_bytes(), b'preserve artifact')
        self.assertEqual(self.gate.snapshot()['recoveries'][0], {'legacy': 'preserve'})
    def test_history_completion_avoids_teardown_and_publishes_existing_artifact(self):
        self.job()
        self.backend.execution = Execution({'done': True}, {'prompt_id': 'prompt'}, b'PNG', 'image/png')
        self.tick()
        self.assertEqual(self.observer.actions, [])
        self.assertEqual(self.store.job('owned')['state'], 'success')
        self.assertEqual(self.store.path('results/owned.bin').read_bytes(), b'PNG')
        self.assertEqual(self.gate.snapshot()['owners'], {})
    def test_deadline_teardown_restart_and_idempotent_invocations(self):
        self.job()
        self.observer.callback = lambda: self.assertIsNone(
            self.gate.try_acquire('conflicting', 'hash', ('gpu1',), 'ollama'))
        self.tick()
        self.assertEqual(self.observer.actions, ['TERM', 'start'])
        self.assertEqual(self.store.job('owned')['state'], 'failed')
        self.assertEqual(self.gate.snapshot()['owners'], {})
        for _ in range(3):
            self.new_manager().tick(force=True)
        self.assertEqual(self.observer.actions, ['TERM', 'start'])
        self.assertEqual(len(self.gate.snapshot()['recoveries']), 1)
    def test_owned_terminal_failure_can_recover_before_deadline(self):
        self.job()
        self.manager.update_job('owned', owned_execution_failed=True, execution_deadline=time.time() + 300)
        self.tick()
        self.assertEqual(self.observer.actions, ['TERM', 'start'])
    def test_each_gpu_identity_cleanup_health_and_unrelated_activity_failure_retains_both(self):
        self.job()
        for failure in ('gpu0', 'gpu1', 'health', 'cleanup', 'queue', 'survivor'):
            with self.subTest(failure=failure):
                state = self.gate.snapshot()
                state['automatic_recovery'] = {}
                state['recovery_budget'] = []
                self.store.write('ownership.json', state)
                self.observer.current.update(running=True, pid=101, started_at='original')
                self.observer.fail = failure
                self.tick()
                self.assert_held()
                self.assertEqual(self.incident()['phase'], 'operator_required')
    def test_stop_and_start_failure_do_not_release_or_retry(self):
        self.job()
        self.observer.fail = 'stop'
        self.tick()
        self.new_manager().tick(force=True)
        self.assertEqual(self.observer.actions, ['TERM'])
        self.assert_held()
    def test_start_failure_is_operator_required(self):
        self.job()
        self.observer.fail = 'start'
        self.tick()
        self.new_manager().tick(force=True)
        self.assertEqual(self.observer.actions, ['TERM', 'start'])
        self.assert_held()
    def test_container_replacement_blocks_control(self):
        self.job()
        self.observer.current['started_at'] = 'foreign'
        self.tick()
        self.assertEqual(self.observer.actions, [])
        self.assert_held()
    def test_missing_invalid_boot_metadata_never_proves_reboot(self):
        self.job()
        for value in (None, '', 'invented', BOOT1.upper() + 'x'):
            state = self.gate.snapshot()
            for owner in state['owners'].values():
                owner['boot_id'] = value
            self.store.write('ownership.json', state)
            self.boot[0] = BOOT2
            self.tick()
            self.assert_held()
            self.assertEqual(self.observer.actions, [])
        self.assertEqual(self.store.job('owned')['recovery_error'], 'lease_boot_metadata_unverified')
    def test_policy_cannot_upgrade_unresolved_legacy_owner(self):
        self.job()
        with self.assertRaises(RecoveryRequired):
            authorize_policy(self.gate, self.targets, self.backends, self.p, 'OFFLINE', self.observer)
    def test_same_boot_live_native_lock_prevents_backend_control(self):
        lease = self.job()
        lock = NativeLock(self.gate.lock_paths['gpu0'], create=False)
        self.assertTrue(lock.acquire(0))
        try:
            self.tick()
            self.assert_held()
            self.assertEqual(self.observer.actions, [])
        finally:
            lock.close()
        self.tick()
        self.assertEqual(self.observer.actions, ['TERM', 'start'])
    def test_rate_limit_survives_manager_restart_and_new_incident(self):
        self.job()
        self.tick()
        self.job('second')
        self.new_manager().tick(force=True)
        self.assert_held()
        second = [i for i in self.gate.snapshot()['automatic_recovery'].values()
                  if i['identity']['job_id'] == 'second'][0]
        self.assertEqual(second['error'], 'persistent_recovery_rate_limit')
        self.assertEqual(self.observer.actions, ['TERM', 'start'])
    def test_mutable_job_updates_do_not_change_incident_identity(self):
        self.job()
        self.manager.update_job('owned', execution_deadline=time.time() + 300)
        self.tick()
        incident_id = self.incident()['id']
        self.manager.update_job('owned', state='reconciling', phase='supervisor_updated', updated_by='restart')
        self.new_manager().tick(force=True)
        self.assertEqual(self.incident()['id'], incident_id)
        self.assertEqual(len(self.gate.snapshot()['automatic_recovery']), 1)
    def test_physical_lock_replacement_never_controls_backend(self):
        self.job()
        path = self.gate.lock_paths['gpu1']
        path.rename(path.with_suffix('.preserved'))
        path.touch()
        self.tick()
        self.assert_held()
        self.assertEqual(self.observer.actions, [])
    def test_recovery_restarts_after_stop_effect_and_after_start_effect(self):
        self.job()
        for boundary in ('after_TERM', 'after_start'):
            self.observer.boundary = boundary
            with self.assertRaises(Crash):
                self.tick()
            self.assert_held()
            self.manager = self.new_manager()
        self.tick()
        self.assertEqual(self.observer.actions, ['TERM', 'start'])
        self.assertEqual(self.gate.snapshot()['owners'], {})
        self.assertEqual(len(self.gate.snapshot()['recoveries']), 1)
    def test_start_intent_without_effect_is_not_repeated(self):
        self.job()
        self.observer.boundary = 'before_start'
        with self.assertRaises(Crash):
            self.tick()
        self.new_manager().tick(force=True)
        self.assertEqual(self.observer.actions, ['TERM'])
        self.assert_held()
        self.assertEqual(self.incident()['phase'], 'operator_required')
    def test_atomic_persistence_boundaries_before_and_after_effect(self):
        self.job()
        real = self.store.write
        cut = {'enabled': True}
        def write(name, value):
            if (name == 'ownership.json' and cut['enabled'] and
                    any(i.get('phase') == 'released' for i in value.get('automatic_recovery', {}).values())):
                cut['enabled'] = False
                real(name, value)
                raise Crash('after atomic audit/release')
            return real(name, value)
        with patch.object(self.store, 'write', side_effect=write):
            with self.assertRaises(Crash):
                self.tick()
        self.new_manager().tick(force=True)
        self.assertEqual(self.observer.actions, ['TERM', 'start'])
        self.assertEqual(len(self.gate.snapshot()['recoveries']), 1)
        self.assertEqual(self.gate.snapshot()['owners'], {})
    def test_finalization_interruption_resumes_without_teardown(self):
        self.job()
        self.backend.execution = Execution({'done': True}, {'prompt_id': 'prompt'})
        real = self.store.put_job
        def put(job):
            real(job)
            if job['state'] == 'success':
                raise Crash('after final job')
        with patch.object(self.store, 'put_job', side_effect=put):
            with self.assertRaises(Crash):
                self.tick()
        self.new_manager().tick(force=True)
        self.assertEqual(self.observer.actions, [])
        self.assertEqual(self.store.job('owned')['state'], 'success')
        self.assertEqual(self.gate.snapshot()['owners'], {})
    def test_dual_meeting_reboot_requires_current_gpu1_restoration_verification(self):
        from tests.test_dual_gpu import fixture
        from aihub_gpu_runner.dual_gpu import switch_binding
        # The shared lease is never sufficient to bypass existing placement checks.
        root = Path(self.tmp.name) / 'dual'
        root.mkdir()
        settings = fixture(root)[2]
        settings['mode_switch']['gpu_uuids'] = self.p['gpu_uuids']
        target = Target(name='meeting-dual', **dict(settings, resources=('gpu0', 'gpu1'), models=('synthetic',)))
        self.targets[target.name] = target
        self.job(host=True, target=target.name)
        self.manager.update_job('owned', dual_gpu_session=True, switch_binding=switch_binding(target.mode_switch))
        self.boot[0] = BOOT2
        with patch('aihub_gpu_runner.dual_gpu.ComposeOllamaSwitch.verify', side_effect=CleanupPending('placement_unverified')):
            self.tick()
        self.assert_held()
        self.assertEqual(self.store.job('owned')['state'], 'failed')
        self.assertEqual(self.observer.actions, [])

    def test_force_termination_is_policy_gated_and_issued_once(self):
        self.job()
        self.observer.stubborn = True
        # Persist a grace period already expired rather than sleeping in this test.
        original = self.manager.save
        def save(incident, **fields):
            if 'term_intent' in fields:
                fields['term_intent'] = '2000-01-01T00:00:00+00:00'
            return original(incident, **fields)
        with patch.object(self.manager, 'save', side_effect=save):
            self.tick()
        self.assertEqual(self.observer.actions, ['TERM', 'KILL', 'start'])
        self.assertEqual(self.gate.snapshot()['owners'], {})

    def test_runner_deadline_recovers_automatically_and_successful_generation_is_unchanged(self):
        target = Target('wallpaper', 'comfy', ('gpu0', 'gpu1'), 'http://offline.invalid',
            workflow=WORKFLOW, output_node='2', idle_memory_mb=20, conditioning_idle_memory_mb=10,
            execution_timeout=.02, cleanup_timeout=.1)
        transport = Transport(history='missing')
        backend = ComfyBackend(target, transport, lambda: 3, conditioning_probe=lambda: 2, poll_interval=.001)
        targets, backends = {'wallpaper': target}, {'wallpaper': backend}
        manager = RecoveryManager(self.gate, targets, backends, self.observer)
        runner = Runner(self.gate, targets, backends, recovery=manager, poll_interval=.001)
        self.addCleanup(runner.close)
        runner.submit('deadline', 'wallpaper', 'workflow', WORKFLOW)
        runner.future('deadline').result(2)
        deadline = time.monotonic() + 3
        while (runner.status('deadline')['state'] not in ('failed', 'success') or
               self.gate.snapshot()['owners']) and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(runner.status('deadline')['state'], 'failed')
        self.assertEqual(self.observer.actions, ['TERM', 'start'])
        self.assertEqual(transport.submissions, 1)
        self.assertEqual(self.gate.snapshot()['owners'], {})
        transport.history = 'success'
        runner.submit('success', 'wallpaper', 'workflow', WORKFLOW)
        runner.future('success').result(2)
        self.assertEqual(runner.status('success')['state'], 'success')
        self.assertEqual(self.observer.actions, ['TERM', 'start'])
        self.assertEqual(transport.submissions, 2)
        self.assertEqual(runner.artifact('success')[0], b'\x89PNG\r\n\x1a\nsynthetic')

    def test_uncertain_control_responses_are_resolved_by_observation(self):
        self.job()
        signal, start = self.observer.signal, self.observer.start
        def signal_uncertain(*args):
            signal(*args)
            raise Uncertain('response_lost_after_signal')
        def start_uncertain(*args):
            start(*args)
            raise Uncertain('response_lost_after_start')
        with patch.object(self.observer, 'signal', side_effect=signal_uncertain), \
                patch.object(self.observer, 'start', side_effect=start_uncertain):
            self.tick()
        self.assertEqual(self.observer.actions, ['TERM', 'start'])
        self.assertEqual(self.gate.snapshot()['owners'], {})

    def test_owner_free_reboot_readiness_failure_persists_admission_fence(self):
        self.boot[0] = BOOT2
        self.observer.fail = 'health'
        self.tick()
        self.observer.fail = None
        self.new_manager().tick(force=True)
        self.assertEqual(self.gate.snapshot()['boot_recovery_status'], 'operator_required')
        self.assertIsNone(self.gate.try_acquire('new', 'hash', ('gpu0',), 'wallpaper'))

    def test_force_disabled_never_uses_docker_stop_implicit_kill(self):
        self.job()
        self.observer.stubborn = True
        self.manager.p['allow_force'] = False
        original = self.manager.save
        def save(incident, **fields):
            if 'term_intent' in fields:
                fields['term_intent'] = '2000-01-01T00:00:00+00:00'
            return original(incident, **fields)
        with patch.object(self.manager, 'save', side_effect=save):
            self.tick()
        self.assertEqual(self.observer.actions, ['TERM'])
        self.assert_held()
        self.assertEqual(self.incident()['phase'], 'operator_required')

    def test_all_recovery_journal_write_boundaries_resume_without_duplicate_control(self):
        # Each run uses its own private journal and injects process death at a
        # different successful durable write, including both action intents.
        for cut_at in range(1, 17):
            with self.subTest(cut_at=cut_at):
                root = Path(self.tmp.name) / ('cut-' + str(cut_at))
                store = AtomicStore(root / 'state')
                gate = Admission(store, {r: root / (r + '.lock') for r in ('gpu0', 'gpu1')},
                                 self.gate.scope, boot_reader=lambda: BOOT1)
                gate.bootstrap({'approval_ref': 'OFFLINE', 'bootstrap_id': gate.snapshot()['bootstrap_id'],
                                 'backend_quiescent': True, 'cleanup_verified': True})
                observer = Observer()
                authorize_policy(gate, self.targets, self.backends, self.p, 'OFFLINE', observer)
                lease = gate.try_acquire('cut', 'hash', ('gpu0', 'gpu1'), 'wallpaper')
                store.put_job({'request_id': 'cut', 'request_hash': 'hash', 'state': 'running',
                    'target': 'wallpaper', 'resources': ['gpu0', 'gpu1'], 'boot_id': BOOT1,
                    'execution_deadline': time.time() - 10, 'error_code': None,
                    'execution_container': dict(observer.current), 'backend_detail': {'prompt_id': 'prompt'}})
                lease.close()
                manager = RecoveryManager(gate, self.targets, self.backends, observer)
                real, count = store.write, [0]
                def write(name, value):
                    real(name, value)
                    count[0] += 1
                    if count[0] == cut_at:
                        raise Crash('journal boundary')
                with patch.object(store, 'write', side_effect=write):
                    try:
                        manager.tick(force=True)
                    except Crash:
                        pass
                resumed = RecoveryManager(gate, self.targets, self.backends, observer)
                resumed.tick(force=True)
                resumed.tick(force=True)
                self.assertLessEqual(observer.actions.count('TERM'), 1)
                self.assertLessEqual(observer.actions.count('KILL'), 1)
                self.assertLessEqual(observer.actions.count('start'), 1)
                records = gate.snapshot().get('recoveries', [])
                self.assertLessEqual(len(records), 1)
                # An intent persisted before start with no observed effect stops
                # for review. Every other boundary completes without replay.
                if gate.snapshot()['owners']:
                    incident = next(iter(gate.snapshot()['automatic_recovery'].values()))
                    self.assertEqual(incident['phase'], 'operator_required')
                    self.assertEqual(set(gate.snapshot()['owners']), {'gpu0', 'gpu1'})
                else:
                    self.assertEqual(len(records), 1)


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='offline-observer-')
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(patch.stopall)
        patch('aihub_gpu_runner.recovery.guest_boot_id', return_value=BOOT1).start()
        self.kernel_message = 'synthetic healthy kernel'
        class Gate:
            scope = 'offline'
        self.p = policy(Gate())
        self.metadata = {}
        for service, binding in self.p['services'].items():
            meta = {'Id': binding['id'], 'Name': '/' + binding['name'], 'Image': binding['image'],
                'Config': {'Env': ['SYNTHETIC=1']}, 'Mounts': [],
                'HostConfig': {'RestartPolicy': {'Name': 'no'}},
                'State': {'Running': True, 'Pid': 101 if service == 'wallpaper' else 303,
                          'StartedAt': 'original', 'Paused': False, 'Dead': False, 'Restarting': False}}
            binding['contract_sha256'] = RecoveryObserver.contract(meta)
            self.metadata[service] = meta
        self.target = Target('wallpaper', 'comfy', ('gpu0', 'gpu1'), 'http://offline.invalid',
            workflow=WORKFLOW, output_node='2', idle_memory_mb=20, conditioning_idle_memory_mb=10)
        self.queue_busy, self.api_error = False, False
        parent = self
        class BackendTransport(Transport):
            def json(self, method, route, payload=None, timeout=5):
                if parent.api_error:
                    raise Uncertain('api_connection_failed')
                if route == '/system_stats':
                    return {'system': {'comfyui_version': 'synthetic'}}
                if route == '/queue' and parent.queue_busy:
                    return {'queue_running': [[0, 'foreign-prompt']], 'queue_pending': []}
                return super().json(method, route, payload, timeout)
        self.backend = ComfyBackend(self.target, BackendTransport(), lambda: 3, conditioning_probe=lambda: 2)
        self.apps = GPU0 + ',303,3\n'
        self.gpu_override = {}
        self.commands = []
        self.observer = RecoveryObserver(self.p, {'wallpaper': self.target}, {'wallpaper': self.backend},
                                         run=self.run_command, poll_interval=.001)
    def run_command(self, argv, **kwargs):
        from types import SimpleNamespace
        self.commands.append(argv)
        if argv[0] == self.p['journalctl']:
            return SimpleNamespace(returncode=0, stdout=json.dumps({'_BOOT_ID': BOOT1.replace('-', ''),
                '_TRANSPORT': 'kernel', 'MESSAGE': self.kernel_message}))
        if argv[0] == self.p['docker']:
            if argv[1] == 'inspect':
                for meta in self.metadata.values():
                    if argv[2] in (meta['Id'], meta['Name'][1:]):
                        return SimpleNamespace(returncode=0, stdout=json.dumps([meta]))
                raise AssertionError('unbound container')
            if argv[1] == 'top':
                return SimpleNamespace(returncode=0, stdout='PID\n' + ('101' if argv[2] == 'a'*64 else '303'))
            if argv[1] in ('kill', 'start'):
                self.assertEqual(argv[-1], 'a' * 64)
                return SimpleNamespace(returncode=0, stdout='')
            raise AssertionError('forbidden Docker operation')
        if 'query-compute-apps' in argv[1]:
            return SimpleNamespace(returncode=0, stdout=self.apps)
        uuid = argv[1].split('=', 1)[1]
        row = self.gpu_override.get(uuid, uuid + ',3,0,40,0,No')
        return SimpleNamespace(returncode=0, stdout=row)
    def test_expected_music_cuda_context_is_allowed_within_baseline(self):
        proof = self.observer.ready(('gpu0', 'gpu1'), time.monotonic() + 1)
        self.assertTrue(proof['cleanup_verified'])
        self.assertEqual(proof['observations']['service_memory_mb']['music']['gpu0'], 3)
        self.assertFalse(any(c[1] in ('kill', 'start') for c in self.commands if c[0] == self.p['docker']))
    def test_unknown_process_and_excess_music_residency_stop_recovery(self):
        for apps in (GPU0 + ',999,1', GPU0 + ',303,99'):
            self.apps = apps
            with self.assertRaises(RecoveryRequired):
                self.observer.gpu_sample(('gpu0', 'gpu1'), time.monotonic() + 1, executing=True)
    def test_either_uuid_health_temperature_or_memory_failure_blocks_cleanup(self):
        for uuid in (GPU0, GPU1):
            for row in ((GPU1 if uuid == GPU0 else GPU0) + ',3,0,40,0,No',
                        uuid + ',3,0,40,1,No', uuid + ',3,0,40,0,Yes',
                        uuid + ',3,0,99,0,No', uuid + ',999,0,40,0,No',
                        uuid + ',3,90,40,0,No'):
                with self.subTest(row=row):
                    self.gpu_override = {uuid: row}
                    with self.assertRaises((RecoveryRequired, CleanupPending)):
                        self.observer.gpu_sample(('gpu0', 'gpu1'), time.monotonic() + 1)
    def test_foreign_queue_and_connection_error_are_never_death_proof(self):
        self.queue_busy = True
        with self.assertRaises(RecoveryRequired):
            self.observer.queues(('gpu0', 'gpu1'), time.monotonic() + 1, prompt='owned', teardown=True)
        self.queue_busy, self.api_error = False, True
        observation = self.observer.queues(('gpu0', 'gpu1'), time.monotonic() + 1, teardown=True)
        self.assertTrue(observation['wallpaper']['api_unavailable'])
        with self.assertRaises(Uncertain):
            self.observer.ready(('gpu0', 'gpu1'), time.monotonic() + .02)
    def test_only_exact_wallpaper_id_receives_signals_and_generation_is_rechecked(self):
        identity = self.observer.container('wallpaper', time.monotonic() + 1)
        self.observer.signal(identity, 'TERM', time.monotonic() + 1)
        self.assertIn([self.p['docker'], 'kill', '--signal=TERM', 'a' * 64], self.commands)
        self.metadata['wallpaper']['State']['StartedAt'] = 'foreign'
        with self.assertRaises(RecoveryRequired):
            self.observer.signal(identity, 'KILL', time.monotonic() + 1)
        self.assertFalse(any('--signal=KILL' in c for c in self.commands))

    def test_kernel_driver_faults_and_unavailable_logs_block_recovery(self):
        self.kernel_message = 'NVRM: Xid (PCI:0000:01:00): 79 GPU has fallen off the bus'
        with self.assertRaisesRegex(RecoveryRequired, 'kernel_gpu_driver_fault'):
            self.observer.gpu_sample(('gpu0', 'gpu1'), time.monotonic() + 1)
        with patch.object(self.observer, 'command', return_value=''):
            with self.assertRaisesRegex(RecoveryRequired, 'kernel_health_log_unavailable'):
                self.observer.driver_health(time.monotonic() + 1)

    def test_legitimate_nonwallpaper_recreation_preserves_approved_contract(self):
        self.metadata['music']['Id'] = 'e' * 64
        proof = self.observer.ready(('gpu0', 'gpu1'), time.monotonic() + 1)
        self.assertEqual(proof['observations']['service_identities']['music']['id'], 'e' * 64)
        self.metadata['music']['Config']['Env'].append('CHANGED=1')
        with self.assertRaises(RecoveryRequired):
            self.observer.gpu_sample(('gpu0', 'gpu1'), time.monotonic() + 1)


if __name__ == '__main__':
    unittest.main()
