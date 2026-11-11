"""Production-shaped omitted flag, real config factory, native locks/journals; no GPU I/O."""
import copy,hashlib,json,os,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from aihub_gpu_runner.config import load_config,admission_from_config,make_runner
from aihub_gpu_runner.core import Admission,AtomicStore,NativeLock,RecoveryRequired,guest_boot_id,encode,Conflict
from aihub_gpu_runner.api import Application
from aihub_gpu_runner.recovery import digest
from aihub_gpu_runner.maintenance import initialize,read_gate
from aihub_gpu_runner.scope_transition import transition_recovery_gate

class GateFixture(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.fixture=json.loads((Path(__file__).parent/'fixtures/production_gate_omitted.json').read_text())
  config=self.fixture['config'];config['state_dir']=str(self.root/'state');config['lock_paths']={r:str(self.root/(r+'.lock')) for r in ('gpu0','gpu1')}
  for name,target in config['targets'].items():
   target['base_url']='http://127.0.0.1:1'
   if 'workflow_template' in target:
    target.pop('workflow_template');target.pop('workflow_sha256');target['workflow']=self.fixture['templates'][name]
  self.old=self.root/'old.json';self.new=self.root/'new.json'
  self.old.write_text(json.dumps(config));self.new.write_text(json.dumps(dict(config,recovery_control_required=True)))
  _,_,root,locks,scope=load_config(self.old)
  self.a=Admission(AtomicStore(root),locks,scope)
  self.a.bootstrap(dict(approval_ref='OFFLINE',bootstrap_id=self.a.snapshot()['bootstrap_id'],backend_quiescent=True,cleanup_verified=True))
  initialize(self.a)
  for name in ('supervisor.lock','jobs.lock','practical-recovery.lock'):
   with NativeLock(root/name).held():pass
  self.p=dict(self.fixture['policy'],scope=scope)
  s=self.a.snapshot();s.update(verified_boot=guest_boot_id(),recovery_policy=dict(policy=self.p,policy_id=digest(self.p),approval_ref='original'),
    practical_control=dict(admission='open',active=None,attempts={}))
  self.a.store.write('ownership.json',s)
  self.settings=dict(schema=1,automatic_no_start=True,destructive=False,idle_only_manual=True)
  for name in ('read_token','operate_token','ticket_key'):
   p=self.root/name;p.write_text(name[0]*64);self.settings[name+'_file']=str(p)
  self.settings_path=self.root/'recovery.json';self.settings_path.write_text(json.dumps(self.settings))
  self.net=patch('aihub_gpu_runner.backends.HTTPTransport._request',side_effect=AssertionError('network forbidden'));self.net.start();self.addCleanup(self.net.stop)
 def evidence(self,old=None,new=None):
  old,new=old or self.old,new or self.new
  s=self.a.store.read('ownership.json');new_scope=load_config(new)[-1]
  return dict(operator_approved_recovery_gate_transition=True,all_admission_callers_reviewed=True,callers_excluded=True,
    approval_ref='OFFLINE-GATE',caller_evidence_sha256='offline',old_scope=load_config(old)[-1],new_scope=new_scope,
    old_config_sha256=hashlib.sha256(old.read_bytes()).hexdigest(),new_config_sha256=hashlib.sha256(new.read_bytes()).hexdigest(),
    ownership_sha256=hashlib.sha256(self.a.store.path('ownership.json').read_bytes()).hexdigest(),
    maintenance_sha256=hashlib.sha256(self.a.store.path('recovery-control.json').read_bytes()).hexdigest(),
    jobs_sha256=digest({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.a.store.path('jobs').glob('*.json')}),
    old_policy_id=s['recovery_policy']['policy_id'],new_policy_id=digest(dict(s['recovery_policy']['policy'],scope=new_scope)))
 def observer(self):
  return patch('aihub_gpu_runner.recovery.RecoveryObserver',return_value=Mock(
    container=Mock(return_value={'synthetic':'unchanged'}),driver_health=Mock(return_value={'synthetic':'healthy'}),
    stopped_ready=Mock(return_value=dict(idle_ready=True,stable_samples=3,queues={'synthetic':'empty'}))))
 def transition(self,**kwargs):
  with self.observer():return transition_recovery_gate(self.old,self.new,self.evidence(),**kwargs)

class ConfigRegression(GateFixture):
 def test_omitted_flag_starts_ordinary_service_but_reports_configuration_error_even_without_owner(self):
  self.assertNotIn('recovery_control_required',json.loads(self.old.read_text()))
  with patch.dict(os.environ,AIHUB_RECOVERY_CONFIG=str(self.settings_path),AIHUB_GPU_RUNNER_TOKEN='j'*64):
   _,runner=make_runner(self.old)
   try:
    app=runner.recovery_application
    status=app.status();self.assertFalse(status['recovery_operational']);self.assertEqual(status['configuration_errors'],['mandatory_admission_gate_not_configured'])
    self.assertEqual(status['capabilities'],[]);self.assertTrue(status['admission_ready']);self.assertTrue(runner.admission.automatic_no_start_enabled)
    for path in ('/check','/check-emergency'):
     code,_,raw=app.handle('GET',path,{'Authorization':'Bearer '+'r'*64,'X-HA-User':'admin'})
     value=json.loads(raw);self.assertEqual(code,200);self.assertFalse(value['eligible']);self.assertEqual(value['classification'],'configuration_error')
    code,_,raw=Application(runner,'j'*64).handle('GET','/resources',{'Authorization':'Bearer '+'j'*64})
    self.assertEqual(code,200);self.assertTrue(json.loads(raw)['ready'])
    code,_,raw=app.handle('POST','/recover',{'Authorization':'Bearer '+'o'*64,'X-HA-User':'admin'},b'{"token":"stale","confirmed":true}')
    self.assertEqual(code,409);self.assertEqual(json.loads(raw)['error'],'recovery_configuration_error')
   finally:runner.close()
 def test_configured_factory_and_host_admission_enforce_existing_closed_and_missing_gate(self):
  self.transition()
  _,_,a=admission_from_config(self.new);self.assertTrue(a.control_required)
  gate=read_gate(a.store,True);gate.update(mode='closed',active='synthetic');gate['attempts']['synthetic']={};a.store.write('recovery-control.json',gate)
  self.assertIsNone(a.try_acquire('host-fixture','hash',('gpu0',),'host-whisperx'))
  from aihub_gpu_runner.host_stage import HostStage
  stage=HostStage(a,'gpu0','host-entry-fixture','hash',host_policy=self.fixture['config']['host_stages']['whisperx'])
  with patch('subprocess.Popen',side_effect=AssertionError('host execution forbidden')):
   self.assertEqual(stage.run(['/bin/true'],{},timeout=0),75)
  a.store.path('recovery-control.json').unlink() # Disposable fault injection only.
  self.assertTrue(a.maintenance_blocked())
  self.assertIsNone(a.try_acquire('host-fixture','hash',('gpu0',),'host-whisperx'))

class TransitionTests(GateFixture):
 def test_successor_startup_preserves_old_failed_cleanup_binding_without_replay(self):
  job=dict(request_id='historical-retired',target='ollama',operation='generate',payload={'model':'fixture'},resources=['gpu1'],
    state='failed',error_code='operator_recovered_no_automatic_resubmission',request_hash='a'*64,
    ollama_cleanup_context={'policy_id':digest(self.p),'original_evidence':'preserved'})
  self.a.store.put_job(job);raw=self.a.store.path('jobs/historical-retired.json').read_bytes()
  self.transition()
  with patch.dict(os.environ,AIHUB_RECOVERY_CONFIG=str(self.settings_path),AIHUB_GPU_RUNNER_TOKEN='j'*64):
   _,runner=make_runner(self.new)
   try:
    self.assertTrue(runner.recovery_application.status()['recovery_operational'])
    self.assertEqual(runner.store.path('jobs/historical-retired.json').read_bytes(),raw)
    self.assertEqual(runner.futures,{})
   finally:runner.close()
 def test_exact_atomic_rebind_preserves_history_and_gate(self):
  before=self.a.snapshot();gate=self.a.store.path('recovery-control.json').read_bytes();self.transition()
  after=self.a.store.read('ownership.json')
  for key in set(before)-{'scope','recovery_policy','scope_transitions'}:self.assertEqual(before[key],after[key])
  record=after['scope_transitions'][-1];self.assertEqual(record['previous_recovery_policy'],before['recovery_policy'])
  self.assertEqual(dict(after['recovery_policy']['policy'],scope=before['scope']),before['recovery_policy']['policy'])
  self.assertEqual(self.a.store.path('recovery-control.json').read_bytes(),gate)
  self.assertEqual(admission_from_config(self.new)[2].scope,after['scope'])
  with self.assertRaisesRegex(RecoveryRequired,'no_retry'):self.transition()
 def test_owner_and_nonterminal_job_block(self):
  s=self.a.snapshot();s['owners']['gpu1']=dict(job_id='retained',request_hash='h',lease_id='l',target='ollama',phase='reconciling')
  self.a.store.write('ownership.json',s)
  with self.assertRaisesRegex(RecoveryRequired,'owner_free'):self.transition()
  s['owners']={};self.a.store.write('ownership.json',s);self.a.store.put_job(dict(request_id='pending',state='submitted'))
  with self.assertRaisesRegex(RecoveryRequired,'owner_free'):self.transition()
 def test_scope_drift_or_threshold_change_block(self):
  v=json.loads(self.new.read_text());v['workers']+=1;self.new.write_text(json.dumps(v))
  with self.assertRaisesRegex(ValueError,'only_recovery_gate'):self.transition()
 def test_supervisor_physical_lock_and_stale_evidence_block(self):
  for name in ('supervisor.lock','practical-recovery.lock'):
   with NativeLock(self.a.store.path(name),create=False).held():
    with self.assertRaises(TimeoutError):self.transition()
  with NativeLock(self.a.lock_paths['gpu1'],create=False).held():
   with self.assertRaises(TimeoutError):self.transition()
  evidence=self.evidence();evidence['maintenance_sha256']='changed'
  with self.assertRaisesRegex(RecoveryRequired,'journal_binding'):
   transition_recovery_gate(self.old,self.new,evidence)
 def test_uncertain_commit_is_inspect_only_never_repeated(self):
  original=self.a.store.write
  def fail_after(store,name,value):
   original(name,value)
   if name=='ownership.json':raise OSError('synthetic lost completion')
  with self.observer(),patch.object(AtomicStore,'write',fail_after):
   with self.assertRaises(OSError):transition_recovery_gate(self.old,self.new,self.evidence())
  self.assertEqual(len(self.a.store.read('ownership.json')['scope_transitions']),1)
  with self.assertRaisesRegex(RecoveryRequired,'no_retry'):self.transition()
 def test_rollback_is_another_audited_owner_free_transition(self):
  original=self.a.snapshot();self.transition();evidence=self.evidence(self.new,self.old)
  evidence.update(approval_ref='OFFLINE-ROLLBACK',rollback_of='OFFLINE-GATE')
  with self.observer():transition_recovery_gate(self.new,self.old,evidence,rollback=True)
  after=self.a.snapshot();self.assertEqual(after['recovery_policy']['policy'],original['recovery_policy']['policy'])
  self.assertEqual(len(after['scope_transitions']),2)
 def test_missing_maintenance_journal_is_never_initialized(self):
  evidence=self.evidence();p=self.a.store.path('recovery-control.json');p.unlink()
  with self.assertRaisesRegex(RecoveryRequired,'no_initialization'):transition_recovery_gate(self.old,self.new,evidence)
  self.assertFalse(p.exists())
