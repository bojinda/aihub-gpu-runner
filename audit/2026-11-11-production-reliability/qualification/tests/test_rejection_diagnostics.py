import json,unittest
from aihub_gpu_runner.ollama_diagnostics import BoundedDiagnostics
from aihub_gpu_runner.ollama_probe import snapshot
from tests.test_ollama_readonly_probe import ProbeAttestationTests

class DiagnosticTests(ProbeAttestationTests):
 def run_with_diagnostics(self,callback):
  return snapshot(self.observer,self.ident,1,lambda p:False,'10',read_birth=lambda p:self.births[p],read_cgroup=lambda p:self.group,diagnostic=callback)
 def test_missing_second_census_has_identity_and_precise_predicate(self):
  self.tables=[self.table(),'PID PPID PIDNS EXE COMMAND\n'+self.parent+'\n']
  d=BoundedDiagnostics();processes,roles=self.run_with_diagnostics(d)
  self.assertEqual(roles,{})
  event=d.export()['events'][-1]
  self.assertEqual(event['predicate'],'second_child_metadata_missing')
  self.assertEqual(event['first']['birth'],'20');self.assertEqual(event['first']['ppid'],'100')
  self.assertEqual(event['first']['pidns'],'4026532940');self.assertEqual(event['first']['cgroup'],self.group)
  self.assertEqual(len(self.calls),2)
 def test_unknown_model_argument_redacted_and_rejection_unchanged(self):
  self.child=self.child.replace('--list-devices --offline --verbose','--model PRIVATE_PROMPT --port 9000')
  d=BoundedDiagnostics();self.assertEqual(self.run_with_diagnostics(d)[1],{})
  self.assertEqual(d.export()['events'][-1]['predicate'],'exact_probe_role_mismatch')
  self.assertNotIn('PRIVATE_PROMPT',json.dumps(d.export()));self.assertEqual(len(self.calls),1)
 def test_sink_exception_does_not_change_acceptance_or_add_observations(self):
  def broken(*a,**kw):raise OSError('diagnostic failure')
  self.assertEqual(self.run_with_diagnostics(broken)[1]['101']['role'],'llama_list_devices')
  self.assertEqual(len(self.calls),2)
 def test_second_birth_change_identified(self):
  command=self.command
  def changed(a,d):
   result=command(a,d)
   if len(self.calls)==2:self.births['101']='new'
   return result
  self.observer.command=changed;d=BoundedDiagnostics()
  self.assertEqual(self.run_with_diagnostics(d)[1],{})
  self.assertEqual(d.export()['events'][-1]['predicate'],'second_child_birth_changed')
 def test_namespace_rejection_and_original_call_count(self):
  self.child=self.child.replace('101 100 4026532940','101 100 4026539999')
  d=BoundedDiagnostics();self.assertEqual(self.run_with_diagnostics(d)[1],{})
  self.assertEqual(d.export()['events'][-1]['predicate'],'child_pid_namespace_mismatch')
  self.assertEqual(len(self.calls),1)

class BoundTests(unittest.TestCase):
 def test_bounded_eviction(self):
  d=BoundedDiagnostics()
  for i in range(100):d('test',pid=str(i),argv=['SECRET'*100]*100)
  v=d.export();self.assertEqual(len(v['events']),64);self.assertEqual(v['evicted_events'],36)
  self.assertLess(len(json.dumps(v)),140000);self.assertNotIn('SECRET',json.dumps(v))
 def test_oversize_is_explicit(self):
  d=BoundedDiagnostics();d('test',pid='4',details={str(i):'x'*256 for i in range(24)})
  v=d.export();self.assertEqual(v['oversized_events'],1)
  self.assertEqual(v['events'][0]['omitted'],'event_size_limit')
