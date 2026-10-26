"""No production I/O: injected probes, real private journals and native locks."""
import copy
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from aihub_gpu_runner.backends import CleanupPending, Target, Uncertain
from aihub_gpu_runner.core import Admission, AtomicStore, NativeLock, RecoveryRequired, Runner, timestamp
from aihub_gpu_runner.prestart_recovery import ERROR, KIND, RECEIPTS
from aihub_gpu_runner.recovery import RecoveryManager, RecoveryObserver, authorize_policy, digest, validate_policy
from tests.test_automatic_recovery import BOOT1, BOOT2, Crash, policy
from tests.test_container_contract import metadata
from tests.test_wallpaper_conditioning import GPU0, GPU1, WORKFLOW


class PrestartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='offline-prestart-')
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(patch.stopall)
        self.root = Path(self.tmp.name)
        self.store = AtomicStore(self.root / 'state')
        self.boot = BOOT1
        self.clock = 1.
        patch('time.monotonic', side_effect=lambda: self.clock).start()
        patch('time.sleep', side_effect=self.sleep).start()
        patch('subprocess.run', side_effect=AssertionError('real control forbidden')).start()
        patch('subprocess.check_output', side_effect=AssertionError('real GPU forbidden')).start()
        patch('urllib.request.urlopen', side_effect=AssertionError('real HTTP forbidden')).start()
        patch('aihub_gpu_runner.recovery.guest_boot_id', return_value=BOOT1).start()
        self.gate = Admission(self.store, {r: self.root / (r + '.lock') for r in ('gpu0', 'gpu1')},
                              'offline-prestart', boot_reader=lambda: self.boot)
        self.gate.bootstrap({'approval_ref': 'OFFLINE', 'bootstrap_id': self.gate.snapshot()['bootstrap_id'],
                             'backend_quiescent': True, 'cleanup_verified': True})
        self.targets = {
            'wallpaper': Target('wallpaper', 'comfy', ('gpu0', 'gpu1'), 'http://offline.invalid',
                workflow=WORKFLOW, output_node='2', idle_memory_mb=20, conditioning_idle_memory_mb=10,
                lifecycle='on_demand', startup_timeout=2, shutdown_timeout=2),
            'music': Target('music', 'comfy', ('gpu0',), 'http://offline.invalid', workflow=WORKFLOW, output_node='2'),
            'ollama': Target('ollama', 'ollama', ('gpu1',), 'http://offline.invalid', models=('synthetic',))}
        self.metas = {s: metadata(s, c) for s, c in [('wallpaper', 'a'), ('music', 'b'), ('ollama', 'c')]}
        self.metas['music']['State'].update(Running=True, Pid=202)
        self.metas['ollama']['State'].update(Running=True, Pid=303)
        self.p = policy(self.gate)
        self.p.update(boot_startup='stopped_idle', samples=3,
                      stopped_idle_memory_mb={'gpu0': 8, 'gpu1': 4},
                      never_submitted_prestart_recovery={'enabled': True,
                          'ingress_isolation_verified': True, 'caller_exclusion_ref': 'OFFLINE-EXCLUSION'})
        self.p['services'] = {s: dict(name=m['Name'][1:], id=m['Id'], image=m['Image'],
            contract_sha256=RecoveryObserver.contract(m), identity_mode='exact' if s == 'wallpaper' else 'contract',
            idle_memory_mb={'gpu0': 10, 'gpu1': 5}) for s, m in self.metas.items()}
        self.calls, self.commands = [], []
        self.models, self.busy, self.gpu_high, self.unknown, self.excess = [], False, False, False, False
        self.kernel = 'healthy synthetic kernel'
        self.births = {'202': 'music-birth', '303': 'ollama-birth'}
        self.sample_hook = None
        self.backends = {n: types.SimpleNamespace(transport=types.SimpleNamespace(json=self.http)) for n in self.targets}
        self.observer = RecoveryObserver(self.p, self.targets, self.backends, run=self.command, poll_interval=.1)
        self.observer.processes = Mock(side_effect=lambda i, d: {str(i['pid']): self.births[str(i['pid'])]} if i['running'] else {})
        authorize_policy(self.gate, self.targets, self.backends, self.p, 'OFFLINE', self.observer)
        self.manager = self.new_manager()
        self.commands.clear(); self.calls.clear()

    def sleep(self, amount):
        self.clock += amount

    def new_manager(self):
        return RecoveryManager(self.gate, self.targets, self.backends, self.observer)

    def http(self, method, route, payload=None, timeout=1):
        self.calls.append((method, route))
        self.assertEqual(method, 'GET', 'no inference/control permitted')
        if route == '/api/ps': return {'models': self.models}
        if route == '/api/version': return {'version': 'synthetic'}
        if route == '/queue': return {'queue_running': [[0, 'foreign']] if self.busy else [], 'queue_pending': []}
        if route == '/system_stats': return {'system': {}}
        raise AssertionError(route)

    def command(self, argv, **kwargs):
        self.commands.append(argv)
        self.assertGreater(kwargs['timeout'], 0)
        if argv[0] == self.p['journalctl']:
            out = json.dumps({'_BOOT_ID': BOOT1.replace('-', ''), '_TRANSPORT': 'kernel', 'MESSAGE': self.kernel})
        elif argv[0] == self.p['docker']:
            if argv[1] == 'inspect':
                matches = [m for m in self.metas.values() if argv[2] in (m['Id'], m['Name'][1:])]
                if not matches: return types.SimpleNamespace(returncode=1, stdout='')
                out = json.dumps(matches)
            elif argv[1] == 'top':
                out = 'PID\n' + ('202' if argv[2] == 'b'*64 else '303')
            else:
                raise AssertionError('mutating Docker forbidden: ' + str(argv))
        elif argv[1].startswith('--query-compute-apps='):
            out = GPU0 + ',202,3\n' + GPU1 + ',303,' + ('96' if self.excess else '1') + '\n'
            if self.unknown: out += GPU1 + ',999,1\n'
        elif argv[1].startswith('--id='):
            uuid = argv[1][5:]
            out = f'{uuid},{96 if self.gpu_high else 3},0,30,0,No'
            if self.sample_hook: self.sample_hook()
        else:
            raise AssertionError(argv)
        return types.SimpleNamespace(returncode=0, stdout=out)

    def admit(self):
        payload = copy.deepcopy(WORKFLOW)
        request = {'target': 'wallpaper', 'operation': 'workflow', 'payload': payload}
        self.lease = self.gate.try_acquire('job', digest(request), ('gpu0', 'gpu1'), 'wallpaper')
        self.store.put_job(dict(request, request_id='job', request_hash=digest(request),
            resources=['gpu0', 'gpu1'], state='waiting', boot_id=self.boot, error_code=None,
            created_at=timestamp(), updated_at=timestamp()))
        self.runner = Runner.__new__(Runner)
        self.runner.store, self.runner.admission = self.store, self.gate
        self.runner.targets, self.runner.backends = self.targets, self.backends
        self.runner.job_mutex, self.runner.recovery = self.manager.jobs, self.manager
        self.runner.closed = False
        self.backends['wallpaper'].validate = Mock()

    def prepare(self, failure=None, live=False):
        self.admit()
        with patch.object(self.observer, 'stopped_ready', side_effect=failure or CleanupPending('synthetic_prestart_pending')):
            if live:
                self.manager.update_job('job', state='running', lease_id=self.lease.owner['lease_id'])
                with self.assertRaises(CleanupPending):
                    self.manager.lifecycle.start(self.lease.owner, self.lease.resources)
                self.manager.update_job('job', state='cleanup_pending', error_code='synthetic_prestart_pending')
            else:
                self.runner._work('job', self.lease)
        self.commands.clear(); self.calls.clear()
        self.prepared_clock = self.clock

    def tick(self): self.new_manager().tick(force=True)

    def held(self):
        self.assertEqual(set(self.gate.snapshot()['owners']), {'gpu0', 'gpu1'})
        self.assertFalse(self.gate.snapshot().get(RECEIPTS))

    def rejected(self):
        self.tick(); self.held()
        self.assertEqual(self.store.job('job')['recovery_status'], 'operator_required')

    def edit_job(self, **fields): self.manager.update_job('job', **fields)

    def edit_record(self, **fields):
        r = self.store.job('job')['wallpaper_lifecycle']; r.update(fields)
        self.edit_job(wallpaper_lifecycle=r)

    def test_verified_prestart_failure_releases_paired_owners_once_without_output_or_replay(self):
        self.prepare(); self.tick(); self.tick()
        state = self.gate.snapshot()
        self.assertEqual(state['owners'], {})
        receipt = state[RECEIPTS][self.lease.owner['lease_id']]
        self.assertEqual(receipt['kind'], KIND)
        self.assertEqual(receipt['proof']['stable_samples'], 3)
        self.assertEqual(len(receipt['proof']['samples']), 3)
        for sample in receipt['proof']['samples']:
            self.assertEqual(set(sample['observations']['gpus']), {'gpu0', 'gpu1'})
            self.assertEqual(sample['observations']['service_memory_mb']['ollama']['gpu1'], 1)
        self.assertEqual(receipt['proof']['process_births']['ollama'], {'303': 'ollama-birth'})
        self.assertEqual(len(state['recoveries']), 1)
        self.assertEqual(len(state['recovery_budget']), 1)
        self.assertNotIn('wallpaper_lifecycle_receipts', state)
        self.assertEqual(list(self.store.path('results').iterdir()), [])
        self.assertEqual(self.store.job('job')['error_code'], ERROR)
        self.assertEqual(self.store.job('job')['state'], 'failed')
        before = len(self.commands)
        returned = self.runner.submit('job', 'wallpaper', 'workflow', copy.deepcopy(WORKFLOW))
        self.assertEqual(returned['state'], 'failed')
        self.tick()
        self.assertEqual(len(self.commands), before)

    def test_running_wallpaper_is_not_stopped_or_adopted(self):
        self.prepare(); self.metas['wallpaper']['State'].update(Running=True, Pid=101)
        self.births['101'] = 'foreign'
        self.rejected()

    def test_replaced_wallpaper_or_changed_restart_policy_is_rejected(self):
        self.prepare(); self.metas['wallpaper']['HostConfig']['RestartPolicy']['Name'] = 'always'
        self.rejected()

    def test_start_generation_change_is_rejected(self):
        self.prepare(); self.metas['wallpaper']['State']['StartedAt'] = 'other'
        self.rejected()

    def test_replaced_container_id_is_rejected(self):
        self.prepare(); self.metas['wallpaper']['Id'] = 'f'*64
        self.rejected()

    def test_unknown_gpu_process_is_rejected_immediately(self):
        self.prepare(); self.unknown = True
        self.rejected(); self.assertEqual(self.clock, self.prepared_clock)

    def test_direct_api_activity_is_rejected(self):
        self.prepare(); self.models = [{'name': 'foreign'}]
        self.rejected()

    def test_foreign_music_queue_is_rejected(self):
        self.prepare(); self.busy = True
        self.rejected()

    def test_strict_service_excess_is_not_polled_or_reclassified(self):
        self.prepare(); self.excess = True
        self.rejected(); self.assertEqual(self.clock, self.prepared_clock)

    def test_birth_change_during_physical_sample_is_rejected(self):
        self.prepare(); self.sample_hook = lambda: self.births.update({'303': 'reused'})
        self.rejected()

    def test_both_native_locks_remain_held_during_physical_observation(self):
        self.prepare()
        def check_locks():
            for path in self.gate.lock_paths.values():
                probe = NativeLock(path, create=False)
                try: self.assertFalse(probe.acquire(0))
                finally: probe.close()
        self.sample_hook = check_locks
        self.tick()
        self.assertEqual(self.gate.snapshot()['owners'], {})

    def test_driver_fault_is_rejected(self):
        self.prepare(); self.kernel = 'NVRM Xid: synthetic fault'
        self.rejected()

    def test_aggregate_high_exhausts_one_deadline_and_cannot_renew(self):
        self.prepare(); self.gpu_high = True
        self.rejected(); count = len(self.commands)
        self.gpu_high = False; self.tick()
        self.held(); self.assertEqual(len(self.commands), count)
        self.assertAlmostEqual(self.clock - self.prepared_clock, 1., places=2)

    def test_intervening_high_aggregate_resets_consecutive_samples(self):
        self.prepare()
        original = self.observer.gpu_sample
        samples = [0]
        def sample(*a, **kw):
            samples[0] += 1
            self.gpu_high = samples[0] == 2
            return original(*a, **kw)
        with patch.object(self.observer, 'gpu_sample', side_effect=sample): self.tick()
        self.assertEqual(self.gate.snapshot()['owners'], {})
        self.assertEqual(samples[0], 5)

    def test_expired_observation_deadline_is_not_renewed(self):
        self.prepare()
        with patch('aihub_gpu_runner.wallpaper_lifecycle.now', return_value=10**12): self.rejected()
        self.assertEqual(self.commands, [])

    def test_owner_drift_retains_both(self):
        self.prepare()
        state = self.gate.snapshot(); state['owners']['gpu1']['lease_id'] = 'foreign'
        self.store.write('ownership.json', state)
        self.tick(); self.held()

    def test_live_native_worker_has_no_recovery_writes_or_probes(self):
        self.prepare(live=True)
        try:
            before = self.gate.snapshot()
            self.tick(); self.assertEqual(self.gate.snapshot(), before)
            self.assertEqual(self.commands, [])
        finally: self.lease.close()

    def test_external_native_lock_blocks_recovery(self):
        self.prepare()
        lock = NativeLock(self.gate.lock_paths['gpu1'], create=False)
        with lock.held(0):
            before = self.gate.snapshot(); self.tick()
            self.assertEqual(self.gate.snapshot(), before)
            self.assertEqual(self.commands, [])

    def test_replaced_native_lock_identity_retains_ownership(self):
        self.prepare()
        path = self.gate.lock_paths['gpu0']; path.rename(path.with_suffix('.old'))
        path.write_bytes(b'')
        self.rejected()

    def test_boot_mismatch_fences_without_new_release_or_gpu_calls(self):
        self.prepare(); self.boot = BOOT2
        self.tick(); self.held(); self.assertEqual(self.commands, [])
        self.assertEqual(self.gate.snapshot()['boot_recovery_status'], 'operator_required')

    def test_policy_change_retains_ownership(self):
        self.prepare()
        state = self.gate.snapshot(); state['recovery_policy']['policy_id'] = 'changed'
        self.store.write('ownership.json', state)
        self.rejected()

    def test_source_change_retains_ownership(self):
        self.prepare()
        with patch('aihub_gpu_runner.prestart_recovery.source_identity', return_value={}): self.rejected()

    def test_job_change_during_sample_retains_ownership(self):
        self.prepare(); self.sample_hook = lambda: self.edit_job(phase='foreign-write')
        self.rejected()

    def test_budget_exhaustion_retains_ownership_without_probes(self):
        self.prepare()
        self.manager.change(lambda s: s.update(recovery_budget=[{'incident_id': 'older', 'at': timestamp()}]))
        self.rejected(); self.assertEqual(self.commands, [])

    def test_existing_operator_incident_is_not_cleared(self):
        self.prepare()
        incident = self.manager.incident(self.lease.owner, self.lease.resources, 'wallpaper')
        self.manager.save(incident, phase='operator_required', error='old-latch')
        self.tick(); self.held(); self.assertEqual(self.commands, [])

    def test_existing_operator_job_latch_is_not_cleared(self):
        self.prepare(); self.edit_job(recovery_status='operator_required')
        self.rejected(); self.assertEqual(self.commands, [])

    def test_normal_receipt_with_retained_owner_is_ineligible(self):
        self.prepare()
        self.manager.change(lambda s: s.update(wallpaper_lifecycle_receipts={self.lease.owner['lease_id']: {}}))
        with self.assertRaisesRegex(RecoveryRequired, 'prior_receipt_with_retained_owner'):
            self.manager.prestart.checked(self.lease.owner, self.lease.resources)
        self.tick(); self.held(); self.assertEqual(self.commands, [])
        self.assertIn('automatic_recovery_error', self.gate.snapshot())

    def test_uncertain_incident_creation_latches_before_any_observation(self):
        self.prepare(); real = self.store.write
        def uncertain(name, value):
            real(name, value)
            if name == 'ownership.json' and value.get('automatic_recovery'):
                raise OSError('incident-create-fsync-uncertain')
        with patch.object(self.store, 'write', side_effect=uncertain): self.tick()
        self.rejected(); self.assertEqual(self.commands, [])

    def test_general_recovery_required_never_gets_new_failure_marker(self):
        self.prepare(failure=RecoveryRequired('unexplained_service'))
        self.assertNotIn('prestart_failure', self.store.job('job')['wallpaper_lifecycle'])
        self.rejected()

    def test_missing_durable_handoff_is_ineligible(self):
        self.prepare()
        record = self.store.job('job')['wallpaper_lifecycle']; del record['prestart_failure']
        self.edit_job(wallpaper_lifecycle=record)
        self.rejected()

    def test_ambiguous_start_or_submission_intent_even_null_is_ineligible(self):
        self.prepare(); self.edit_record(start_intent=None)
        self.rejected()

    def test_existing_prompt_or_artifact_is_ineligible(self):
        self.prepare(); self.edit_job(backend_detail={'prompt_id': 'foreign'})
        self.rejected()

    def test_result_file_is_ineligible(self):
        self.prepare(); self.store.write('results/job.json', {})
        self.rejected()

    def test_artifact_file_is_ineligible(self):
        self.prepare(); self.store.write_bytes('results/job.bin', b'foreign-output')
        self.rejected()

    def test_submission_intent_even_null_is_ineligible(self):
        self.prepare(); self.edit_record(submission_intent=None)
        self.rejected()

    def test_running_container_record_even_null_is_ineligible(self):
        self.prepare(); self.edit_record(running_container=None)
        self.rejected()

    def test_request_payload_drift_is_ineligible(self):
        self.prepare(); self.edit_job(payload={})
        self.rejected(); self.assertEqual(self.commands, [])

    def test_uncertain_general_worker_failure_cannot_use_valid_handoff(self):
        self.prepare(); self.edit_job(error_code='supervision_or_storage_failure')
        self.rejected(); self.assertEqual(self.commands, [])

    def test_expected_service_stopped_is_ineligible(self):
        self.prepare(); self.metas['ollama']['State'].update(Running=False, Pid=0)
        self.rejected()

    def test_unknown_kernel_boot_is_ineligible(self):
        self.prepare(); self.boot = None
        self.tick(); self.held(); self.assertEqual(self.commands, [])

    def test_lost_failure_marker_write_acknowledgement_cannot_release(self):
        real = self.store.write
        def uncertain(name, value):
            real(name, value)
            if name == 'jobs/job.json' and value.get('wallpaper_lifecycle', {}).get('prestart_failure'):
                raise OSError('failure-marker-fsync-uncertain')
        with patch.object(self.store, 'write', side_effect=uncertain), self.assertRaises(Exception):
            self.prepare()
        self.lease.close(); self.rejected()

    def test_crash_during_failure_finalization_reconciles_once_without_probes(self):
        self.prepare(); real = self.store.write
        def crash(name, value):
            real(name, value)
            if name == 'jobs/job.json' and value.get('prestart_failure_receipt'):
                raise Crash('finalized-job-fsync')
        with patch.object(self.store, 'write', side_effect=crash), self.assertRaises(Crash): self.tick()
        before = len(self.commands); self.tick()
        self.assertEqual(len(self.commands), before)
        self.assertEqual(self.store.job('job')['error_code'], ERROR)
        self.assertEqual(len(self.gate.snapshot()['recoveries']), 1)

    def test_uncertain_observation_intent_latches_without_physical_probes(self):
        self.prepare(); real = self.store.write
        def uncertain(name, value):
            real(name, value)
            if name == 'ownership.json' and any(i.get('phase') == 'prestart_observing' for i in value.get('automatic_recovery', {}).values()):
                raise OSError('observation-intent-fsync-uncertain')
        with patch.object(self.store, 'write', side_effect=uncertain): self.tick()
        self.rejected(); self.assertEqual(self.commands, [])

    def test_tampered_receipt_prevents_job_finalization(self):
        self.prepare(); real = self.store.write
        def crash(name, value):
            real(name, value)
            if name == 'ownership.json' and value.get(RECEIPTS): raise Crash('after-release')
        with patch.object(self.store, 'write', side_effect=crash), self.assertRaises(Crash): self.tick()
        state = self.gate.snapshot()
        state[RECEIPTS][self.lease.owner['lease_id']]['prepared_sha256'] = 'tampered'
        self.store.write('ownership.json', state)
        before = len(self.commands)
        with self.assertRaisesRegex(RecoveryRequired, 'receipt_changed'):
            self.new_manager().prestart.finalize_released()
        self.assertEqual(len(self.commands), before)
        self.assertNotEqual(self.store.job('job')['state'], 'failed')

    def test_crash_after_receipt_before_finalization_resumes_without_probes_or_release(self):
        self.prepare(); real = self.store.write
        def crash(name, value):
            real(name, value)
            if name == 'ownership.json' and value.get(RECEIPTS): raise Crash('after-release')
        with patch.object(self.store, 'write', side_effect=crash), self.assertRaises(Crash): self.tick()
        self.assertEqual(self.gate.snapshot()['owners'], {})
        self.assertNotEqual(self.store.job('job')['state'], 'failed')
        before = len(self.commands)
        self.new_manager().startup(); self.tick()
        self.assertEqual(len(self.commands), before)
        self.assertEqual(self.store.job('job')['error_code'], ERROR)
        self.assertEqual(len(self.gate.snapshot()['recoveries']), 1)

    def test_uncertain_release_write_before_effect_latches_and_after_effect_reconciles(self):
        self.prepare(); real = self.store.write
        def uncertain(name, value):
            if name == 'ownership.json' and value.get(RECEIPTS): raise OSError('before-effect')
            real(name, value)
        with patch.object(self.store, 'write', side_effect=uncertain): self.tick()
        self.held(); before = len(self.commands)
        self.tick(); self.held(); self.assertEqual(len(self.commands), before)

    def test_uncertain_release_after_effect_uses_receipt(self):
        self.prepare(); real = self.store.write
        def uncertain(name, value):
            real(name, value)
            if name == 'ownership.json' and value.get(RECEIPTS): raise OSError('after-effect')
        with patch.object(self.store, 'write', side_effect=uncertain): self.tick()
        self.assertEqual(self.gate.snapshot()['owners'], {})
        before = len(self.commands); self.tick()
        self.assertEqual(len(self.commands), before)
        self.assertEqual(self.store.job('job')['error_code'], ERROR)

    def test_interrupted_observation_intent_cannot_reobserve(self):
        self.prepare(); real = self.store.write
        def crash(name, value):
            real(name, value)
            if name == 'ownership.json' and any(i.get('phase') == 'prestart_observing' for i in value.get('automatic_recovery', {}).values()):
                raise Crash('observation-intent')
        with patch.object(self.store, 'write', side_effect=crash), self.assertRaises(Crash): self.tick()
        self.rejected(); self.assertEqual(self.commands, [])

    def test_failed_prepared_write_never_grants_new_authority(self):
        real = self.store.write
        def uncertain(name, value):
            real(name, value)
            if name == 'jobs/job.json' and value.get('wallpaper_lifecycle', {}).get('phase') == 'prepared':
                raise OSError('prepared-fsync-uncertain')
        with patch.object(self.store, 'write', side_effect=uncertain):
            with self.assertRaises(Exception): self.prepare()
        self.lease.close(); self.rejected()

    def test_policy_gate_requires_explicit_ingress_exclusion_and_calibration(self):
        for mutate in (lambda p: p['never_submitted_prestart_recovery'].update(ingress_isolation_verified=False),
                       lambda p: p['never_submitted_prestart_recovery'].update(caller_exclusion_ref=''),
                       lambda p: p['services'].pop('ollama')):
            p = copy.deepcopy(self.p); mutate(p)
            with self.assertRaisesRegex(ValueError, 'explicit_isolated_paired_policy'):
                validate_policy(p, self.gate, self.targets)

    def test_old_policy_and_old_record_grant_no_new_authority(self):
        p = copy.deepcopy(self.p); del p['never_submitted_prestart_recovery']
        validate_policy(p, self.gate, self.targets)
        state = self.gate.snapshot(); state['recovery_policy'].update(policy=p, policy_id=digest(p))
        self.store.write('ownership.json', state)
        self.observer.p = p; self.manager = self.new_manager()
        self.prepare()
        self.assertNotIn('prestart_binding', self.store.job('job')['wallpaper_lifecycle'])
        self.assertNotIn('prestart_failure', self.store.job('job')['wallpaper_lifecycle'])
        self.assertFalse(self.manager.prestart.enabled)

    def test_opted_in_normal_start_does_not_create_failure_handoff(self):
        self.admit()
        self.manager.update_job('job', state='running', lease_id=self.lease.owner['lease_id'])
        self.addCleanup(self.lease.close)
        # The control implementation is injected; no container is started.
        def started(original, deadline):
            self.metas['wallpaper']['State'].update(Running=True, Pid=101, StartedAt='new')
            self.births['101'] = 'owned-wallpaper'
        with patch.object(self.observer, 'start', side_effect=started), \
                patch.object(self.observer, 'placement', return_value={'verified': True}), \
                patch.object(self.observer, 'ready', return_value={'backend_ready': True}):
            self.manager.lifecycle.start(self.lease.owner, self.lease.resources)
        record = self.store.job('job')['wallpaper_lifecycle']
        self.assertEqual(record['phase'], 'ready')
        self.assertIn('start_intent', record)
        self.assertNotIn('prestart_failure', record)
        self.assertIn('prestart_binding', record)


if __name__ == '__main__': unittest.main()
