import concurrent.futures
import copy
import json
import threading
import unittest
from unittest.mock import Mock, patch
from aihub_gpu_runner.core import Conflict, NativeLock, Runner, encode
from aihub_gpu_runner.maintenance import FILE, IngressGate
from aihub_gpu_runner.recovery_control import Blocked, RecoveryControl
from aihub_gpu_runner.recovery_api import RecoveryApplication
from aihub_gpu_runner.api import Application
from tests.recovery_fixture import Fixture


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture(self)

    def test_read_only_check_has_zero_filesystem_or_authority_changes(self):
        before = {str(p):p.read_bytes() for p in self.f.store.root.rglob('*') if p.is_file()}
        check = self.f.control.check('operator1')
        self.assertTrue(check['eligible'],check)
        self.assertEqual({str(p):p.read_bytes() for p in self.f.store.root.rglob('*') if p.is_file()},before)
        self.f.calls.assert_not_called()

    def test_success_exact_authority_audit_paired_release_and_preservation(self):
        result = self.f.run()
        self.assertEqual(result['result'],'succeeded',result)
        self.f.calls.assert_called_once()
        self.assertEqual(self.f.a.snapshot()['owners'],{})
        self.assertEqual(len(self.f.a.snapshot()['recoveries']),2)
        self.assertEqual(self.f.a.snapshot()['automatic_recovery'],self.f.before['automatic_recovery'])
        self.assertEqual(self.f.store.job('fixture-wallpaper')['wallpaper_lifecycle'], self.f.job['wallpaper_lifecycle'])
        self.assertEqual(self.f.gate()['mode'],'open')
        self.assertEqual(self.f.store.job('fixture-wallpaper')['state'],'failed')
        self.assertTrue(all(result['verification'][k] for k in ('job_terminal','paired_release','audit_verified','normal_readiness')))

    def test_duplicate_execution_after_success_is_rejected(self):
        token = self.f.token(); self.f.run(token)
        with self.assertRaisesRegex(Blocked,'duplicate'):
            self.f.run(token)
        self.assertEqual(self.f.calls.call_count,1)

    def test_wrong_actor_tampering_expiry_and_clock_rollback(self):
        token = self.f.token()
        for actor,t,now in [('different',token,1000),('operator1',token+'0',1000),
                            ('operator1',token,1060),('operator1',token,999)]:
            with self.subTest(actor=actor,now=now):
                self.f.now=now
                with self.assertRaises(Blocked): self.f.control.recover(t,actor,True)
        self.assertEqual(self.f.gate()['attempts'],{})
        self.f.calls.assert_not_called()

    def test_confirmation_must_be_boolean_true(self):
        token=self.f.token()
        for value in (False,'true',1,None):
            with self.assertRaises(Blocked): self.f.control.recover(token,'operator1',value)
        self.f.calls.assert_not_called()

    def test_health_churn_does_not_invalidate_generation(self):
        token = self.f.token()
        for s in ('music','ollama'):
            self.f.observation['containers'][s]['State']['Health']['Log']=[{'End':'new-time','Output':'new-output'}]
        self.assertEqual(self.f.run(token)['result'],'succeeded')

    def test_unhealthy_stale_health_and_unknown_state_block(self):
        for field,value in [('Health',{'Status':'unhealthy','FailingStreak':1}),('Paused',True),('Unexpected',False)]:
            with self.subTest(field=field):
                original=copy.deepcopy(self.f.observation)
                self.f.observation['containers']['music']['State'][field]=value
                self.assertFalse(self.f.control.check('operator1')['eligible'])
                self.f.observation=original
        self.f.observation['containers']['music']['health_fresh']=False
        self.assertFalse(self.f.control.check('operator1')['eligible'])

    def test_stable_generation_drift_invalidates_ticket(self):
        token=self.f.token()
        for field in ('Id','Image','RestartCount','contract','pid_birth','netns','executable','address'):
            with self.subTest(field=field):
                original=copy.deepcopy(self.f.observation)
                self.f.observation['containers']['music'][field]='changed'
                with self.assertRaisesRegex(Blocked,'approved_state_changed'): self.f.run(token)
                self.f.observation=original
        self.f.calls.assert_not_called()

    def test_pid_and_start_time_drift(self):
        token=self.f.token()
        for field,value in [('Pid',234),('StartedAt','later')]:
            original=copy.deepcopy(self.f.observation)
            self.f.observation['containers']['music']['State'][field]=value
            with self.assertRaises(Blocked): self.f.run(token)
            self.f.observation=original

    def test_policy_job_boot_incident_and_lease_drift(self):
        token=self.f.token()
        original=copy.deepcopy(self.f.before)
        for mutate in (
            lambda s:s['recovery_policy'].update(approval_ref='new'),
            lambda s:s['owners']['gpu1'].update(lease_id='other'),
            lambda s:s['automatic_recovery']['fixture-incident'].update(error='other'),
            lambda s:s.update(verified_boot='other')):
            state=copy.deepcopy(original);mutate(state);self.f.store.write('ownership.json',state)
            with self.assertRaises(Blocked):self.f.run(token)
        self.f.store.write('ownership.json',original)
        job=copy.deepcopy(self.f.job);job['payload']['changed']=True;self.f.store.put_job(job)
        with self.assertRaises(Blocked):self.f.run(token)
        self.f.calls.assert_not_called()

    def test_prestart_allowlist_rejects_any_execution_intent(self):
        for field in ('start_intent','submission_intent','execution','stop_intent','unexpected'):
            job=copy.deepcopy(self.f.job);job['wallpaper_lifecycle'][field]={}
            self.f.store.put_job(job)
            self.assertIn('not_proven_never_started',self.f.control.check('operator1')['failed_checks'])

    def test_other_pending_job_and_active_future_block(self):
        self.f.store.put_job(dict(request_id='other',state='waiting'))
        self.assertIn('other_nonterminal_jobs',self.f.control.check('operator1')['failed_checks'])
        self.f.store.put_job(dict(request_id='other',state='failed'))
        self.f.runner.futures['other']=concurrent.futures.Future()
        self.assertIn('supervision_active',self.f.control.check('operator1')['failed_checks'])

    def test_physical_lock_replacement_blocks(self):
        token=self.f.token()
        p=self.f.a.lock_paths['gpu0'];q=p.with_suffix('.new');q.touch();q.replace(p)
        with self.assertRaisesRegex(Blocked,'physical_locks_changed'):self.f.run(token)

    def test_busy_physical_lock_prevents_authority_release(self):
        token=self.f.token()
        with NativeLock(self.f.a.lock_paths['gpu0'],create=False).held():
            result=self.f.run(token)
        self.assertEqual(result['result'],'operator_required')
        self.assertEqual(self.f.a.snapshot(),self.f.before)
        self.assertEqual(self.f.gate()['mode'],'closed')

    def test_backend_unavailable_is_not_eligible(self):
        self.f.observation['backend_available']=False
        self.assertIn('backend_unavailable',self.f.control.check('operator1')['failed_checks'])

    def test_backend_timeout_under_fence_retains_owners(self):
        token=self.f.token()
        def hook(fenced):
            if fenced:raise TimeoutError('synthetic backend unavailable')
        self.f.observe_hook=hook
        result=self.f.run(token)
        self.assertEqual(result['result'],'operator_required')
        self.f.calls.assert_not_called()
        self.assertEqual(self.f.gate()['mode'],'closed')
        self.assertEqual(self.f.a.snapshot(),self.f.before)

    def test_450_ceiling_preserved_475_does_not_pass(self):
        self.f.observation['proof']['observations']['service_memory_mb']['music']['gpu0']=475
        self.assertIn('music_residency_above_accepted_policy',self.f.control.check('operator1')['failed_checks'])

    def test_gpu_rebound_fault_busy_queue_and_manual_work_block(self):
        for mutate in (
            lambda o:o['proof']['observations']['gpus']['gpu0'].update(memory_mb=900),
            lambda o:o['proof']['observations']['driver_health'].update(reported_gpu_faults=True),
            lambda o:o['proof']['queues']['music'].update(queue_running=[1]),
            lambda o:o.update(manual_activity=True)):
            old=copy.deepcopy(self.f.observation);mutate(self.f.observation)
            self.assertFalse(self.f.control.check('operator1')['eligible'])
            self.f.observation=old

    def test_paused_ha_is_not_exclusion_proof(self):
        for path in self.f.observation['isolation']['coverage']:
            with self.subTest(path=path):
                self.f.observation['isolation']['coverage'][path]=False
                self.assertIn('producer_exclusion_incomplete',self.f.control.check('operator1')['failed_checks'])
                self.f.observation['isolation']['coverage'][path]=True
        self.f.observation['isolation']['unmanaged_clients']=['manual ComfyUI']
        self.assertFalse(self.f.control.check('operator1')['eligible'])

    def test_active_direct_comfyui_connection_blocks_and_later_activity_invalidates_ticket(self):
        token=self.f.token();ingress=IngressGate(self.f.a,'manual-comfyui')
        with ingress.connection():
            self.assertIn('client_connections_not_drained',self.f.control.check('operator1')['failed_checks'])
        with self.assertRaisesRegex(Blocked,'approved_state_changed'):self.f.run(token)
        self.assertEqual(self.f.run()['result'],'succeeded')

    def test_fence_excludes_api_submission_cancel_host_stage_and_manual_ingress(self):
        token=self.f.token()
        def hook(fenced):
            if not fenced:return
            with self.assertRaises(Conflict):Runner.submit(self.f.runner,'new','wallpaper','workflow',{})
            with self.assertRaises(Conflict):Runner.cancel(self.f.runner,'fixture-wallpaper')
            self.assertIsNone(self.f.a.try_acquire('manual-stage','hash',('gpu0',),'wallpaper'))
            with self.assertRaises(Conflict):
                with IngressGate(self.f.a,'manual-comfyui').connection(): pass
        self.f.observe_hook=hook
        self.assertEqual(self.f.run(token)['result'],'succeeded')

    def test_two_concurrent_confirmations_consume_once(self):
        token=self.f.token();barrier=threading.Barrier(2)
        def run():
            barrier.wait()
            try:return self.f.run(token)
            except (Blocked,TimeoutError):return {'result':'blocked'}
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda _:run(),range(2)))
        self.assertEqual(sum(r['result']=='succeeded' for r in results),1,results)
        self.assertEqual(self.f.calls.call_count,1)

    def test_concurrent_check_and_manual_connection_converges_fail_closed(self):
        token=self.f.token();entered=threading.Event();finish=threading.Event()
        def connect():
            with IngressGate(self.f.a,'manual-comfyui').connection():
                entered.set();finish.wait(3)
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            future=pool.submit(connect);self.assertTrue(entered.wait(2))
            with self.assertRaises(Blocked):self.f.run(token)
            finish.set();future.result()
        self.f.calls.assert_not_called()

    def test_gate_write_failure_before_effect_prevents_authority(self):
        token=self.f.token();original=self.f.store.write
        def write(name,value):
            if name==FILE and value['mode']=='closed':raise OSError('disk full')
            original(name,value)
        with patch.object(self.f.store,'write',side_effect=write):
            with self.assertRaises(OSError):self.f.run(token)
        self.f.calls.assert_not_called()
        self.assertEqual(self.f.gate()['mode'],'open')

    def test_authority_raises_before_commit_never_retried_or_aborted(self):
        token=self.f.token()
        self.f.calls.side_effect=OSError('uncertain write')
        result=self.f.run(token)
        self.assertEqual(result['result'],'operator_required')
        with self.assertRaises(Blocked):self.f.control.reconcile(result['operation'])
        self.assertFalse(self.f.control.check_abort('operator1')['eligible'])
        with self.assertRaises(Blocked):self.f.run(token)
        self.assertEqual(self.f.calls.call_count,1)

    def test_lost_authority_reply_reconciles_without_second_release(self):
        original=self.f.calls._mock_wraps
        def lost(*args):original(*args);raise TimeoutError('reply lost')
        self.f.calls.side_effect=lost
        result=self.f.run()
        self.assertEqual(result['result'],'operator_required')
        self.assertEqual(self.f.store.job('fixture-wallpaper')['state'],'reconciling')
        completed=self.f.control.reconcile(result['operation'])
        self.assertEqual(completed['result'],'succeeded')
        self.assertEqual(self.f.calls.call_count,1)

    def test_interruption_after_release_durable_fence_and_new_controller(self):
        original=self.f.calls._mock_wraps
        class Crash(BaseException):pass
        def crash(*args):original(*args);raise Crash()
        self.f.calls.side_effect=crash
        with self.assertRaises(Crash):self.f.run()
        operation=self.f.gate()['active']
        self.assertTrue(self.f.a.maintenance_blocked())
        fresh=RecoveryControl(self.f.runner,self.f.observer,b'synthetic-ticket-key-never-use-live-'*2,clock=lambda:self.f.now)
        self.assertEqual(fresh.reconcile(operation)['result'],'succeeded')
        self.assertEqual(self.f.calls.call_count,1)

    def test_interruption_before_authority_intent_allows_separately_verified_abort(self):
        token=self.f.token()
        def unavailable(fenced):
            if fenced:raise TimeoutError()
        self.f.observe_hook=unavailable
        result=self.f.run(token);self.f.observe_hook=None
        check=self.f.control.check_abort('operator1')
        self.assertTrue(check['eligible'],check)
        outcome=self.f.control.abort(check['token'],'operator1',True)
        self.assertEqual(outcome['result'],'aborted')
        self.assertEqual(self.f.a.snapshot(),self.f.before)
        self.assertEqual(self.f.gate()['mode'],'open')
        self.f.calls.assert_not_called()
        with self.assertRaises(Blocked):self.f.control.abort(check['token'],'operator1',True)

    def test_abort_stale_identity_retains_fence(self):
        self.f.observe_hook=lambda fenced: (_ for _ in ()).throw(TimeoutError()) if fenced else None
        self.f.run();self.f.observe_hook=None
        check=self.f.control.check_abort('operator1')
        self.f.observation['containers']['music']['RestartCount']=1
        with self.assertRaises(Blocked):self.f.control.abort(check['token'],'operator1',True)
        self.assertEqual(self.f.gate()['mode'],'closed')

    def test_job_finalize_failure_is_reconciled_only_after_audit(self):
        with patch.object(self.f.a,'finalize_recoveries',side_effect=OSError('disk full')):
            result=self.f.run()
        self.assertEqual(self.f.gate()['mode'],'closed')
        self.assertEqual(self.f.control.reconcile(result['operation'])['result'],'succeeded')
        self.assertEqual(self.f.calls.call_count,1)

    def test_final_readiness_failure_never_reports_success(self):
        def hook(fenced):
            if self.f.calls.call_count:raise TimeoutError('backend disappeared')
        self.f.observe_hook=hook
        result=self.f.run()
        self.assertEqual(result['result'],'operator_required')
        self.assertEqual(self.f.gate()['mode'],'closed')
        self.f.observe_hook=None
        self.assertEqual(self.f.control.reconcile(result['operation'])['result'],'succeeded')

    def test_artifact_or_history_drift_after_authority_retains_fence(self):
        original=self.f.calls._mock_wraps
        def corrupt(*args):
            original(*args)
            self.f.store.write_bytes('results/preserved.bin',b'changed')
        self.f.calls.side_effect=corrupt
        result=self.f.run()
        self.assertEqual(result['result'],'operator_required')
        with self.assertRaises(Blocked):self.f.control.reconcile(result['operation'])
        self.assertEqual(self.f.gate()['mode'],'closed')

    def test_missing_or_malformed_gate_blocks_restart_and_admission(self):
        for data in (b'{}',b'broken'):
            self.f.store.write_bytes(FILE,data)
            self.assertTrue(self.f.a.maintenance_blocked())
            self.assertFalse(self.f.control.status()['available'])
            self.assertFalse(self.f.control.check('operator1')['eligible'])
        self.f.store.path(FILE).unlink()
        self.assertTrue(self.f.a.maintenance_blocked())
        self.assertIsNone(self.f.a.try_acquire('test','hash',('gpu0',),'wallpaper'))

    def test_status_and_normal_api_report_fence(self):
        status=self.f.control.status()
        self.assertTrue(status['available'],status)
        self.assertEqual(status['affected_jobs'],['fixture-wallpaper'])
        self.assertTrue(status['operator_required'])
        self.f.observe_hook=lambda fenced: (_ for _ in ()).throw(TimeoutError()) if fenced else None
        self.f.run()
        app=Application(self.f.runner,'synthetic-token-0123456789')
        code,_,body=app.handle('GET','/resources',{'Authorization':'Bearer '+app.token})
        self.assertEqual(code,200);self.assertFalse(json.loads(body)['ready'])

    def test_authority_success_return_without_audit_is_not_success(self):
        self.f.calls.side_effect=lambda *args:None
        result=self.f.run()
        self.assertEqual(result['result'],'operator_required')
        self.assertIn('recovery_audit_not_exactly_once',result['failed_checks'])
        self.assertEqual(self.f.a.snapshot(),self.f.before)

    def test_partial_paired_release_never_reopens_access(self):
        original=self.f.calls._mock_wraps
        def corrupt(*args):
            original(*args)
            state=self.f.a.snapshot();state['owners']['gpu1']=self.f.before['owners']['gpu1']
            self.f.store.write('ownership.json',state)
        self.f.calls.side_effect=corrupt
        result=self.f.run()
        self.assertEqual(result['result'],'operator_required')
        self.assertTrue(self.f.a.maintenance_blocked())

    def test_container_drift_after_authority_keeps_access_closed(self):
        original=self.f.calls._mock_wraps
        def drift(*args):
            original(*args)
            self.f.observation['containers']['music']['Id']='different-generation'
        self.f.calls.side_effect=drift
        self.assertEqual(self.f.run()['result'],'operator_required')
        self.assertTrue(self.f.a.maintenance_blocked())

    def test_runner_restart_invalidates_unconsumed_confirmation(self):
        token=self.f.token()
        new=RecoveryControl(self.f.runner,self.f.observer,b'synthetic-ticket-key-never-use-live-'*2,clock=lambda:self.f.now)
        with self.assertRaises(Blocked):new.recover(token,'operator1',True)
        self.f.calls.assert_not_called()

    def test_missing_container_identity_is_not_eligible(self):
        del self.f.observation['containers']['music']['pid_birth']
        self.assertFalse(self.f.control.check('operator1')['eligible'])

    def test_lost_supervisor_authority_blocks(self):
        self.f.supervisor.close()
        self.assertIn('supervisor_authority_unavailable',self.f.control.check('operator1')['failed_checks'])


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.f=Fixture(self)
        self.app=RecoveryApplication(self.f.control,'read-fixture-'*5,'operate-fixture-'*5)
    def request(self,method,path,value=None,token=None,actor='operator1'):
        return self.app.handle(method,path,{'Authorization':'Bearer '+(token or self.app.operate_token),
                                           'X-HA-User':actor},encode(value) if value is not None else b'')
    def test_authentication_and_separate_privileges(self):
        self.assertEqual(self.request('GET','/status',token='wrong')[0],401)
        self.assertEqual(self.request('POST','/recover',{},token=self.app.read_token)[0],403)
        self.assertEqual(self.request('GET','/check',token=self.app.read_token)[0],200)
        self.assertEqual(self.request('GET','/status',actor='')[0],400)
    def test_arbitrary_commands_paths_evidence_and_release_are_absent(self):
        for path in ('/jobs','/release','/recover?command=restart','/commands'):
            self.assertEqual(self.request('POST',path,{})[0],404)
        self.assertEqual(self.request('POST','/recover',{'token':self.f.token(),'confirmed':True,'evidence':{}})[0],400)
        self.f.calls.assert_not_called()
    def test_api_success_and_duplicate_409(self):
        value={'token':self.f.token(),'confirmed':True}
        code,_,body=self.request('POST','/recover',value)
        self.assertEqual(code,200,body)
        self.assertEqual(json.loads(body)['result'],'succeeded')
        self.assertEqual(self.request('POST','/recover',value)[0],409)
    def test_malformed_oversized_duplicate_json_and_backend_errors(self):
        headers={'Authorization':'Bearer '+self.app.operate_token,'X-HA-User':'operator1'}
        self.assertEqual(self.app.handle('POST','/recover',headers,b'{}'*10000)[0],413)
        self.assertEqual(self.app.handle('POST','/recover',headers,b'{"confirmed":true,"confirmed":false}')[0],400)
        self.f.observer.observe=Mock(side_effect=OSError('PRIVATE'))
        _,_,body=self.request('GET','/check')
        self.assertNotIn(b'PRIVATE',body)
        self.assertFalse(json.loads(body)['eligible'])


if __name__=='__main__': unittest.main()
