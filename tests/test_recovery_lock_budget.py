"""Real CPU-process contention; no backend or production files are involved."""
import subprocess,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from aihub_gpu_runner.core import AtomicStore,NativeLock
from aihub_gpu_runner.idle_recovery import IdleRecovery,_DEADLINE
from aihub_gpu_runner.recovery_control import Blocked

@unittest.skipIf(sys.platform=='win32','POSIX flock contention is qualified on Linux')
class RealLockBudget(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.store=AtomicStore(Path(self.tmp.name));self.control=IdleRecovery.__new__(IdleRecovery)
  self.control.store=self.store;self.control.runner=SimpleNamespace(service_lock=SimpleNamespace(fd=1))
  for n in ('practical-recovery','recovery-control','maintenance','recovery'):
   with NativeLock(self.store.path(n+'.lock')).held():pass
 def test_real_contender_returns_named_busy_with_one_budget_and_releases_prefix(self):
  script='import fcntl,sys,time; f=open(sys.argv[1],"r+"); fcntl.flock(f,fcntl.LOCK_EX); print("held",flush=True); time.sleep(15)'
  child=subprocess.Popen([sys.executable,'-c',script,str(self.store.path('recovery.lock'))],stdout=subprocess.PIPE,text=True)
  try:
   self.assertEqual(child.stdout.readline().strip(),'held');start=time.monotonic()
   with self.assertRaisesRegex(Blocked,'busy_recovery'):
    with self.control.exclusive():self.fail('entered while held')
   self.assertLess(time.monotonic()-start,1.5)
   for name in ('practical-recovery','recovery-control','maintenance'):
    with NativeLock(self.store.path(name+'.lock'),create=False).held(0):pass
  finally:child.terminate();child.wait(5);child.stdout.close()
  with self.control.exclusive():pass
 def test_missing_late_lock_does_not_leak_acquired_prefix(self):
  self.store.path('maintenance.lock').unlink()
  with self.assertRaises(FileNotFoundError):
   with self.control.exclusive():pass
  for name in ('practical-recovery','recovery-control'):
   with NativeLock(self.store.path(name+'.lock'),create=False).held(0):pass
 def test_one_expired_request_budget_refuses_before_lock_or_scan(self):
  token=_DEADLINE.set(time.monotonic()-.1)
  try:
   with self.assertRaisesRegex(Blocked,'request_deadline'):
    with self.control.exclusive():pass
  finally:_DEADLINE.reset(token)
 def test_body_timeout_is_not_mislabeled_as_lock_contention(self):
  lock=NativeLock(self.store.path('jobs.lock'))
  with self.assertRaisesRegex(TimeoutError,'observer_timeout'):
   with self.control.hold(lock,'jobs'):raise TimeoutError('observer_timeout')
 def test_six_thousand_normal_records_are_scanned_without_cap(self):
  for i in range(6001):self.store.put_job({'request_id':f'synthetic-{i}','state':'success'})
  start=time.monotonic();token=_DEADLINE.set(start+50)
  try:self.assertEqual(len(self.control.jobs()),6001)
  finally:_DEADLINE.reset(token)
  self.assertLess(time.monotonic()-start,50)

if __name__=='__main__':unittest.main()
