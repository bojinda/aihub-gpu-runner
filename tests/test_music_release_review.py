"""Permanent review regressions: real private journals/API, fake GPU and I/O.

The nine original adversarial probes are retained here with corrected diagnostic
expectations and the required failed-only recovery disposition.
"""
import concurrent.futures
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import unittest
from unittest.mock import patch

from aihub_gpu_runner import __main__ as cli
from aihub_gpu_runner.api import Application
from aihub_gpu_runner.backends import CleanupPending, Uncertain
from aihub_gpu_runner.client import JobClient
from aihub_gpu_runner.core import Conflict, RecoveryRequired, encode
from aihub_gpu_runner.music_cleanup import MusicReleaseGuard, failure_code
from aihub_gpu_runner.recovery import digest
from tests.test_automatic_recovery import BOOT2, Crash
from tests.test_music_release import Fixture


class MusicReviewTests(unittest.TestCase):
    token = 'offline-only-review-token'

    def api(self, f): return Application(f.runner, self.token)

    def get(self, f, suffix=''):
        return self.api(f).handle('GET','/jobs/music-job'+suffix,{'Authorization':'Bearer '+self.token})

    def crash_terminal(self):
        f=Fixture(self);f.memory=lambda:422;write=f.store.write
        def crash(name,value):
            write(name,value)
            if name.startswith('jobs/') and value.get('state')=='success':raise Crash('offline terminal edge')
        f.store.write=crash
        with self.assertRaises(Crash):f.run()
        f.store.write=write;f.lease.close()
        return f

    def local_evidence(self, f, **extra):
        return dict({k:f.lease.owner[k] for k in ('job_id','request_hash','lease_id','target')},
                    approval_ref='OFFLINE-NEW-TEST-ONLY',operator_approved_backend_recovery=True,
                    backend_quiescent=True,cleanup_verified=True,**extra)

    def local(self, f, *, terminal=True, **extra):
        f.a.recover('music-job',self.local_evidence(f,**extra))
        if terminal:
            f.runner._update('music-job',state='failed',error_code='operator_recovered_no_automatic_resubmission')

    def automatic(self, f):
        f.boot=BOOT2
        # Only hardware observations are injected. Real recover/finish/audit/clear.
        f.manager.boot_readiness=lambda resources,deadline:{'cleanup_verified':True,'idle_ready':True,'verified_boot':BOOT2}
        f.manager.boot_generation=lambda deadline:None
        f.manager.recover(f.lease.owner,('gpu0',))

    def client(self, f, routes):
        api=self.api(f);token=self.token
        class GetOnly:
            def request(self, method, route, payload=None):
                if method!='GET' or payload is not None:raise RuntimeError('GET_only_test')
                routes.append(route)
                return api.handle(method,route,{'Authorization':'Bearer '+token})
        return JobClient(GetOnly())

    def test_hard_faults_preserve_fixed_reason_through_job_and_http(self):
        expected={'foreign_queue':'unexplained_backend_activity_music',
                  'unknown_compute':'unexplained_gpu_process_or_memory',
                  'health':'gpu_health_requires_operator_gpu0','pid_birth':'music_release_process_birth_changed'}
        for fault,reason in expected.items():
            with self.subTest(fault=fault):
                f=Fixture(self)
                def inject():
                    if fault=='foreign_queue':f.foreign=True
                    elif fault=='unknown_compute':f.extra_pid=999
                    elif fault=='health':f.health='1'
                    else:f.births['202']='new'
                f.on_ack=inject;job=f.run();f.failed()
                self.assertEqual(f.free,1);self.assertEqual(f.clock.t,0)
                self.assertEqual(job['error_code'],reason)
                http,_,body=self.get(f)
                self.assertEqual(http,200);self.assertEqual(json.loads(body)['error_code'],reason)
                patch.stopall()

    def test_missing_or_wrong_release_receipt_blocks_result_and_artifact(self):
        for mode in ('missing','wrong_hash','owner','policy','resources','context','release_time','proof_marker'):
            with self.subTest(mode=mode):
                f=Fixture(self);f.run();f.success();state=f.a.snapshot()
                receipt=state['music_release_receipts'][f.lease.owner['lease_id']]
                if mode=='missing':state['music_release_receipts'].clear()
                elif mode=='wrong_hash':receipt['proof_sha256']='bad'
                elif mode=='owner':receipt['owner']['request_hash']='other'
                elif mode=='context':receipt['final_observation']['context_sha256']='other'
                elif mode=='policy':receipt['policy_id']='other'
                elif mode=='release_time':receipt.pop('released_at')
                elif mode=='proof_marker':
                    job=f.store.job('music-job')
                    job['cleanup_proof']['music_release_idle']['cleanup_verified']=False
                    job['music_release_idle']=copy.deepcopy(job['cleanup_proof']['music_release_idle'])
                    receipt['proof_sha256']=digest(job['music_release_idle']);f.store.put_job(job)
                else:receipt['resources']=['gpu1']
                f.store.write('ownership.json',state)
                for reader in (f.runner.result,f.runner.artifact):
                    with self.assertRaisesRegex(Conflict,'receipt_unverified'):reader('music-job')
                patch.stopall()

    def test_terminal_status_is_not_published_artifact(self):
        f=Fixture(self);f.memory=lambda:422;original=f.lease.release
        def check(proof,**kw):
            status=self.api(f)._status(f.store.job('music-job'))
            self.assertEqual(status['state'],'success');self.assertFalse(status['resources_released'])
            for reader in (f.runner.result,f.runner.artifact):
                with self.assertRaises(Conflict):reader('music-job')
            return original(proof,**kw)
        f.lease.release=check;f.run();f.success()

    def test_old_reader_is_unsafe_for_owned_terminal_crash(self):
        f=self.crash_terminal();f.held()
        with self.assertRaises(Conflict):f.runner.artifact('music-job')
        path=Path(__file__).resolve().parents[1]/'qualification/baseline-core.py'
        spec=importlib.util.spec_from_file_location('aihub_gpu_runner.review_base_core',path)
        old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        reader=object.__new__(old.Runner);reader.store=f.store;reader.futures={};reader.admission=f.a
        self.assertEqual(reader.artifact('music-job')[0],b'ID3offline')

    def test_automatic_failed_recovery_release_supports_get_only_retrieval(self):
        f=self.crash_terminal()
        f.runner._update('music-job',state='cleanup_pending',error_code='offline interrupted')
        self.automatic(f)
        job=f.store.job('music-job');self.assertEqual(job['state'],'failed')
        self.assertEqual(job['error_code'],'guest_reboot_interrupted')
        self.assertEqual(job['recovery_status'],'verified');self.assertFalse(f.a.snapshot()['owners'])
        before={p:p.read_bytes() for p in f.store.root.rglob('*') if p.is_file()}
        routes=[];client=self.client(f,routes)
        for _ in range(2):
            self.assertEqual(client.result('music-job')['state'],'failed')
            self.assertEqual(client._request('music-job','GET','/jobs/music-job/artifact',binary=True)[0],b'ID3offline')
        self.assertEqual(before,{p:p.read_bytes() for p in f.store.root.rglob('*') if p.is_file()})
        self.assertTrue(routes);self.assertEqual(f.free,1)
        self.assertFalse(f.a.snapshot().get('music_release_receipts'))

    @unittest.skipUnless(sys.platform.startswith('linux'),'real POSIX directory fsync edge')
    def test_real_directory_fsync_failure_after_release_replace(self):
        f=Fixture(self);f.memory=lambda:422;original=os.fsync;in_clear=[False];injected=[];write=f.store.write
        def fsync(fd):
            if in_clear[0] and not injected and stat.S_ISDIR(os.fstat(fd).st_mode):
                injected.append(True);raise OSError('offline directory fsync failure')
            return original(fd)
        def wrapped(name,value):
            in_clear[0]=name=='ownership.json' and bool(value.get('music_release_receipts')) and not value['owners']
            try:return write(name,value)
            finally:in_clear[0]=False
        f.store.write=wrapped
        with patch('os.fsync',side_effect=fsync):job=f.run()
        self.assertTrue(injected);self.assertEqual(job['publication_warning'],'ownership_release_sync_unconfirmed')
        self.assertFalse(f.a.snapshot()['owners']);self.assertEqual(f.free,1)
        self.assertEqual(f.runner.artifact('music-job')[0],b'ID3offline')

    def test_post_last_sample_rebound_is_a_sequential_observation_limit(self):
        f=Fixture(self);f.memory=lambda:422;original=MusicReleaseGuard.release_binding
        def rebound(guard,deadline):
            f.memory=lambda:454;return original(guard,deadline)
        with patch.object(MusicReleaseGuard,'release_binding',rebound):f.run()
        f.success()
        with self.assertRaisesRegex(RecoveryRequired,'baseline_music'):f.obs.gpu_sample(('gpu0',),70,stopped=True)

    def test_gpu1_owner_update_before_commit_is_preserved(self):
        f=Fixture(self);f.extra_gpu1=True
        other=f.a.try_acquire('ollama-job','other',('gpu1',),'ollama');self.addCleanup(other.close)
        original=MusicReleaseGuard.observe_release
        def observe(guard,deadline):
            original(guard,deadline);other.mark('executing',{'offline':'concurrent GPU1 update'})
        with patch.object(MusicReleaseGuard,'observe_release',observe):f.run()
        f.success();self.assertEqual(f.a.snapshot()['owners']['gpu1']['lease_id'],other.owner['lease_id'])
        self.assertEqual(f.a.snapshot()['owners']['gpu1']['phase'],'executing')

    def test_nonfinite_compute_memory_hard_stops_without_release(self):
        for invalid in ('nan','inf','-1'):
            with self.subTest(invalid=invalid):
                f=Fixture(self);command=f.obs.command
                def bad(argv,deadline):
                    raw=command(argv,deadline)
                    return raw.rsplit(',',1)[0]+','+invalid+'\n' if argv[1]=='--query-compute-apps=gpu_uuid,pid,used_gpu_memory' else raw
                f.obs.command=bad;f.run();f.failed();self.assertEqual(f.free,1);self.assertEqual(f.clock.t,0)
                self.assertEqual(f.store.job('music-job')['error_code'],'unexplained_gpu_process_or_memory')
                patch.stopall()

    def test_real_local_cli_failed_recovery_gets_existing_artifact_without_normal_receipt(self):
        f=self.crash_terminal();evidence=f.root/'offline-evidence.json'
        evidence.write_bytes(encode(self.local_evidence(f)))
        with patch.object(cli,'admission_from_config',return_value=({},f.targets,f.a)),patch.object(
                sys,'argv',['runner','--config','offline-only','recover','--job-id','music-job','--evidence-file',str(evidence)]),patch('sys.stdout',new=io.StringIO()):
            cli.main()
        job=f.store.job('music-job');self.assertEqual(job['state'],'failed')
        self.assertEqual(job['error_code'],'operator_recovered_no_automatic_resubmission')
        self.assertFalse(f.a.snapshot().get('music_release_receipts'))
        audit=f.a.snapshot()['recoveries'][0]
        self.assertNotIn('provenance',audit);self.assertNotIn('boot_id',audit);self.assertNotIn('resources',audit)
        routes=[];self.assertEqual(self.client(f,routes).result('music-job')['state'],'failed')
        self.assertEqual(self.get(f,'/artifact')[2],b'ID3offline');self.assertEqual(f.free,1)

    def test_local_audit_before_terminal_failed_write_stays_fenced_across_restart(self):
        f=self.crash_terminal();self.local(f,terminal=False)
        self.assertEqual(f.store.job('music-job')['state'],'success')
        self.assertEqual(self.get(f,'/result')[0],409);self.assertEqual(self.get(f,'/artifact')[0],409)
        # No normal receipt can be inferred from this already-committed audit.
        self.assertFalse(f.a.snapshot()['owners']);self.assertFalse(f.a.snapshot().get('music_release_receipts'))
        from aihub_gpu_runner.core import Runner
        with patch.object(f.manager,'start'),patch.object(f.manager,'close'):
            runner=Runner(f.a,f.targets,f.backends,recovery=f.manager,poll_interval=.01)
            try:
                self.assertFalse(runner.pending)
                with self.assertRaises(Conflict):runner.result('music-job')
            finally:runner.close()
        f.runner._update('music-job',state='failed',error_code='operator_recovered_no_automatic_resubmission')
        self.assertEqual(self.get(f,'/result')[0],200);self.assertEqual(f.free,1)

    def test_actual_local_cli_crash_after_audit_before_failed_write(self):
        f=self.crash_terminal();path=f.root/'offline-evidence.json'
        path.write_bytes(encode(self.local_evidence(f)));put=f.store.put_job
        def crash(job):
            if job.get('error_code')=='operator_recovered_no_automatic_resubmission':
                raise Crash('offline crash before terminal-failed write')
            return put(job)
        with patch.object(cli,'admission_from_config',return_value=({},f.targets,f.a)),patch.object(
                sys,'argv',['runner','--config','offline-only','recover','--job-id','music-job','--evidence-file',str(path)]),patch.object(
                f.store,'put_job',side_effect=crash),self.assertRaises(Crash):
            cli.main()
        self.assertFalse(f.a.snapshot()['owners']);self.assertEqual(len(f.a.snapshot()['recoveries']),1)
        self.assertEqual(f.store.job('music-job')['state'],'success')
        self.assertEqual(self.get(f,'/result')[0],409);self.assertEqual(self.get(f,'/artifact')[0],409)
        self.assertFalse(f.a.snapshot().get('music_release_receipts'));self.assertEqual(f.free,1)

    def test_failed_recovery_without_normal_cleanup_proof_uses_only_its_audit(self):
        f=self.crash_terminal();job=f.store.job('music-job')
        job.pop('cleanup_proof');job.pop('music_release_idle');f.store.put_job(job)
        self.local(f,boot_id=f.lease.owner['boot_id'],resources=['gpu0'],policy_id=f.p and digest(f.p),
                   physical_gpu_uuids={'gpu0':f.p['gpu_uuids']['gpu0']})
        self.assertEqual(self.get(f,'/result')[0],200)
        state=f.a.snapshot();state['recoveries']=[];f.store.write('ownership.json',state)
        self.assertEqual(self.get(f,'/result')[0],409)

    def test_automatic_success_without_normal_receipt_remains_fenced(self):
        f=self.crash_terminal();self.automatic(f)
        self.assertEqual(f.store.job('music-job')['state'],'success')
        self.assertFalse(f.a.snapshot()['owners']);self.assertEqual(self.get(f,'/result')[0],409)

    def test_local_wrong_and_historical_audits_never_authorize(self):
        changes=('job_id','request_hash','lease_id','target','boot_id','resources','policy_id','physical_gpu_uuids',
                 'approval_ref','operator_approved_backend_recovery','backend_quiescent','cleanup_verified',
                 'incident_id','provenance','missing_audit','unrelated','ready_only','wrong_terminal_reason')
        for change in changes:
            with self.subTest(change=change):
                f=self.crash_terminal();self.local(f);state=f.a.snapshot();audit=state['recoveries'][0]
                if change=='missing_audit':state['recoveries']=[]
                elif change=='unrelated':state['recoveries']=[{'historical':'not this job','recovered_at':'old'}]
                elif change=='ready_only':state['recoveries']=[];state['ready']=True
                elif change=='wrong_terminal_reason':f.runner._update('music-job',error_code='unrelated_failure')
                elif change in ('operator_approved_backend_recovery','backend_quiescent','cleanup_verified'):audit[change]=False
                elif change=='approval_ref':audit[change]=''
                else:audit[change]='unrelated'
                f.store.write('ownership.json',state)
                self.assertEqual(self.get(f,'/result')[0],409);patch.stopall()

    def test_automatic_wrong_bindings_and_incomplete_proof_never_authorize(self):
        changes=('job_id','request_hash','lease_id','target','boot_id','policy_id','physical_gpu_uuids','resources',
                 'incident_id','observed_boot','provenance','incident_phase','incident_boot','incident_proof',
                 'job_incident','job_proof','job_recovery_status','cleanup','verified_boot','readiness','duplicate_unrelated')
        for change in changes:
            with self.subTest(change=change):
                f=self.crash_terminal();f.runner._update('music-job',state='cleanup_pending',error_code='offline interrupted')
                self.automatic(f);state=f.a.snapshot();audit=state['recoveries'][0]
                incident=state['automatic_recovery'][audit['incident_id']]
                if change in ('job_id','request_hash','lease_id','target','boot_id','policy_id','physical_gpu_uuids','resources'):
                    audit['identity'][change]='wrong'
                elif change=='incident_phase':incident['phase']='verified'
                elif change=='incident_boot':incident['observed_boot']='wrong'
                elif change=='incident_proof':incident['proof']={'cleanup_verified':False}
                elif change=='job_incident':f.runner._update('music-job',recovery_incident='wrong')
                elif change=='job_proof':f.runner._update('music-job',recovery_proof={})
                elif change=='job_recovery_status':f.runner._update('music-job',recovery_status='operator_required')
                elif change=='cleanup':audit['proof']['cleanup_verified']=False
                elif change=='verified_boot':audit['proof']['verified_boot']='wrong'
                elif change=='readiness':audit['proof']['idle_ready']=False
                elif change=='duplicate_unrelated':state['recoveries']=[dict(audit,incident_id='unrelated')]
                else:audit[change]='wrong'
                f.store.write('ownership.json',state)
                self.assertEqual(self.get(f,'/result')[0],409);patch.stopall()

    def test_audit_does_not_override_active_owner_or_supervision(self):
        for mode in ('owner','lease_alias','future'):
            with self.subTest(mode=mode):
                f=self.crash_terminal();self.local(f)
                if mode=='future':f.runner.futures['music-job']=concurrent.futures.Future()
                else:
                    state=f.a.snapshot();owner=copy.deepcopy(f.lease.owner)
                    if mode=='lease_alias':owner['job_id']='other-name'
                    state['owners']['gpu0']=owner;f.store.write('ownership.json',state)
                self.assertEqual(self.get(f,'/result')[0],409);patch.stopall()

    def test_normal_receipt_and_failed_recovery_are_get_only_and_backwards_readable(self):
        for mode in ('normal','local'):
            with self.subTest(mode=mode):
                f=Fixture(self)
                if mode=='normal':f.run();f.success()
                else:f=self.crash_terminal();self.local(f)
                before={p:p.read_bytes() for p in f.store.root.rglob('*') if p.is_file()}
                for _ in range(2):
                    result=self.client(f,[]).result('music-job')
                    self.assertEqual(result['state'],'success' if mode=='normal' else 'failed')
                    self.assertEqual(f.runner.artifact('music-job')[0],b'ID3offline')
                self.assertEqual(before,{p:p.read_bytes() for p in f.store.root.rglob('*') if p.is_file()})
                path=Path(__file__).resolve().parents[1]/'qualification/baseline-core.py'
                spec=importlib.util.spec_from_file_location('aihub_gpu_runner.review_compat_core',path)
                old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
                reader=object.__new__(old.Runner);reader.store=f.store;reader.futures={};reader.admission=f.a
                self.assertEqual(reader.result('music-job')['state'],'success' if mode=='normal' else 'failed')
                self.assertEqual(reader.artifact('music-job')[0],b'ID3offline')
                self.assertEqual(before,{p:p.read_bytes() for p in f.store.root.rglob('*') if p.is_file()})
                patch.stopall()

    def test_missing_corrupt_result_or_artifact_does_not_fabricate_output(self):
        for mode in ('missing_result','corrupt_result','missing_artifact','changed_artifact'):
            with self.subTest(mode=mode):
                f=self.crash_terminal();self.local(f)
                if mode=='missing_result':
                    f.store.path('results/music-job.json').unlink()
                    result=json.loads(self.get(f,'/result')[2])
                    self.assertEqual(result['state'],'failed');self.assertIsNone(result['value'])
                    self.assertFalse(result.get('artifact_available'));self.assertEqual(self.get(f,'/artifact')[0],404)
                elif mode=='corrupt_result':
                    f.store.write_bytes('results/music-job.json',b'{invalid')
                    self.assertEqual(self.get(f,'/result')[0],503);self.assertEqual(self.get(f,'/artifact')[0],503)
                elif mode=='missing_artifact':
                    f.store.path('results/music-job.bin').unlink();self.assertEqual(self.get(f,'/artifact')[0],503)
                else:
                    # Existing artifact store has no persisted digest. Reads must
                    # expose exact retained bytes, never invent a repaired MP3.
                    f.store.write_bytes('results/music-job.bin',b'damaged-offline-bytes')
                    self.assertEqual(f.runner.result('music-job')['state'],'failed')
                    self.assertEqual(self.get(f,'/artifact')[2],b'damaged-offline-bytes')
                self.assertEqual(f.free,1);patch.stopall()

    def test_diagnostics_after_aggregate_and_terminal_rechecks_survive_core(self):
        for stage in ('prepare','verify','recheck','release_binding'):
            with self.subTest(stage=stage):
                f=Fixture(self);f.memory=lambda:422
                with patch.object(MusicReleaseGuard,stage,side_effect=RecoveryRequired('music_release_process_birth_changed')):
                    f.run()
                f.failed();self.assertEqual(f.store.job('music-job')['error_code'],'music_release_process_birth_changed')
                self.assertEqual(json.loads(self.get(f)[2])['error_code'],'music_release_process_birth_changed')
                self.assertEqual(f.free,0 if stage=='prepare' else 1);patch.stopall()

    def test_unknown_or_ambiguous_diagnostics_are_generic_and_secret_free(self):
        secret='OFFLINE-SENTINEL-DO-NOT-PUBLISH'
        for stage in ('backend','verify','release_binding','transport','storage'):
            with self.subTest(stage=stage):
                f=Fixture(self);f.memory=lambda:422
                if stage=='backend':f.on_ack=lambda:(_ for _ in ()).throw(RecoveryRequired(secret))
                elif stage=='transport':f.on_ack=lambda:(_ for _ in ()).throw(Uncertain(secret))
                elif stage=='storage':f.on_ack=lambda:(_ for _ in ()).throw(OSError(secret))
                else:
                    p=patch.object(MusicReleaseGuard,stage,side_effect=RecoveryRequired(secret));p.start()
                job=f.run();f.failed();self.assertEqual(f.free,1)
                self.assertNotIn(secret,json.dumps(job));self.assertNotIn(secret,self.get(f)[2].decode())
                self.assertIn(job['error_code'],('comfy_cleanup_uncertain','supervision_or_storage_failure','ownership_release_unconfirmed'))
                patch.stopall()
        self.assertIsNone(failure_code(RecoveryRequired('gpu_health_requires_operator_gpu0 secret')))
        self.assertIsNone(failure_code(OSError('gpu_health_requires_operator_gpu0')))

    def test_non_music_cleanup_diagnostic_contract_unchanged(self):
        f=Fixture(self)
        def fail(*args):raise RecoveryRequired('gpu_health_requires_operator_gpu0')
        f.backend._queue_empty=fail
        with self.assertRaisesRegex(CleanupPending,'comfy_cleanup_uncertain'):
            f.backend.cleanup(None,60)

    def test_all_allowlisted_hard_causes_are_fixed_and_reach_http(self):
        from aihub_gpu_runner.music_cleanup import _HARD_FAILURES
        for reason in sorted(_HARD_FAILURES):
            with self.subTest(reason=reason):
                f=Fixture(self)
                f.on_ack=lambda:(_ for _ in ()).throw(RecoveryRequired(reason))
                f.run();f.failed();self.assertEqual(f.free,1)
                self.assertEqual(json.loads(self.get(f)[2])['error_code'],reason)
                patch.stopall()
        class NoString(RecoveryRequired):
            def __str__(self):raise RuntimeError('must not format unknown exceptions')
        self.assertIsNone(failure_code(NoString({'private':'unpublished'})))


if __name__=='__main__':unittest.main()
