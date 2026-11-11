import json,unittest
from unittest.mock import patch
from aihub_gpu_runner.backends import Uncertain
from tests.test_idle_recovery import IdleFixture
from tests.test_recovery_gate_activation import GateFixture
from aihub_gpu_runner.integrated_runtime import IntegratedApplication
from aihub_gpu_runner.config import make_runner
from aihub_gpu_runner.api import Application
from aihub_gpu_runner.core import NativeLock
import os

class MissingPrerequisite(GateFixture):
 def test_existing_production_shaped_gate_without_idle_lock_is_specific_and_readonly(self):
  # Production activated the shared gate while retaining the old journal. It
  # never used initialize(), unlike the earlier recovery fixtures.
  self.transition();p=self.a.store.path('recovery-control.lock');p.unlink()
  before={str(p):p.read_bytes() for p in self.a.store.root.rglob('*') if p.is_file()}
  with patch.dict(os.environ,AIHUB_RECOVERY_CONFIG=str(self.settings_path),AIHUB_GPU_RUNNER_TOKEN='j'*64):
   _,runner=make_runner(self.new)
   try:
    app=runner.recovery_application;status=app.status()
    self.assertFalse(status['recovery_operational'])
    self.assertEqual(status['configuration_errors'],['idle_coordination_lock_missing_recovery_control'])
    self.assertNotIn('manual_idle_recovery_v1',status['capabilities'])
    self.assertTrue(status['admission_ready']);self.assertTrue(runner.admission.automatic_no_start_enabled)
    with patch.object(app,'idle_target',return_value=True):
     code,_,raw=app.handle('GET','/check',{'Authorization':'Bearer '+'r'*64,'X-HA-User':'admin'})
    self.assertEqual(code,200);self.assertEqual(json.loads(raw)['classification'],'configuration_error')
    code,_,_=Application(runner,'j'*64).handle('GET','/resources',{'Authorization':'Bearer '+'j'*64})
    self.assertEqual(code,200)
    self.assertFalse(p.exists())
    self.assertEqual(before,{str(p):p.read_bytes() for p in self.a.store.root.rglob('*') if p.is_file()})
   finally:runner.close()

class ObservationRefusals(unittest.TestCase):
 def setUp(self):self.f=IdleFixture(self)
 def test_absence_does_not_create_lock_or_release_ownership(self):
  path=self.f.store.path('recovery-control.lock');path.unlink()
  out=self.f.control.check('admin')
  self.assertEqual(out['failed_checks'],['idle_coordination_lock_missing_recovery_control'])
  self.assertFalse(path.exists());self.f.calls.assert_not_called()
 def test_observation_unavailable_has_explicit_refusal(self):
  for exc in (PermissionError(13,'private'),Uncertain('private endpoint')):
   with patch.object(self.f.control,'proof',side_effect=exc):out=self.f.control.check('admin')
   self.assertEqual(out['classification'],'observation_unavailable')
   self.assertNotIn('private',json.dumps(out));self.f.calls.assert_not_called()
 def test_lock_removed_after_prerequisite_check_is_specific(self):
  original=self.f.control.exclusive
  def lost():
   self.f.store.path('recovery-control.lock').unlink();return original()
  with patch.object(self.f.control,'exclusive',side_effect=lost):out=self.f.control.check('admin')
  self.assertEqual(out['failed_checks'],['idle_coordination_lock_missing_recovery_control'])
 def test_lock_disappeared_after_approval_cannot_mutate(self):
  token=self.f.token();self.f.store.path('recovery-control.lock').unlink()
  from aihub_gpu_runner.recovery_control import Blocked
  with self.assertRaises(Blocked):self.f.control.recover(token,'operator1',True)
  self.f.calls.assert_not_called()
 def test_unexpected_failure_has_bounded_trace_reference(self):
  app=IntegratedApplication.__new__(IntegratedApplication)
  app.read_token='r'*64;app.operate_token='o'*64;app.idle=self.f.control;app.runner=self.f.runner
  with patch.object(app,'idle_target',return_value=True),patch.object(self.f.control,'check',side_effect=AttributeError('sensitive content')):
   with self.assertLogs('aihub_gpu_runner.integrated_runtime',level='ERROR') as logs:
    code,_,body=app.handle('GET','/check',{'Authorization':'Bearer '+'r'*64,'X-HA-User':'admin'})
  v=json.loads(body);self.assertEqual(code,503);self.assertEqual(len(v['diagnostic_reference']),32)
  self.assertIn(v['diagnostic_reference'],str(logs.output));self.assertNotIn('sensitive content',str(logs.output)+body.decode())

if __name__=='__main__':unittest.main()
