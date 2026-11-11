import copy,errno,types,unittest
from unittest.mock import patch
from aihub_gpu_runner.ollama_probe import IMAGE,snapshot
from aihub_gpu_runner.backends import CleanupPending
from aihub_gpu_runner.core import RecoveryRequired
from aihub_gpu_runner.recovery import ProcessDisappeared
from tests import test_ollama_physical_cleanup as fixture

class ProbeAttestationTests(unittest.TestCase):
 def setUp(self):
  self.cid='f'*64;self.ident=dict(id=self.cid,pid=100,started_at='fixed-generation')
  self.group='0::/system.slice/docker-'+self.cid+'.scope\n';self.births={'100':'10','101':'20'}
  self.child='101 100 4026532940 /usr/lib/ollama/llama-server /usr/lib/ollama/llama-server --list-devices --offline --verbose'
  self.parent='100 50 4026532940 /usr/bin/ollama /bin/ollama serve'
  self.tables=None;self.calls=[]
  self.observer=types.SimpleNamespace(p={'docker':'/readonly/docker','services':{'ollama':{'image':IMAGE}}},command=self.command)
 def table(self):return 'PID PPID PIDNS EXE COMMAND\n'+self.parent+'\n'+self.child+'\n'
 def command(self,argv,deadline):
  self.calls.append(argv)
  return self.tables.pop(0) if self.tables is not None else self.table()
 def run_snapshot(self):return snapshot(self.observer,self.ident,1,lambda p:False,'10',read_birth=lambda p:self.births[p],read_cgroup=lambda p:self.group)
 def test_exact_observed_llama_list_devices_is_attested_with_birth_parent_namespace_and_generation(self):
  processes,roles=self.run_snapshot();self.assertEqual(roles['101']['role'],'llama_list_devices');self.assertEqual(processes['101'],'20')
  self.assertEqual(len(self.calls),2);self.assertEqual(roles['101']['parent_birth'],'10')
 def test_exact_ollama_gpu_discovery_is_attested(self):
  self.child='101 100 4026532940 /usr/bin/ollama /usr/bin/ollama gpu-discover --lib-dir /usr/lib/ollama --lib-dir /usr/lib/ollama/cuda_v12'
  self.assertEqual(self.run_snapshot()[1]['101']['role'],'ollama_gpu_discover')
 def test_model_or_server_or_extra_arguments_are_not_probe_roles(self):
  for flags in ('--model weights --port 1','--port 1 --offline --verbose','--list-devices --offline --verbose --model weights','--list-devices --offline --verbose --port 1'):
   self.child='101 100 4026532940 /usr/lib/ollama/llama-server /usr/lib/ollama/llama-server '+flags
   self.assertEqual(self.run_snapshot()[1],{})
 def test_executable_spoof_is_rejected(self):
  self.child=self.child.replace('4026532940 /usr/lib/ollama/llama-server','4026532940 /tmp/untrusted',1)
  self.assertEqual(self.run_snapshot()[1],{})
 def test_other_parent_or_namespace_is_rejected(self):
  for child in (self.child.replace('101 100','101 99'),self.child.replace('101 100 4026532940','101 100 4026539999')):
   self.child=child;self.assertEqual(self.run_snapshot()[1],{})
 def test_changed_container_cgroup_is_rejected(self):
  self.group='0::/system.slice/docker-'+('a'*64)+'.scope\n';self.assertEqual(self.run_snapshot()[1],{})
 def test_other_image_is_not_accepted(self):
  self.observer.p['services']['ollama']['image']='other-image';self.assertEqual(self.run_snapshot()[1],{})
 def test_parent_birth_drift_is_rejected(self):
  self.births['100']='changed';self.assertEqual(self.run_snapshot()[1],{})
 def test_changed_executable_between_censuses_is_rejected(self):
  self.tables=[self.table(),self.table().replace('--list-devices --offline --verbose','--model weights')]
  self.assertEqual(self.run_snapshot()[1],{})
 def test_pid_reuse_between_censuses_is_rejected(self):
  old=self.command
  def command(a,d):
   result=old(a,d)
   if len(self.calls)==2:self.births['101']='new-lifetime'
   return result
  self.observer.command=command;self.assertEqual(self.run_snapshot()[1],{})

class ProbeGuardTests(unittest.TestCase):
 def setUp(self):
  self.c=fixture.PhysicalCleanupTests('runTest');self.c.setUp();self.addCleanup(self.c.doCleanups)
  self.c.memory=lambda:1;self.until=.25;self.roles_enabled=True;self.unleased=False;self.role_changed=False;self.reused=False
  self.role=dict(pid='305',birth='probe-birth',ppid='303',parent_birth='server-birth',pidns='synthetic-namespace',exe='/usr/lib/ollama/llama-server',argv=['/usr/lib/ollama/llama-server','--list-devices','--offline','--verbose'],cgroup='synthetic',container_id=self.c.metas['ollama']['Id'],container_started_at='synthetic',role='llama_list_devices')
  def birth(pid):
   if pid=='305':
    if self.c.clock.t>=self.until:raise ProcessDisappeared(pid,errno.ENOENT)
    return 'new-lifetime' if self.reused else 'probe-birth'
   return self.c.births[pid]
  self.c.guard.birth_reader=birth;self.c.prepare();self.count=0
  def census(*args,**kwargs):
   self.count+=1;p=dict(self.c.ollama_pids);r={}
   if self.c.clock.t<self.until:
    p['305']='probe-birth'
    if self.roles_enabled:r['305']=copy.deepcopy(self.role)
    if self.role_changed and self.count>1:r['305']['exe']='/tmp/changed'
   return p,r
  self.patch=patch('aihub_gpu_runner.ollama_probe.snapshot',side_effect=census);self.patch.start();self.addCleanup(self.patch.stop)
  self.c.apps=lambda:fixture.GPU0+',202,2000\n'+((fixture.GPU0 if self.unleased else fixture.GPU1)+',305,32\n' if self.c.clock.t<self.until else '')
 def observations(self):return [v['ollama_cleanup_observation'] for v in self.c.records if 'ollama_cleanup_observation' in v]
 def test_live_probe_is_never_idle_positive_exit_then_three_fresh_samples_required(self):
  proof=self.c.guard.verify(2);self.assertEqual(proof['stable_samples'],3);self.assertGreaterEqual(self.c.clock.t,.6)
  observations=self.observations();good=[v for v in observations if not v['process_snapshot_discarded']]
  self.assertEqual(len(good),3);self.assertTrue(all('305' in v['exited_ollama_processes'] for v in good))
  self.assertTrue(any('ollama_readonly_probe_attestations' in v for v in self.c.records))
 def test_unknown_new_compute_still_fails_closed(self):
  self.roles_enabled=False
  with self.assertRaisesRegex(RecoveryRequired,'new_or_reused_process'):self.c.guard.verify(2)
 def test_probe_on_unleased_gpu_still_fails_closed(self):
  self.unleased=True
  with self.assertRaisesRegex(RecoveryRequired,'unleased_gpu_activity'):self.c.guard.verify(2)
 def test_reused_probe_pid_still_fails_closed(self):
  self.reused=True
  with self.assertRaisesRegex(RecoveryRequired,'new_or_reused_process'):self.c.guard.verify(2)
 def test_probe_executable_or_arguments_change_still_fails_closed(self):
  self.role_changed=True
  with self.assertRaisesRegex(RecoveryRequired,'probe_identity_changed'):self.c.guard.verify(2)
 def test_probe_that_never_exits_exhausts_existing_budget_and_retains_owner(self):
  self.until=10
  with self.assertRaises(CleanupPending):self.c.guard.verify(.4)
  self.assertIn('gpu1',self.c.state['owners'])
 def test_probe_inference_results_and_native_work_are_never_replayed(self):
  self.c.guard.verify(2)
  self.assertFalse(any(call[0]=='POST' for call in self.c.calls))
  self.assertIn('gpu1',self.c.state['owners'],'Verifier alone never clears ownership')

if __name__=='__main__':unittest.main(verbosity=2)
