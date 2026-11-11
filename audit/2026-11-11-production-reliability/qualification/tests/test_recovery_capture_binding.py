import copy,time,unittest
from unittest.mock import patch
from tests.recovery_fixture import Fixture,BOOT
from aihub_gpu_runner.core import RecoveryRequired,Runner
from types import SimpleNamespace
from aihub_gpu_runner.recovery_capture import eligible,validate,empty_barrier,active_record,PROVENANCE

class RecoveryCaptureBindingTests(unittest.TestCase):
 def setUp(self):
  self.f=Fixture(self);self.job=copy.deepcopy(self.f.job);life=self.job['wallpaper_lifecycle']
  current=dict(self.f.original,pid=123,running=True,started_at='new-generation')
  life.update(phase='ready',running_container=current,original_pids={'123':'456'})
  self.job.update(execution_container=current,execution_processes={'123':'456'},execution_deadline=time.time()-1)
  self.state=self.f.a.snapshot()
  self.record=dict(provenance=PROVENANCE,collected_at=time.time(),job_id=self.job['request_id'],lease_id=self.job['lease_id'],request_hash=self.job['request_hash'],scope=self.state['scope'],policy_id=self.state['recovery_policy']['policy_id'],container=current,lock_identity=self.state['lock_identity'],execution_anchor=dict(pid=123,birth='456',boot=BOOT),barrier_generation={'id':'fixture'},execution_init_birth='456')
  self.boot=patch('aihub_gpu_runner.recovery_capture.guest_boot_id',return_value=BOOT);self.boot.start();self.addCleanup(self.boot.stop)
 def test_ready_is_not_prepared_and_original_is_unchanged(self):
  original=copy.deepcopy(self.job);eligible(self.f.a,self.state,self.job);validate(self.record,self.job,self.state)
  self.assertEqual(self.job,original);self.assertNotIn('execution_anchor',self.job)
 def test_generation_and_submission_refusals(self):
  for field in ('backend_detail','completion_verified','execution_anchor'):
   job=copy.deepcopy(self.job);job[field]=True
   with self.assertRaises(RecoveryRequired):eligible(self.f.a,self.state,job)
  job=copy.deepcopy(self.job);job['wallpaper_lifecycle']['submission_intent']='now'
  with self.assertRaises(RecoveryRequired):eligible(self.f.a,self.state,job)
  job['wallpaper_lifecycle']['submission_intent']=None
  with self.assertRaises(RecoveryRequired):eligible(self.f.a,self.state,job)
  with self.assertRaises(RecoveryRequired):eligible(self.f.a,self.state,self.job,{'active':True})
 def test_every_capture_binding_is_required(self):
  for field in ('job_id','lease_id','request_hash','scope','policy_id','container','lock_identity','provenance'):
   r=copy.deepcopy(self.record);r[field]='changed'
   with self.assertRaises(RecoveryRequired,msg=field):validate(r,self.job,self.state)
  for field in ('pid','birth','boot'):
   r=copy.deepcopy(self.record);r['execution_anchor'][field]='changed'
   with self.assertRaises(RecoveryRequired,msg=field):validate(r,self.job,self.state)
 def test_foreign_and_uncertain_barriers_refused(self):
  base={'mode':'open','operation':None,'prompts':{},'running':{},'pending':[],'inflight':{}}
  empty_barrier(base)
  for key in ('prompts','running','pending','inflight','queue_intents'):
   b=copy.deepcopy(base);b[key]=['foreign']
   with self.assertRaises(RecoveryRequired,msg=key):empty_barrier(b)
  for missing in ('prompts','running','pending','inflight','operation'):
   b=copy.deepcopy(base);del b[missing]
   with self.assertRaises(RecoveryRequired,msg=missing):empty_barrier(b)
  for key in ('mode','operation'):
   b=copy.deepcopy(base);b[key]='unknown'
   with self.assertRaises(RecoveryRequired):empty_barrier(b)
 def test_unconfirmed_capture_cannot_be_used_as_active_record(self):
  self.state['practical_control']={'active':'x','admission':'closed','attempts':{'x':{'mode':'recover','observation_before':{'recovery_capture':self.record},'lease':self.job['lease_id'],'job_before':self.job}}}
  with self.assertRaises(RecoveryRequired):active_record(self.state,self.job)
  self.state['practical_control']['attempts']['x']['approval']='confirmed_exact_action'
  self.assertEqual(active_record(self.state,self.job),self.record)

 def test_cpu_startup_preserves_exact_ready_incident(self):
  self.f.store.put_job(self.job);before=self.f.store.path(self.f.store.job_name(self.job['request_id'])).read_bytes()
  self.f.supervisor.close()
  recovery=SimpleNamespace(startup=lambda:None,start=lambda:None,close=lambda:None)
  runner=Runner(self.f.a,{}, {},recovery=recovery)
  try:self.assertEqual(self.f.store.path(self.f.store.job_name(self.job['request_id'])).read_bytes(),before)
  finally:runner.close()
