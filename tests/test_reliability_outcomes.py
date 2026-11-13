"""Desired reliability outcomes. GPU telemetry is synthetic; no GPU access."""
import copy
import errno
import time
import unittest
from unittest.mock import patch

from aihub_gpu_runner.backends import CleanupPending
from aihub_gpu_runner.core import RecoveryRequired, retained_completed_ollama
from aihub_gpu_runner.ollama_cleanup import OllamaCleanupGuard
from aihub_gpu_runner.recovery import ProcessDisappeared
from tests import test_ollama_physical_cleanup as fixture
from tests.test_idle_recovery import IdleFixture


class CleanupOutcomes(unittest.TestCase):
    def setUp(self):
        self.f=fixture.PhysicalCleanupTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.memory=lambda:1


    def test_duplicate_gpu_row_is_named_and_retained(self):
        self.f.prepare();self.f.apps=lambda:2*(fixture.GPU1+',303,0\n')
        with self.assertRaisesRegex(RecoveryRequired,'unknown_or_duplicate'):self.f.guard.verify(2)
        self.assertEqual(self.f.guard.failure(RecoveryRequired('ollama_cleanup_unknown_or_duplicate_gpu_process'))['pid'],'303')

    def test_final_checks_resample_without_repeating_backend_effects(self):
        self.f.prepare();original=self.f.guard.sample;calls=0
        from aihub_gpu_runner.ollama_cleanup import _ProcessPending
        def sample(deadline):
            nonlocal calls
            calls+=1
            if calls==1:raise _ProcessPending()
            return original(deadline)
        self.f.guard.sample=sample
        proof=self.f.guard.commit_check(2)
        self.assertEqual(proof['stable_samples'],3);self.assertEqual(calls,4)
        self.assertFalse(any(c[0]=='POST' for c in self.f.calls))

    def test_each_final_boundary_preserves_precise_cause_and_owned_result_fence(self):
        for boundary in (1,2,3):
            with self.subTest(boundary=boundary):
                f=fixture.PhysicalCleanupTests('runTest');f.setUp()
                try:
                    f.memory=lambda:1;runner,lease,_=f.worker();count=0
                    original=OllamaCleanupGuard.commit_check
                    def check(guard,*a,**kw):
                        nonlocal count
                        count+=1
                        if count==boundary:
                            guard.mark_phase('compute_apps','ollama','999')
                            raise RecoveryRequired('ollama_cleanup_unknown_or_duplicate_gpu_process')
                        return original(guard,*a,**kw)
                    with patch.object(OllamaCleanupGuard,'commit_check',check):runner._work('owned-job',lease)
                    job=f.store.job('owned-job')
                    self.assertEqual(job['error_code'],'ollama_cleanup_unknown_or_duplicate_gpu_process')
                    self.assertEqual(job['ollama_cleanup_failure']['pid'],'999')
                    self.assertIn('gpu1',f.state['owners'])
                    self.assertEqual(len([c for c in f.calls if c[0]=='POST']),2)
                    with self.assertRaises(Exception):runner.result('owned-job')
                finally:f.doCleanups()


class RetainedHandoverOutcomes(unittest.TestCase):
    def setUp(self):
        self.f=IdleFixture(self)
        from aihub_gpu_runner.recovery import digest
        state=self.f.a.snapshot();state['recovery_policy']['policy']['scope']=state['scope']
        state['recovery_policy']['policy_id']=digest(state['recovery_policy']['policy'])
        self.f.observer.p=state['recovery_policy']['policy']
        self.f.job['ollama_cleanup_context']['policy_id']=state['recovery_policy']['policy_id']
        self.f.store.write('ownership.json',state);self.f.store.put_job(self.f.job)
        self.f.before=copy.deepcopy(state)

    def test_completed_retained_job_is_preserved_without_any_authority_call(self):
        before=self.f.store.job(self.f.job['request_id'])
        self.assertTrue(retained_completed_ollama(self.f.a,before,self.f.a.snapshot()['owners']))
        self.assertEqual(self.f.store.job(before['request_id']),before)
        self.f.calls.assert_not_called()

    def test_drift_or_incomplete_execution_does_not_enter_preservation_class(self):
        for key,value in [('phase','submit_intent'),('completion_verified',False),('boot_id','changed'),('lease_id','changed'),('host_stage',True)]:
            job=dict(self.f.job,**{key:value})
            self.assertFalse(retained_completed_ollama(self.f.a,job,self.f.a.snapshot()['owners']),key)

    def test_actual_runner_and_automatic_worker_preserve_retained_job_across_cpu_handover(self):
        from unittest.mock import Mock
        from aihub_gpu_runner.backends import Target
        from aihub_gpu_runner.core import Runner
        from aihub_gpu_runner.recovery import RecoveryManager,digest
        from tests.test_automatic_recovery import policy
        from tests.test_wallpaper_conditioning import WORKFLOW
        p=policy(self.f.a)
        p['services']['ollama']=dict(p['services']['music'],name='ollama',id='e'*64)
        p['boot_startup']='stopped_idle';p['stopped_idle_memory_mb']=dict(p['idle_memory_mb'])
        state=self.f.a.snapshot();state['recovery_policy']={'policy':p,'policy_id':digest(p)}
        self.f.store.write('ownership.json',state)
        job=copy.deepcopy(self.f.job);job['ollama_cleanup_context']['policy_id']=digest(p)
        job['error_code']='ollama_cleanup_unknown_or_duplicate_gpu_process';self.f.store.put_job(job)
        targets={'ollama':Target('ollama','ollama',('gpu1',),'http://offline.invalid',models=('synthetic',)),
                 'wallpaper':Target('wallpaper','comfy',('gpu0','gpu1'),'http://offline.invalid',
                     workflow=WORKFLOW,output_node='2',idle_memory_mb=20,conditioning_idle_memory_mb=10,lifecycle='on_demand')}
        backend=Mock();observer=Mock()
        # Real RecoveryManager startup/tick runs; no observer/backend may be used.
        for name in ('container','ready','queues','gpu_sample','start','signal'):
            getattr(observer,name).side_effect=AssertionError('backend activity during retained handover')
        recovery=RecoveryManager(self.f.a,targets,{k:backend for k in targets},observer=observer)
        before_job=self.f.store.path(self.f.store.job_name(job['request_id'])).read_bytes()
        before_state=self.f.store.path('ownership.json').read_bytes()
        self.f.supervisor.close()
        runner=Runner(self.f.a,targets,{k:backend for k in targets},recovery=recovery)
        try:
            time.sleep(.05)  # Allow the actual automatic worker its normal tick.
            self.assertEqual(self.f.store.path(self.f.store.job_name(job['request_id'])).read_bytes(),before_job)
            self.assertEqual(self.f.store.path('ownership.json').read_bytes(),before_state)
            self.assertFalse(runner.pending);self.assertFalse(runner.futures)
            self.assertEqual(backend.mock_calls,[]);self.assertEqual(observer.mock_calls,[])
        finally:runner.close()

    def test_unknown_history_manual_class_stays_disabled_despite_idle(self):
        job=dict(self.f.job,error_code='ollama_cleanup_unknown_or_duplicate_gpu_process')
        self.f.store.put_job(job)
        self.assertEqual(self.f.control.check('operator1')['failed_checks'],['unsupported_retained_failure_class'])
        self.f.calls.assert_not_called();self.assertEqual(self.f.proof_calls,0)

    def test_observation_timeout_is_a_specific_unavailable_result_not_lock_busy(self):
        def timeout(*a,**k):raise TimeoutError('private observer details')
        self.f.observer.stopped_ready=timeout
        result=self.f.control.check('operator1')
        self.assertEqual(result['classification'],'observation_unavailable')
        self.assertEqual(result['failed_checks'],['idle_recovery_observation_unavailable'])
        self.assertNotIn('private observer details',str(result));self.f.calls.assert_not_called()

    def test_final_proof_runs_under_both_original_physical_locks(self):
        from aihub_gpu_runner.core import NativeLock
        seen=[]
        def observe():
            if self.f.proof_calls==4:
                seen.append([not NativeLock(p,create=False).acquire(0) for p in self.f.a.lock_paths.values()])
        # locked_elsewhere uses the same-process thread lock conservatively.
        self.f.proof_hook=observe
        result=self.f.run()
        self.assertTrue(result['verified_release']);self.assertEqual(self.f.proof_calls,4)
        self.assertTrue(seen and all(seen[0]))


if __name__=='__main__':unittest.main()
