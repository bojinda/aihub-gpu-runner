"""Independent audit tests. No production addresses, GPU or network access."""
import errno,json,os,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from aihub_gpu_runner.core import NativeLock,RecoveryRequired
from aihub_gpu_runner.recovery import process_birth,ProcessDisappeared
from aihub_gpu_runner.ollama_probe import rows
from tests.test_idle_recovery import IdleFixture
from tests.test_ollama_physical_cleanup import PhysicalCleanupTests,GPU1

class ContractTests(unittest.TestCase):
 def test_unsupported_class_no_authority(self):
  f=IdleFixture(self);j=f.store.job(f.job['request_id']);j['error_code']='ollama_cleanup_unknown_or_duplicate_gpu_process';f.store.put_job(j)
  self.assertEqual(f.control.check('audit')['failed_checks'],['unsupported_retained_failure_class']);f.calls.assert_not_called()
 def test_worker_lock_masks_class_without_mutation(self):
  f=IdleFixture(self);j=f.store.job(f.job['request_id']);j['error_code']='ollama_cleanup_unknown_or_duplicate_gpu_process';f.store.put_job(j)
  before={str(p):p.read_bytes() for p in f.store.root.rglob('*') if p.is_file()}
  with NativeLock(f.store.path('recovery.lock'),create=False).held():
   self.assertEqual(f.control.check('audit')['failed_checks'],['local_lock_timeout'])
  self.assertEqual(f.control.check('audit')['failed_checks'],['unsupported_retained_failure_class'])
  self.assertEqual(before,{str(p):p.read_bytes() for p in f.store.root.rglob('*') if p.is_file()});f.calls.assert_not_called()

class ObservationTests(PhysicalCleanupTests):
 def test_unknown_later_compute_row_fails_immediately(self):
  self.prepare();self.apps=lambda:GPU1+',9999,256\n'
  with self.assertRaisesRegex(RecoveryRequired,'unknown_or_duplicate_gpu_process'):self.guard.sample(2)
  self.assertLess(self.clock.t,2)
 def test_duplicate_known_row_same_failure_label(self):
  self.prepare();self.apps=lambda:(GPU1+',304,96\n')*2
  with self.assertRaisesRegex(RecoveryRequired,'unknown_or_duplicate_gpu_process'):self.guard.sample(2)

class RealProcessTests(unittest.TestCase):
 def test_real_exit_birth_and_procps_zombie(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);script="import os,time;from pathlib import Path;p=os.fork();\nif p==0:time.sleep(.15);os._exit(0)\nPath('pid').write_text(str(p))\nwhile not Path('reap').exists():time.sleep(.01)\nos.waitpid(p,0)\n"
   parent=subprocess.Popen([sys.executable,'-B','-c',script],cwd=d)
   try:
    end=time.monotonic()+4
    while not (root/'pid').exists() and time.monotonic()<end:time.sleep(.01)
    pid=(root/'pid').read_text();birth=process_birth(pid)
    while time.monotonic()<end:
     raw=subprocess.check_output(['ps','-ww','-p',pid,'-o','pid,ppid,pidns,exe:4096,args'],text=True)
     if '<defunct>' in raw:break
     time.sleep(.01)
    parsed=rows(raw);self.assertEqual(process_birth(pid),birth)
    self.assertIn('<defunct>',raw);self.assertIn('-',raw);self.assertTrue(parsed)
    (root/'reap').touch();parent.wait(timeout=4)
    with self.assertRaises(ProcessDisappeared) as e:process_birth(pid)
    self.assertIn(e.exception.errno,(errno.ENOENT,errno.ESRCH))
   finally:
    if parent.poll() is None:(root/'reap').touch();parent.wait(timeout=4)
 def test_real_process_flock_contention_then_release(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'lock';p.touch()
   child=subprocess.Popen([sys.executable,'-B','-c',"import fcntl,sys;f=open(sys.argv[1],'r+');fcntl.flock(f,fcntl.LOCK_EX);print('locked',flush=True);sys.stdin.readline()",str(p)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
   try:
    self.assertEqual(child.stdout.readline().strip(),'locked')
    with self.assertRaisesRegex(TimeoutError,'local_lock_timeout'):
     with NativeLock(p,create=False).held(0):pass
    child.communicate('\n',timeout=4)
    with NativeLock(p,create=False).held(0):pass
   finally:
    if child.poll() is None:child.communicate('\n',timeout=4)

if __name__=='__main__':unittest.main()
