import copy,errno,json,time,unittest
from concurrent.futures import Future,ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock,patch
from aihub_gpu_runner.core import NativeLock,RecoveryRequired
from aihub_gpu_runner.recovery import ProcessDisappeared
from aihub_gpu_runner.recovery_control import Blocked
from aihub_gpu_runner.recovery_checks import digest
from aihub_gpu_runner.idle_recovery import IdleRecovery
from aihub_gpu_runner.integrated_runtime import IntegratedApplication
from tests.recovery_fixture import Fixture

class IdleFixture(Fixture):
 def __init__(self,case):
  super().__init__(case)
  with NativeLock(self.store.path('practical-recovery.lock')).held():pass
  s=self.a.snapshot();s['practical_control']={'admission':'open','active':None,'attempts':{}}
  p=s['recovery_policy']['policy'];p['samples']=3;s['recovery_policy']['policy_id']=digest(p)
  owner=dict(s['owners']['gpu1'],target='ollama');s['owners']={'gpu1':owner}
  j=dict(self.job,target='ollama',resources=['gpu1'],phase='execution_complete',completion_verified=True,
      error_code='ollama_cleanup_new_or_reused_process')
  j.pop('wallpaper_lifecycle');j.pop('lifecycle_operator_error');j.pop('recovery_error')
  identities={k:dict(id=k,pid=100 if k=='ollama' else 0,running=k=='ollama',started_at='fixed',contract_sha256=k) for k in p['services']}
  j['ollama_cleanup_context']=dict(binding={k:owner[k] for k in ('job_id','lease_id','request_hash','target','boot_id')},resources=['gpu1'],
      owned_work_completed=True,policy_id=s['recovery_policy']['policy_id'],identities=identities,ollama_processes={'100':'10','101':'20'})
  j['ollama_cleanup_failure']={'pid':'102','phase':'compute_apps'}
  self.store.write('ownership.json',s);self.store.put_job(j);self.job=j;self.before=copy.deepcopy(s)
  self.proof_calls=0;self.proof_hook=None
  def container(service,deadline):return copy.deepcopy(identities[service])
  def stopped(*args,**kwargs):
   self.proof_calls+=1
   if self.proof_hook:self.proof_hook()
   return dict(stable_samples=3,idle_ready=True,queues={'ollama':{'models':[]},'music':{'queue_running':[],'queue_pending':[]}},observed_at='synthetic')
  self.observer=SimpleNamespace(p=p,container=container,processes=lambda *a:{'100':'10'},stopped_ready=stopped)
  def birth(pid):
   if pid=='100':return '10'
   raise ProcessDisappeared(pid,errno.ENOENT)
  self.birth=patch('aihub_gpu_runner.idle_recovery.process_birth',side_effect=birth);self.birth.start();case.addCleanup(self.birth.stop)
  self.control=IdleRecovery(self.runner,self.observer,b'synthetic-ticket-key-never-use-live-'*2,clock=lambda:self.now)
 def token(self):
  result=self.control.check('operator1');assert result['eligible'],result;return result['token']
 def run(self,token=None):return self.control.recover(token or self.token(),'operator1',True)

class IdleTests(unittest.TestCase):
 def setUp(self):self.f=IdleFixture(self)
 def test_exact_authority_failed_only_and_original_history(self):
  out=self.f.run();self.assertTrue(out['verified_release']);self.f.calls.assert_called_once()
  self.assertEqual(self.f.a.snapshot()['owners'],{});self.assertEqual(self.f.gate()['mode'],'open')
  self.assertEqual(self.f.store.job(self.f.job['request_id'])['error_code'],'operator_recovered_no_automatic_resubmission')
  self.assertEqual(self.f.a.snapshot()['recoveries'][:-1],self.f.before['recoveries'])
  self.assertEqual(self.f.proof_calls,3)
 def test_check_readonly(self):
  before={str(p):p.read_bytes() for p in self.f.store.root.rglob('*') if p.is_file()}
  self.f.token();self.assertEqual(before,{str(p):p.read_bytes() for p in self.f.store.root.rglob('*') if p.is_file()});self.f.calls.assert_not_called()
 def test_inactive_required(self):
  self.f.runner.futures['working']=Future()
  self.assertEqual(self.f.control.check('operator1')['failed_checks'],['runner_supervision_or_queue_active'])
 def test_stale_policy_and_request_rejected(self):
  token=self.f.token();j=self.f.store.job(self.f.job['request_id']);j['request_hash']='changed';self.f.store.put_job(j)
  with self.assertRaises(Blocked):self.f.run(token)
  self.f.calls.assert_not_called()
 def test_live_child_and_pid_reuse_block(self):
  self.f.observer.processes=lambda *a:{'100':'10','103':'new'}
  self.assertEqual(self.f.control.check('operator1')['failed_checks'],['ollama_child_process_present'])
  self.f.birth.stop();self.f.birth=patch('aihub_gpu_runner.idle_recovery.process_birth',return_value='reused');self.f.birth.start();self.addCleanup(self.f.birth.stop)
  self.assertEqual(self.f.control.check('operator1')['failed_checks'],['ollama_parent_birth_changed'])
 def test_unknown_queues_and_failed_idle_proof_block(self):
  self.f.observer.stopped_ready=lambda *a:dict(stable_samples=2,idle_ready=True)
  self.assertEqual(self.f.control.check('operator1')['failed_checks'],['three_accepted_idle_samples_required'])
 def test_confirmation_actor_and_expiry(self):
  token=self.f.token()
  for confirmed in (False,1,'true'):
   with self.assertRaises(Blocked):self.f.control.recover(token,'operator1',confirmed)
  with self.assertRaises(Blocked):self.f.control.recover(token,'another',True)
  self.f.now+=61
  with self.assertRaises(Blocked):self.f.run(token)
  self.f.calls.assert_not_called()
 def test_concurrent_approval_executes_at_most_once(self):
  token=self.f.token()
  def run():
   try:return self.f.run(token)
   except (Blocked,TimeoutError):return {'blocked':True}
  with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:run(),range(2)))
  self.assertEqual(sum(bool(v.get('verified_release')) for v in results),1);self.f.calls.assert_called_once()
 def test_commit_then_lost_response_verifies_without_recovery_retry(self):
  actual=self.f.a.recover
  def lost(*a):actual(*a);raise OSError('lost')
  self.f.a.recover=Mock(side_effect=lost);out=self.f.run();self.assertEqual(out['result'],'operator_required')
  self.assertEqual(self.f.gate()['mode'],'closed')
  result=self.f.control.reconcile(out['operation']);self.assertTrue(result['verified_release']);self.f.a.recover.assert_called_once()
 def test_unknown_before_commit_never_retries(self):
  self.f.a.recover=Mock(side_effect=OSError('unknown'))
  out=self.f.run();self.assertEqual(out['result'],'operator_required')
  with self.assertRaises(Blocked):self.f.control.reconcile(out['operation'])
  self.f.a.recover.assert_called_once();self.assertTrue(self.f.a.snapshot()['owners'])
 def test_new_check_cannot_bypass_uncertain_fence(self):
  self.f.a.recover=Mock(side_effect=OSError('unknown'));self.f.run()
  self.assertFalse(self.f.control.check('operator1')['eligible']);self.f.a.recover.assert_called_once()
 def test_original_physical_lock_busy_blocks(self):
  with NativeLock(self.f.a.lock_paths['gpu1'],create=False).held():
   self.assertFalse(self.f.control.check('operator1')['eligible'])
  self.f.calls.assert_not_called()
 def test_new_job_during_proof_blocks(self):
  self.f.proof_hook=lambda:self.f.store.put_job(dict(self.f.job,request_id='competing'))
  self.assertEqual(self.f.control.check('operator1')['failed_checks'],['state_changed_during_idle_proof'])
 def test_policy_drift_blocks(self):
  s=self.f.a.snapshot();s['recovery_policy']['policy']['samples']=1;self.f.store.write('ownership.json',s)
  self.assertEqual(self.f.control.check('operator1')['failed_checks'],['accepted_policy_binding_changed'])
 def test_unsupported_unknown_completion_and_target(self):
  j=self.f.store.job(self.f.job['request_id']);j['completion_verified']=False;self.f.store.put_job(j)
  self.assertEqual(self.f.control.check('operator1')['failed_checks'],['backend_execution_completion_unverified'])
 def test_durable_attempt_survives_control_restart(self):
  actual=self.f.a.recover
  def lost(*a):actual(*a);raise OSError('lost')
  self.f.a.recover=Mock(side_effect=lost);out=self.f.run()
  fresh=IdleRecovery(self.f.runner,self.f.observer,b'other-server-session-key-00000000000')
  self.assertTrue(fresh.reconcile(out['operation'])['verified_release']);self.f.a.recover.assert_called_once()
 def test_pre_authority_interruption_only_reopens_unchanged_fence(self):
  original=self.f.control.snapshot
  def fail(fenced=False,**kw):
   if fenced:raise Blocked(['synthetic_pre_authority_observation_failure'])
   return original(fenced,**kw)
  self.f.control.snapshot=fail;out=self.f.run();self.assertEqual(out['result'],'operator_required');self.f.calls.assert_not_called()
  self.f.control.snapshot=original
  done=self.f.control.reconcile(out['operation']);self.assertEqual(done['result'],'aborted_verified')
  self.assertTrue(self.f.a.snapshot()['owners']);self.f.calls.assert_not_called();self.assertEqual(self.f.gate()['mode'],'open')

class IdleRoutingTests(IdleTests):
 def setUp(self):
  super().setUp()
  self.app=IntegratedApplication.__new__(IntegratedApplication);self.app.idle=self.f.control
  self.app.runner=self.f.runner;self.app.read_token='r'*44;self.app.operate_token='o'*44
  self.app.control=SimpleNamespace(status=lambda:dict(available=True,capabilities=[],owners=self.f.a.snapshot()['owners']),
      check=Mock(return_value={'eligible':False,'failed_checks':['existing_emergency']}))
 def request(self,method,path,payload=None,operate=False):
  h={'Authorization':'Bearer '+('o' if operate else 'r')*44,'X-HA-User':'operator1'}
  code,_,raw=self.app.handle(method,path,h,json.dumps(payload).encode() if payload is not None else b'');return code,json.loads(raw)
 def test_http_route_to_original_authority(self):
  code,check=self.request('GET','/check');self.assertTrue(check['eligible'])
  code,out=self.request('POST','/recover',{'token':check['token'],'confirmed':True},True)
  self.assertEqual(code,200);self.assertTrue(out['verified_release']);self.f.calls.assert_called_once()
 def test_read_capability_and_emergency_preserved(self):
  _,check=self.request('GET','/check')
  self.assertEqual(self.request('POST','/recover',{'token':check['token'],'confirmed':True})[0],403)
  self.assertEqual(self.request('GET','/check-emergency')[1]['failed_checks'],['existing_emergency'])
  self.f.calls.assert_not_called()
 def test_status_precise_class_and_blocker(self):
  v=self.request('GET','/status')[1];self.assertEqual(v['incidents'][0]['classification'],'manual_idle_check_required')
  self.f.runner.futures['active']=Future()
  v=self.request('GET','/status')[1];self.assertEqual(v['incidents'][0]['failed_checks'],['runner_supervision_or_queue_active'])
