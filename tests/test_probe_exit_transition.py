import copy,errno,unittest
from unittest.mock import patch
from aihub_gpu_runner.ollama_probe import snapshot,exiting_probe
from tests.test_ollama_readonly_probe import ProbeAttestationTests,ProbeGuardTests

class ExitAttestation(ProbeAttestationTests):
 def test_exit_requires_complete_positive_kernel_identity(self):
  first=self.table();self.child='101 100 4026532940 - [llama-server] <defunct>';second=self.table()
  good=dict(state='Z',ppid='100',birth='20',cgroup=self.group)
  for key,value in [('state','S'),('ppid','99'),('birth','21'),('cgroup','changed'),(None,None)]:
   obs=dict(good)
   if key:obs[key]=value
   self.tables=[first,second]
   result=snapshot(self.observer,self.ident,1,lambda p:False,'10',read_birth=lambda p:self.births[p],read_cgroup=lambda p:self.group,read_zombie=lambda p:obs)
   self.assertEqual(bool(result[1]),key is None)
 def test_quarantined_zombie_survives_next_census_but_exec_change_does_not(self):
  role=self.run_snapshot()[1]['101'];self.child='101 100 4026532940 - [llama-server] <defunct>'
  good=dict(state='Z',ppid='100',birth='20',cgroup=self.group)
  result=snapshot(self.observer,self.ident,1,lambda p:False,'10',read_birth=lambda p:self.births[p],read_cgroup=lambda p:self.group,read_zombie=lambda p:good,prior_probes={'101':role})
  self.assertEqual(result[1],{'101':role})
  self.child='101 100 4026532940 /tmp/unknown /tmp/unknown --compute'
  self.assertEqual(snapshot(self.observer,self.ident,1,lambda p:False,'10',read_birth=lambda p:self.births[p],read_cgroup=lambda p:self.group,read_zombie=lambda p:good,prior_probes={'101':role})[1],{})
 def test_missing_metadata_or_permission_is_not_exit_proof(self):
  first=self.table();self.child='101 100 4026532940 - [llama-server] <defunct>'
  for failure in (PermissionError(),FileNotFoundError()):
   self.tables=[first,self.table()]
   with patch('builtins.open',side_effect=failure):
    def unavailable(pid):raise failure
    self.assertEqual(snapshot(self.observer,self.ident,1,lambda p:False,'10',read_birth=lambda p:self.births[p],read_cgroup=lambda p:self.group,read_zombie=unavailable)[1],{})

# The existing full guard tests also prove that an attested/quarantined lifetime
# never counts as idle, must positively disappear, and needs three fresh samples.
