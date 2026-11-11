"""Fixed installer rehearsal with temp files and fully stubbed external actions."""
import contextlib,hashlib,importlib.util,io,json,subprocess,sys,tempfile,types,unittest,venv,zipfile
from pathlib import Path
from unittest.mock import patch

SPEC=importlib.util.spec_from_file_location('probe_install',Path(__file__).resolve().parents[1]/'install_cpu_successor.py')
M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)

class InstallationTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.r=Path(self.t.name)
  self.pkg=self.r/'package';self.pkg.mkdir();self.old=self.r/'original';self.old.mkdir();(self.old/'preserve').write_bytes(b'original release')
  self.new=self.r/'successor';self.e=self.r/'evidence';self.drop=self.r/'unit.conf';self.state=self.r/'state';self.state.mkdir()
  self.before=dict(recovery_policy={'policy_id':'preserved','policy':{}},lock_identity={'gpu0':'original','gpu1':'original'},music_contract_transitions=['historical'])
  (self.state/'ownership.json').write_text(json.dumps(dict(self.before,owners={})))
  for name,value in [('OLD',self.old),('NEW',self.new),('E',self.e),('DROP',self.drop),('S',self.state),('__file__',str(self.pkg/'install_cpu_successor.py'))]:
   p=patch.object(M,name,value);p.start();self.addCleanup(p.stop)
  self.plan=dict(release='ollama-probe-compatibility-20261110.1',lease='4b6f6cd0993e43ff90c5c8c1c679fbe4',wheel='fixed.whl',wheel_sha256=hashlib.sha256(b'wheel').hexdigest(),recovery_script_sha256=hashlib.sha256(b'inert').hexdigest(),recovery_plan_sha256=hashlib.sha256(b'{}').hexdigest(),after_modules={},before_modules={},dropin='[Service]\nExecStart=\nExecStart=/reviewed/successor\n')
  for name,data in [('fixed.whl',b'wheel'),('recover-held.py',b'inert'),('recovery-review.json',b'{}')]:(self.pkg/name).write_bytes(data)
  self.write_plan();self.calls=[];self.fail=None
  self.inspection=patch.object(M,'inspect',return_value=(self.before,{},{})).start();self.addCleanup(patch.stopall)
  patch.object(M,'sys',types.SimpleNamespace(prefix=str(self.old),flags=types.SimpleNamespace(isolated=True))).start()
  patch.object(M.os,'geteuid',return_value=0).start()
  patch.object(M,'hashes',return_value={}).start()
  patch.object(M,'NativeLock',side_effect=lambda *a,**k:types.SimpleNamespace(held=lambda **kw:contextlib.nullcontext())).start()
  patch.object(M,'RecoveryObserver',return_value=types.SimpleNamespace(stopped_ready=lambda *a,**k:{'idle_ready':True,'stable_samples':3})).start()
  patch.object(M,'load_config',return_value=({'auth_token_env':'TEST_TOKEN','listen':{'port':1}},None,None,None,None)).start()
  patch.object(M,'configured_backends',return_value={}).start()
  patch.object(M.urllib.request,'urlopen',return_value=io.BytesIO(b'{"ready":true,"owners":{}}')).start()
  original=Path.read_text
  patch.object(Path,'read_text',lambda path,*a,**kw:'TEST_TOKEN=private-never-printed\n' if str(path)=='/home/AIHUB_USER/.config/aihub-gpu-runner/runner.env' else original(path,*a,**kw)).start()
  patch.object(M,'checked',side_effect=self.checked).start()
  patch.object(M,'install_pure_wheel').start()
 def write_plan(self):
  (self.pkg/'review-plan.json').write_text(json.dumps(self.plan))
  manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.pkg.iterdir() if p.name!='manifest.json'}
  (self.pkg/'manifest.json').write_text(json.dumps(manifest))
  self.args=['install','--authorized-plan',M.sha(self.pkg/'review-plan.json'),'--expected-manifest',M.sha(self.pkg/'manifest.json')]
 def checked(self,argv,**kw):
  self.calls.append(argv)
  if self.fail and self.fail(argv):raise RuntimeError('uncertain STOP')
  if argv[:2]==['systemctl','show']:return b'0\n'
  return b'{}'
 def run_install(self):
  with patch.object(sys,'argv',self.args),contextlib.redirect_stdout(io.StringIO()):M.main()
 def test_normal_install_uses_old_authority_once_and_one_cpu_stop_start_only(self):
  self.run_install();self.assertEqual(sum(a[0]=='/usr/sbin/runuser' for a in self.calls),1)
  self.assertEqual(sum(a[:2]==['systemctl','stop'] for a in self.calls),1);self.assertEqual(sum(a[:2]==['systemctl','start'] for a in self.calls),1)
  self.assertEqual(self.drop.read_text(),self.plan['dropin']);self.assertEqual((self.old/'preserve').read_bytes(),b'original release')
  self.assertFalse(any('docker' in a or 'nvidia-smi' in a for a in self.calls))
 def test_duplicate_refused_before_any_command(self):
  self.e.mkdir()
  with self.assertRaises(AssertionError):self.run_install()
  self.assertEqual(self.calls,[])
 def test_source_manifest_drift_refused_before_any_command(self):
  (self.pkg/'fixed.whl').write_bytes(b'changed')
  with self.assertRaises(AssertionError):self.run_install()
  self.assertFalse(self.e.exists());self.assertEqual(self.calls,[])
 def test_fresh_preflight_refusal_has_no_effect(self):
  self.inspection.side_effect=AssertionError('active work or drift')
  with self.assertRaises(AssertionError):self.run_install()
  self.assertFalse(self.e.exists());self.assertEqual(self.calls,[])
 def test_uncertain_authority_never_stops_service_or_retries(self):
  self.fail=lambda a:a[0]=='/usr/sbin/runuser'
  with self.assertRaises(RuntimeError):self.run_install()
  self.assertFalse(any(a[:2]==['systemctl','stop'] for a in self.calls));self.assertTrue((self.e/'exact-authority-intent.json').exists())
  with self.assertRaises(AssertionError):self.run_install()
  self.assertEqual(sum(a[0]=='/usr/sbin/runuser' for a in self.calls),1)
 def test_new_work_after_recovery_refuses_stop(self):
  self.inspection.side_effect=[(self.before,{},{}),(self.before,{},{}),AssertionError('new work')]
  with self.assertRaises(AssertionError):self.run_install()
  self.assertFalse(any(a[:2]==['systemctl','stop'] for a in self.calls));self.assertFalse(self.drop.exists())
 def test_interruption_after_dropin_preserves_original_and_never_restarts_automatically(self):
  self.fail=lambda a:a[:2]==['systemctl','start']
  with self.assertRaises(RuntimeError):self.run_install()
  self.assertTrue(self.drop.exists());self.assertTrue((self.e/'cpu-start-intent.json').exists())
  self.assertEqual((self.old/'preserve').read_bytes(),b'original release')
  with self.assertRaises(AssertionError):self.run_install()
 def test_policy_drift_after_start_is_reported_without_journal_edits(self):
  changed=dict(self.before,recovery_policy={'policy_id':'changed'},owners={})
  (self.state/'ownership.json').write_text(json.dumps(changed))
  with self.assertRaises(AssertionError):self.run_install()
  self.assertEqual(json.loads((self.state/'ownership.json').read_bytes()),changed)

class OfflineWheelTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.r=Path(self.t.name)
  self.wheel=next((Path(__file__).resolve().parents[1]/'reviewed-dist').glob('*.whl'));self.site=self.r/'site'
 def altered(self,kind):
  with zipfile.ZipFile(self.wheel) as z:data={n:z.read(n) for n in z.namelist()}
  if kind=='checksum':data['aihub_gpu_runner/ollama_cleanup.py']+=b'\n# altered\n'
  elif kind=='traversal':data['../escape.py']=b'unsafe'
  elif kind=='pth':data['aihub_gpu_runner/injected.pth']=b'import os'
  elif kind=='record':data[next(n for n in data if n.endswith('/RECORD'))]+=b'aihub_gpu_runner/ollama_cleanup.py,,\n'
  w=self.r/'altered.whl'
  with zipfile.ZipFile(w,'w') as z:
   for n,b in data.items():z.writestr(n,b)
  return w
 def test_actual_isolated_without_pip_venv_imports_exact_sdk_and_distribution(self):
  venv.EnvBuilder(with_pip=False).create(self.r/'venv');p=self.r/'venv/bin/python'
  self.site=self.r/'venv/lib'/('python'+str(sys.version_info.major)+'.'+str(sys.version_info.minor))/'site-packages'
  M.install_pure_wheel(self.wheel,self.site)
  result=subprocess.run([str(p),'-I','-B','-c',"import importlib.metadata,aihub_gpu_runner,aihub_gpu_runner.ollama_probe;print(importlib.metadata.version('aihub-gpu-runner'))"],capture_output=True,text=True)
  self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'0.1.0+ollama.probe.compat.20261110.1')
  self.assertEqual(M.hashes(self.site/'aihub_gpu_runner'),M.hashes(Path(__file__).resolve().parents[1]/'qualification/original-installed-source'))
  self.assertFalse((self.r/'venv/bin/pip').exists())
 def test_checksum_corruption_refused_before_writes(self):
  with self.assertRaises(AssertionError):M.install_pure_wheel(self.altered('checksum'),self.site)
  self.assertFalse(self.site.exists())
 def test_path_escape_refused_before_writes(self):
  with self.assertRaises(AssertionError):M.install_pure_wheel(self.altered('traversal'),self.site)
  self.assertFalse(self.site.exists());self.assertFalse((self.r/'escape.py').exists())
 def test_extra_pth_refused_before_writes(self):
  with self.assertRaises(AssertionError):M.install_pure_wheel(self.altered('pth'),self.site)
  self.assertFalse(self.site.exists())
 def test_duplicate_record_refused_before_writes(self):
  with self.assertRaises(AssertionError):M.install_pure_wheel(self.altered('record'),self.site)
  self.assertFalse(self.site.exists())
 def test_second_install_cannot_replace_any_existing_runtime_file(self):
  M.install_pure_wheel(self.wheel,self.site);before=M.hashes(self.site/'aihub_gpu_runner')
  with self.assertRaises(AssertionError):M.install_pure_wheel(self.wheel,self.site)
  self.assertEqual(M.hashes(self.site/'aihub_gpu_runner'),before)

if __name__=='__main__':unittest.main()
