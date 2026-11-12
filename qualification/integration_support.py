"""Real ComfyUI CPU process + native runner. Only GPU telemetry is simulated."""
import copy
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
if os.environ.get('AIHUB_REQUIRE_INSTALLED_RUNNER')=='1':sys.path.append(str(ROOT))
else:sys.path.insert(0,str(ROOT))
from aihub_gpu_runner.core import Admission,AtomicStore,Runner,RecoveryRequired
from aihub_gpu_runner.backends import Target,ComfyBackend,HTTPTransport
from aihub_gpu_runner.recovery import RecoveryManager,authorize_policy
from aihub_gpu_runner.integrated_runtime import IntegratedApplication
from aihub_gpu_runner.target_barrier import TargetBarrier,birth
from tests.test_automatic_recovery import policy
from tests.test_wallpaper_conditioning import WORKFLOW

BASE=Path(os.environ.get('AIHUB_QUALIFICATION_ROOT',str(Path(tempfile.gettempdir())/'aihub-reliability-qualification')))
BASE.mkdir(parents=True,exist_ok=True)
PY=Path(os.environ.get('AIHUB_QUALIFICATION_PYTHON',sys.executable))
COMFY=Path(os.environ.get('AIHUB_COMFY_SOURCE',str(BASE/'ComfyUI')))
COMFY_PY=Path(os.environ.get('AIHUB_COMFY_PYTHON',str(PY)))
RESULTS=Path(os.environ.get('AIHUB_QUALIFICATION_RESULTS',str(BASE/'results')))
RESULTS.mkdir(parents=True,exist_ok=True)
import aihub_gpu_runner
if os.environ.get('AIHUB_REQUIRE_INSTALLED_RUNNER')=='1' and Path(aihub_gpu_runner.__file__).is_relative_to(ROOT):
    raise RuntimeError('installed_runner_required_checkout_import_refused')
(RESULTS/'runner-import-origin.json').write_text(json.dumps({'origin':aihub_gpu_runner.__file__,
    'installed_required':os.environ.get('AIHUB_REQUIRE_INSTALLED_RUNNER')=='1'},indent=2))

def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def wait(predicate,seconds=20):
    end=time.monotonic()+seconds
    last=None
    while time.monotonic()<end:
        try:
            last=predicate()
            if last:return last
        except (OSError,KeyError,ValueError):pass
        time.sleep(.03)
    raise AssertionError('wait timed out: '+str(last))

class Comfy:
    def __init__(self,root,stubborn=False):
        self.root=root;self.proc=None;self.stubborn=stubborn
        self.port=port();self.url='http://127.0.0.1:'+str(self.port)
        self.directory=root/'barrier';self.directory.mkdir()
        (self.directory/'submission.key').write_bytes(os.urandom(32))
        self.barrier=TargetBarrier(self.directory,create=True)
        plugin_source=ROOT/'comfy_nodes/aihub_recovery'
        if os.environ.get('AIHUB_COMFY_PLUGIN_WHEEL'):
            plugin_source=BASE/'comfy-plugin-under-test';plugin_source.mkdir(exist_ok=True)
            shutil.copy2(ROOT/'comfy_nodes/aihub_recovery/__init__.py',plugin_source/'__init__.py')
            shutil.copy2(os.environ['AIHUB_COMFY_PLUGIN_WHEEL'],plugin_source/'runner.whl')
        for name,source in [('aihub_recovery',plugin_source),('fixture_recovery',ROOT/'qualification/fixture_nodes')]:
            target=COMFY/'custom_nodes'/name
            if target.is_symlink() and target.resolve()!=source.resolve():target.unlink()
            if not target.exists():target.symlink_to(source,target_is_directory=True)
        self.log=(root/'comfy.log').open('w')
        self.transport=HTTPTransport(self.url)
    def start(self):
        if self.proc and self.proc.poll() is None:raise AssertionError('No replay/start of existing execution')
        for name in ('user','input','output'):(self.root/name).mkdir(exist_ok=True)
        env=dict(os.environ,PYTHONPATH=str(ROOT),CUDA_VISIBLE_DEVICES='',AIHUB_WALLPAPER_BARRIER=str(self.directory),
                 AIHUB_QUALIFICATION_IMPORTS=str(self.root/'plugin-imports.json'),
                 QUALIFICATION_IGNORE_TERM='native' if self.stubborn=='native' else '1' if self.stubborn else '0')
        self.proc=subprocess.Popen([str(COMFY_PY),'-B','main.py','--cpu','--listen','127.0.0.1','--port',str(self.port),
            '--disable-auto-launch','--disable-api-nodes','--disable-all-custom-nodes','--whitelist-custom-nodes',
            'aihub_recovery','fixture_recovery','--user-directory',str(self.root/'user'),
            '--output-directory',str(self.root/'output'),'--input-directory',str(self.root/'input'),
            '--disable-assets-autoscan','--temp-directory',str(self.root/'temp'),
            '--database-url','sqlite:///'+str(self.root/'comfy.db')],
            cwd=COMFY,env=env,stdout=self.log,stderr=subprocess.STDOUT)
        def ready():
            if self.proc.poll() is not None:raise AssertionError((self.root/'comfy.log').read_text()[-6000:])
            try:return self.transport.json('GET','/aihub/recovery/status',timeout=.3)
            except Exception:return False
        wait(ready,90)
    def close(self):
        if self.proc and self.proc.poll() is None:self.proc.kill();self.proc.wait(10)
        self.log.close()
    def container(self):
        alive=self.proc and birth(self.proc.pid)
        return dict(id='a'*64,contract_sha256='d'*64,started_at=birth(self.proc.pid) if alive else self.started_at if hasattr(self,'started_at') else 'cold',
                    pid=self.proc.pid if alive else 0,running=bool(alive))
    def manual(self,seconds=0):
        return self.transport.json('POST','/prompt',{'prompt':{'2':{'class_type':'FixtureOutput','inputs':{'seconds':seconds}}}})['prompt_id']

class ProcessObserver:
    """Actual process/HTTP observer; CPU capacity stands in for physical GPUs."""
    def __init__(self,comfy):self.comfy=comfy;self.prestart_error=None;self.health=0
    def container(self,service,deadline):return self.comfy.container()
    def start(self,identity,deadline):self.comfy.start();self.comfy.started_at=birth(self.comfy.proc.pid)
    def processes(self,container,deadline):return {str(container['pid']):birth(container['pid'])} if container['running'] else {}
    def placement(self,*args):return {'qualification':'CPU; no physical GPU claim'}
    def ready(self,*args):return {'idle_ready':True,'qualification':'simulated capacity only'}
    def stopped_ready(self,*args):
        if self.prestart_error:raise RecoveryRequired(self.prestart_error)
        return {'idle_ready':True,'backend_stopped':True,'cleanup_verified':True}
    def validate_static(self,*args):pass
    def signal(self,*args):raise AssertionError('Legacy stop mechanism must not be called')

class Environment:
    def __init__(self,seconds=0,stubborn=False,automatic=True,comfy_class=Comfy,allow_force=True,execution_timeout=300):
        self.tmp=tempfile.TemporaryDirectory(prefix='real-recovery-',dir=BASE)
        self.root=Path(self.tmp.name);self.comfy=comfy_class(self.root,stubborn)
        self.store=AtomicStore(self.root/'state')
        self.a=Admission(self.store,{r:self.root/(r+'.lock') for r in ('gpu0','gpu1')},'cpu-disposable',control_required=True)
        self.a.bootstrap(dict(approval_ref='DISPOSABLE ONLY',bootstrap_id=self.a.snapshot()['bootstrap_id'],backend_quiescent=True,cleanup_verified=True))
        from aihub_gpu_runner.maintenance import initialize
        initialize(self.a)
        self.settings=dict(schema=1,automatic_no_start=automatic,destructive=True,target_kind='process',qualification_process=True,
            barrier_dir=str(self.comfy.directory),grace_seconds=.2)
        for k in ('read_token','operate_token','ticket_key'):
            path=self.root/(k+'.secret');path.write_text(os.urandom(32).hex());self.settings[k+'_file']=str(path)
        self.a.integrated_settings=self.settings;self.a.automatic_no_start_enabled=automatic
        workflow=copy.deepcopy(WORKFLOW);workflow['2']={'class_type':'FixtureOutput','inputs':{'seconds':seconds}}
        self.target=Target('wallpaper','comfy',('gpu0','gpu1'),self.comfy.url,workflow=workflow,output_node='2',
            idle_memory_mb=450,conditioning_idle_memory_mb=450,lifecycle='on_demand',startup_timeout=100,
            shutdown_timeout=10,execution_timeout=execution_timeout,acquisition_timeout=5)
        self.backend=ComfyBackend(self.target,HTTPTransport(self.comfy.url),lambda:0,conditioning_probe=lambda:0,poll_interval=.03)
        self.observer=ProcessObserver(self.comfy)
        p=policy(self.a);p.update(boot_startup='stopped_idle',allow_force=allow_force,stopped_idle_memory_mb={'gpu0':450,'gpu1':450})
        if hasattr(self.comfy,'configure'):
            self.comfy.configure(self,p)
        authorize_policy(self.a,{'wallpaper':self.target},{'wallpaper':self.backend},p,'DISPOSABLE ONLY',self.observer)
        if os.environ.get('AIHUB_QUALIFY_GENERATIONS')=='1':
            from aihub_gpu_runner.estop import activate_generations
            activate_generations(self.a,confirmed_coordinated_shutdown=True)
        self.manager=RecoveryManager(self.a,{'wallpaper':self.target},{'wallpaper':self.backend},self.observer)
        self.runner=Runner(self.a,{'wallpaper':self.target},{'wallpaper':self.backend},recovery=self.manager,poll_interval=.02)
        self.app=IntegratedApplication(self.runner,self.settings,'job-only-token-0123456789')
        self.runner.recovery_application=self.app;self.control=self.app.control
    def submit(self,name='owned'):
        self.runner.submit(name,'wallpaper','workflow',self.target.workflow)
        return name
    def active(self,name='owned'):
        def ready():
            job=self.store.job(name)
            if self.runner.future(name).done():raise AssertionError(job)
            prompt=job.get('backend_detail',{}).get('prompt_id')
            if not prompt:return False
            s=self.comfy.barrier.read()
            return prompt if prompt in s['running'].values() and not s['inflight'] else False
        return wait(ready,100)
    def close(self):
        self.comfy.close()
        self.runner.close()
        destination=RESULTS/'real-comfy-last.log'
        shutil.copyfile(self.root/'comfy.log',destination)
        if (self.root/'plugin-imports.json').exists():shutil.copy2(self.root/'plugin-imports.json',RESULTS/'comfy-plugin-import-origins.json')
        self.tmp.cleanup()
    def reopen(self):
        self.manager=RecoveryManager(self.a,{'wallpaper':self.target},{'wallpaper':self.backend},self.observer)
        self.runner=Runner(self.a,{'wallpaper':self.target},{'wallpaper':self.backend},recovery=self.manager,poll_interval=.02)
        self.app=IntegratedApplication(self.runner,self.settings,'job-only-token-0123456789')
        self.runner.recovery_application=self.app;self.control=self.app.control
