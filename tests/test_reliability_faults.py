"""Permanent acceptance regressions for independently demonstrated failures.

Run only in a network-isolated local Linux copy. Hardware/process observations
are fake. Core scheduling/recovery uses real disposable AtomicStore journals.
All transports and process observations remain offline.
"""
import concurrent.futures, contextvars, copy, errno, json, pathlib, threading, time, types, unittest
from unittest.mock import patch
from aihub_gpu_runner.core import Runner, Conflict, RecoveryRequired, Lease
from aihub_gpu_runner.api import Application
from aihub_gpu_runner.client import JobClient, PendingJob
from aihub_gpu_runner.client import OutputSlot, OutputBusy, CURRENT_OPERATION, KnownJobFailure, CoordinationError
from aihub_gpu_runner.recovery import ProcessDisappeared
from tests.test_r1 import CoreTests, FakeBackend
from tests.test_ollama_exit_races import ExitRaceTests
from tests.test_music_release_review import MusicReviewTests


EVIDENCE=pathlib.Path(__file__).resolve().parents[1]/'qualification/reliability'

class CensusCounterexamples(unittest.TestCase):
    def setUp(self):
        self.r=ExitRaceTests('runTest');self.r.setUp();self.addCleanup(self.r.doCleanups);self.c=self.r.c

    def test_omitted_original_worker_reused_birth_retains_owner(self):
        self.c.prepare()
        self.r.tops['ollama'].remove('304');self.r.live['304']='999'
        with self.assertRaisesRegex(RecoveryRequired,'process_birth_changed'):self.c.guard.verify(60)
        self.assertIn('gpu1',self.c.state['owners'])

    def test_omitted_original_same_birth_or_unreadable_is_still_fatal(self):
        self.c.prepare();self.r.tops['ollama'].remove('304')
        with self.assertRaisesRegex(RecoveryRequired,'unverified_process_disappearance'):self.c.guard.verify(60)
        def denied(pid):
            if pid=='304':raise PermissionError(errno.EACCES,'private')
        self.r.stat_hook=denied
        with self.assertRaises(PermissionError):self.c.guard.verify(60)
        self.assertIn('gpu1',self.c.state['owners'])

    def test_gpu1_unknown_and_other_service_residency_remain_fatal(self):
        from tests.test_ollama_physical_cleanup import GPU1
        self.c.prepare()
        for row,reason in [('999,0','unknown_or_duplicate'),('202,96','baseline_music')]:
            with self.subTest(row=row):
                self.c.apps=lambda:GPU1+','+row+'\n'
                with self.assertRaisesRegex(RecoveryRequired,reason):self.c.guard.verify(60)
                self.assertIn('gpu1',self.c.state['owners'])

    def test_other_service_gpu1_pid_requires_repeated_container_membership(self):
        from tests.test_ollama_physical_cleanup import GPU1
        self.c.prepare();self.r.tops['music'].append('205');self.r.live['205']='55'
        self.c.apps=lambda:GPU1+',205,0\n'
        self.r.gpu_hook=lambda:self.r.tops['music'].remove('205')
        with self.assertRaisesRegex(RecoveryRequired,'process_birth_changed'):self.c.guard.verify(60)
        self.assertIn('gpu1',self.c.state['owners'])

    def test_omitted_original_worker_requires_positive_exit_and_fresh_samples(self):
        self.c.prepare();self.r.exit('304')
        proof=self.c.guard.verify(60)
        self.assertEqual(proof['stable_samples'],3)
        self.assertEqual(proof['observation']['exited_ollama_processes']['304'],'40')
        self.assertTrue(self.r.observations()[0]['process_snapshot_discarded'])

    def test_independent_gpu0_helper_exit_does_not_abort_gpu1_cleanup(self):
        self.c.prepare();self.r.tops['music'].append('205');self.r.live['205']='55'
        def exit_child(service):
            if service=='music':self.r.top_hook=None;self.r.exit('205')
        self.r.top_hook=exit_child
        self.assertEqual(self.c.guard.verify(60)['stable_samples'],3)
        self.assertIn('gpu1',self.c.state['owners'])

    def test_ollama_result_is_fenced_until_owner_removal(self):
        runner,lease=self.r.worker();release=lease.release.side_effect;seen=[]
        def observe(proof,**kw):
            self.assertIn('gpu1',self.c.state['owners'])
            with self.assertRaises(Conflict):runner.result('owned-job')
            seen.append(True)
            release(proof,**kw)
        lease.release.side_effect=observe;runner._work('owned-job',lease)
        self.assertTrue(seen[0])

    def test_expired_ollama_publication_retains_owner(self):
        runner,lease=self.r.worker();write=self.c.store.write
        def slow(name,value):
            write(name,value)
            if name=='results/owned-job.json':self.c.clock.t=1000
        self.c.store.write=slow;runner._work('owned-job',lease)
        self.assertEqual(self.c.store.job('owned-job')['state'],'cleanup_pending')
        lease.release.assert_not_called();self.assertIn('gpu1',self.c.state['owners'])

    def test_deadline_crossed_inside_release_never_clears_owner(self):
        runner,lease=self.r.worker()
        def delayed(proof,before_release=None,validate_clear=None):
            before_release();self.c.clock.t=1000;validate_clear()
            self.fail('expired release passed local commit checks')
        lease.release.side_effect=delayed;runner._work('owned-job',lease)
        self.assertEqual(self.c.store.job('owned-job')['state'],'cleanup_pending')
        self.assertIn('gpu1',self.c.state['owners'])
        with self.assertRaises(Conflict):runner.result('owned-job')

    def test_after_terminal_write_crash_keeps_ollama_result_fenced(self):
        from tests.test_automatic_recovery import Crash
        runner,lease=self.r.worker();write=self.c.store.write
        def crash(name,value):
            write(name,value)
            if name=='jobs/owned-job.json' and value.get('state')=='success':raise Crash('offline terminal crash')
        self.c.store.write=crash
        with self.assertRaises(Crash):runner._work('owned-job',lease)
        self.assertIn('gpu1',self.c.state['owners'])
        with self.assertRaises(Conflict):runner.result('owned-job')

    def test_ollama_clear_failure_before_effect_keeps_output_fenced(self):
        runner,lease=self.r.worker()
        def fail(proof,**kw):
            kw['before_release']();kw['validate_clear']();raise OSError('before clear')
        lease.release.side_effect=fail;runner._work('owned-job',lease)
        self.assertEqual(self.c.store.job('owned-job')['state'],'cleanup_pending')
        self.assertIn('gpu1',self.c.state['owners'])
        with self.assertRaises(Conflict):runner.result('owned-job')

    def test_ollama_clear_failure_after_effect_preserves_retrievable_output(self):
        runner,lease=self.r.worker();release=lease.release.side_effect
        def fail(proof,**kw):release(proof,**kw);raise OSError('after clear')
        lease.release.side_effect=fail;runner._work('owned-job',lease)
        self.assertFalse(self.c.state['owners'])
        result=runner.result('owned-job');self.assertEqual(result['state'],'success')
        self.assertEqual(result['publication_warning'],'ownership_release_sync_unconfirmed')


class DurableCounterexamples(unittest.TestCase):
    def setUp(self):
        self.c=CoreTests('runTest');self.c.setUp();self.addCleanup(self.c.tearDown)

    def test_recovery_audit_clear_crash_has_idempotent_failed_finalization(self):
        backend=FakeBackend(cleanup_error=__import__('aihub_gpu_runner.backends',fromlist=['CleanupPending']).CleanupPending('offline'))
        r=self.c.runner({'ollama':backend,'wallpaper':FakeBackend()})
        self.c.submit(r,'interrupted');r.future('interrupted').result(2)
        owner=self.c.gate.snapshot()['owners']['gpu1']
        evidence=dict({k:owner[k] for k in ('job_id','request_hash','lease_id','target')},approval_ref='offline',operator_approved_backend_recovery=True,backend_quiescent=True,cleanup_verified=True)
        self.c.gate.recover('interrupted',evidence)  # Crash before CLI put_job.
        self.assertFalse(self.c.gate.snapshot()['owners'])
        self.assertEqual(len(self.c.gate.snapshot()['recoveries']),1)
        self.assertEqual(r.status('interrupted')['state'],'cleanup_pending')
        with self.assertRaises(Conflict):r.result('interrupted')
        self.c.gate.recover('interrupted',evidence)
        self.c.gate.finalize_recoveries('interrupted')
        self.assertEqual(r.result('interrupted')['state'],'failed')
        self.assertEqual(len(self.c.gate.snapshot()['recoveries']),1)
        self.assertEqual(self.c.submit(r,'interrupted')['state'],'failed')
        self.assertEqual(backend.calls,1)
        r.close();r2=self.c.runner({'ollama':backend,'wallpaper':FakeBackend()})
        self.assertEqual(r2.status('interrupted')['error_code'],'operator_recovered_no_automatic_resubmission')
        self.assertEqual(backend.calls,1)  # Safe no replay, wrong durable disposition.

    def test_acquisition_expiry_during_gate_prevents_execution(self):
        r=self.c.runner(timeout=.05);acquire=self.c.gate.try_acquire;entered=threading.Event();resume=threading.Event()
        self.c.release_events.append(resume)
        def delayed(*args,**kwargs):
            entered.set();self.assertTrue(resume.wait(2));return acquire(*args,**kwargs)
        self.c.gate.try_acquire=delayed
        self.c.submit(r,'expired');self.assertTrue(entered.wait(1));time.sleep(.08);resume.set()
        r.future('expired').result(2)
        self.assertEqual(r.backends['ollama'].calls,0)
        self.assertEqual(r.status('expired')['error_code'],'resource_acquisition_timeout')

    def test_releasing_held_owner_admits_still_waiting_client_without_new_post(self):
        held=self.c.gate.try_acquire('held','hash',('gpu1',),'ollama');held.close()
        r=self.c.runner(timeout=1);self.c.submit(r,'waiting')
        evidence=dict({k:held.owner[k] for k in ('job_id','request_hash','lease_id','target')},approval_ref='offline',operator_approved_backend_recovery=True,backend_quiescent=True,cleanup_verified=True)
        self.c.gate.recover('held',evidence)
        r.future('waiting').result(2);self.assertEqual(r.backends['ollama'].calls,1)

    def test_cancel_during_gate_acquisition_prevents_gpu_execution(self):
        r=self.c.runner();acquire=self.c.gate.try_acquire;entered=threading.Event();resume=threading.Event();self.c.release_events.append(resume)
        def delayed(*args,**kwargs):entered.set();self.assertTrue(resume.wait(2));return acquire(*args,**kwargs)
        self.c.gate.try_acquire=delayed
        self.c.submit(r,'cancel');self.assertTrue(entered.wait(1));self.assertTrue(r.cancel('cancel'));resume.set()
        r.future('cancel').result(2)
        self.assertEqual(r.backends['ollama'].calls,0);self.assertFalse(self.c.gate.snapshot()['owners'])

    def test_client_timeout_blocks_next_writer_until_original_job_settles(self):
        held=self.c.gate.try_acquire('held','hash',('gpu1',),'ollama');held.close()
        r=self.c.runner(timeout=1);app=Application(r,'offline-token-long-enough');calls=[]
        class Transport:
            def request(self,method,route,payload=None):
                from aihub_gpu_runner.core import encode
                calls.append((method,route));return app.handle(method,route,{'Authorization':'Bearer offline-token-long-enough'},b'' if payload is None else encode(payload))
        client=JobClient(Transport(),wait_timeout=.02,poll_interval=.003)
        slot=OutputSlot(self.c.root/'client',[self.c.root/'out'],job_client_factory=lambda:client);op=slot.begin('render','old-op')
        token=CURRENT_OPERATION.set(op)
        try:
            with self.assertRaises(PendingJob):client.run('detached','ollama','generate',{'model':'synthetic','prompt':'neutral'})
        finally:CURRENT_OPERATION.reset(token)
        op.finish(error='runner still waiting')
        with self.assertRaises(OutputBusy):slot.begin('render','new-op')
        self.assertEqual(r.status('detached')['state'],'waiting')
        self.c.gate.recover('held',dict({k:held.owner[k] for k in ('job_id','request_hash','lease_id','target')},approval_ref='offline',operator_approved_backend_recovery=True,backend_quiescent=True,cleanup_verified=True))
        r.future('detached').result(2);self.assertEqual(r.backends['ollama'].calls,1)
        new=slot.begin('render','new-op');new.finish(error='offline no work')
        self.assertEqual(len([x for x in calls if x[0]=='POST']),1)

    def test_disposition_corruption_cannot_finalize_or_publish(self):
        from aihub_gpu_runner.backends import CleanupPending
        r=self.c.runner({'ollama':FakeBackend(cleanup_error=CleanupPending('offline')),'wallpaper':FakeBackend()})
        self.c.submit(r,'blocked');r.future('blocked').result(2)
        owner=self.c.gate.snapshot()['owners']['gpu1']
        evidence=dict({k:owner[k] for k in ('job_id','request_hash','lease_id','target')},approval_ref='offline',operator_approved_backend_recovery=True,backend_quiescent=True,cleanup_verified=True)
        self.c.gate.recover('blocked',evidence);saved=self.c.gate.snapshot()
        for mode in ['audit_missing','hash','lease','resources','owner','job_changed']:
            with self.subTest(mode=mode):
                state=copy.deepcopy(saved);record=state['recovery_dispositions'][owner['lease_id']]
                job=self.c.store.job('blocked');original=copy.deepcopy(job)
                if mode=='audit_missing':state['recoveries']=[]
                elif mode=='hash':record['audit_sha256']='wrong'
                elif mode=='lease':record['owner']['lease_id']='other'
                elif mode=='resources':record['resources']=['gpu0']
                elif mode=='owner':state['owners']['gpu1']=owner
                else:job['phase']='unexpected';self.c.store.put_job(job)
                self.c.store.write('ownership.json',state)
                with self.assertRaises(RecoveryRequired):self.c.gate.finalize_recoveries('blocked')
                self.assertEqual(self.c.store.job('blocked')['state'],'cleanup_pending')
                self.c.store.put_job(original)
        self.c.store.write('ownership.json',saved);self.c.gate.finalize_recoveries('blocked')
        self.assertEqual(r.result('blocked')['state'],'failed')

    def test_recovery_after_effect_write_failure_finalizes_once_without_new_audit(self):
        from aihub_gpu_runner.backends import CleanupPending
        r=self.c.runner({'ollama':FakeBackend(cleanup_error=CleanupPending('offline')),'wallpaper':FakeBackend()})
        self.c.submit(r,'io');r.future('io').result(2);owner=self.c.gate.snapshot()['owners']['gpu1']
        evidence=dict({k:owner[k] for k in ('job_id','request_hash','lease_id','target')},approval_ref='offline',operator_approved_backend_recovery=True,backend_quiescent=True,cleanup_verified=True)
        write=self.c.store.write
        def fail(name,value):
            write(name,value)
            if name=='ownership.json' and value.get('recovery_dispositions'):raise OSError('after replacement')
        with patch.object(self.c.store,'write',side_effect=fail),self.assertRaises(OSError):self.c.gate.recover('io',evidence)
        self.c.gate.recover('io',evidence);self.c.gate.finalize_recoveries('io');self.c.gate.finalize_recoveries('io')
        self.assertEqual(len(self.c.gate.snapshot()['recoveries']),1)
        self.assertEqual(r.result('io')['state'],'failed');self.assertEqual(r.backends['ollama'].calls,1)

    def test_claim_crash_before_job_lease_write_has_failed_only_disposition(self):
        # Claim is durably written before the worker can bind its job file.
        self.c.store.put_job({'request_id':'claimed','request_hash':'hash','target':'ollama',
                             'resources':['gpu1'],'state':'waiting','error_code':None})
        lease=self.c.gate.try_acquire('claimed','hash',('gpu1',),'ollama');lease.close()
        evidence=dict({k:lease.owner[k] for k in ('job_id','request_hash','lease_id','target')},approval_ref='offline',operator_approved_backend_recovery=True,backend_quiescent=True,cleanup_verified=True)
        self.c.gate.recover('claimed',evidence);self.c.gate.finalize_recoveries('claimed')
        job=self.c.store.job('claimed');self.assertEqual(job['state'],'failed')
        self.assertEqual(job['error_code'],'operator_recovered_no_automatic_resubmission')
        self.assertFalse(self.c.gate.snapshot()['owners'])


class MusicDispositionCounterexample(unittest.TestCase):
    def test_previous_boot_music_without_normal_receipt_has_failed_disposition(self):
        helper=MusicReviewTests('runTest');f=helper.crash_terminal();helper.automatic(f)
        self.addCleanup(helper.doCleanups)
        self.assertFalse(f.a.snapshot()['owners'])
        self.assertEqual(f.store.job('music-job')['state'],'failed')
        self.assertEqual(helper.get(f,'/result')[0],200)
        with self.assertRaisesRegex(ValueError,'no_matching_owner'):f.a.recover('music-job',helper.local_evidence(f))
        self.assertFalse(f.a.snapshot().get('music_release_receipts'))


class ApplicationCounterexamples(unittest.TestCase):
    def test_retained_ollama_failure_raises_attention(self):
        from aihub_gpu_runner.recovery_summary import summarize_recovery
        state={'owners':{'gpu1':{'job_id':'held','lease_id':'lease','target':'ollama'}},'boot_recovery_status':'ready'}
        job={'request_id':'held','state':'reconciling','error_code':'ollama_cleanup_unverified_process_disappearance'}
        self.assertTrue(summarize_recovery(state,[job])['operator_required'])
        self.assertFalse(summarize_recovery(state,[dict(job,state='cleanup_pending',error_code=None)])['operator_required'])

    def test_uncertain_post_is_durably_held_and_never_automatically_replayed(self):
        import tempfile
        calls=[]
        class Transport:
            def request(self,method,route,payload=None):calls.append(method);raise OSError('lost POST or GET')
        client=JobClient(Transport(),wait_timeout=.1)
        with tempfile.TemporaryDirectory() as tmp:
            slot=OutputSlot(tmp,[pathlib.Path(tmp)/'out'],job_client_factory=lambda:client)
            op=slot.begin('render','old');token=CURRENT_OPERATION.set(op)
            try:
                with self.assertRaises(PendingJob):client.run('stage-x','ollama','generate',{'model':'synthetic'})
            finally:CURRENT_OPERATION.reset(token)
            op.finish(error='uncertain')
            with self.assertRaises(OutputBusy):slot.begin('render','new')
            self.assertEqual(calls,['POST','GET'])
            self.assertFalse(slot.store.read('operations/old.json')['runner_jobs']['stage-x']['settled'])

    def test_failed_intent_write_never_sends_post(self):
        import tempfile
        transport=types.SimpleNamespace(request=unittest.mock.Mock())
        with tempfile.TemporaryDirectory() as tmp:
            slot=OutputSlot(tmp,[pathlib.Path(tmp)/'out']);op=slot.begin('render','old');token=CURRENT_OPERATION.set(op)
            try:
                with patch.object(slot.store,'write',side_effect=OSError('disk')),self.assertRaises(OSError):
                    JobClient(transport).run('stage-x','ollama','generate',{'model':'synthetic'})
            finally:CURRENT_OPERATION.reset(token);op.finish(error='not sent')
            transport.request.assert_not_called()






    def test_client_trickle_is_bounded_by_total_deadline(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        from aihub_gpu_runner.client import APITransport
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                payload=b'{"ok":true}'
                self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers()
                for byte in payload:
                    try:self.wfile.write(bytes([byte]));self.wfile.flush();time.sleep(.03)
                    except (BrokenPipeError,ConnectionResetError):return
            def log_message(self,*args):pass
            do_POST=do_GET
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=APITransport('http://127.0.0.1:'+str(server.server_port),'offline-only',timeout=.1)
            for method in ('GET','POST'):
                with self.subTest(method=method):
                    start=time.monotonic()
                    with self.assertRaises(OSError):client.request(method,'/status')
                    self.assertLess(time.monotonic()-start,.5)
        finally:server.shutdown();server.server_close();thread.join(2)


if __name__=='__main__':unittest.main(verbosity=2)

def load_tests(loader,tests,pattern):
    suite=unittest.TestSuite()
    for cls in (CensusCounterexamples,DurableCounterexamples,MusicDispositionCounterexample,ApplicationCounterexamples):suite.addTests(loader.loadTestsFromTestCase(cls))
    return suite
