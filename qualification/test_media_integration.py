"""Actual retained Media app + TLS HA bridge + runner HTTP/native authority."""
import asyncio,importlib.util,json,os,subprocess,sys,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
from integration_support import Environment,ROOT,PY,RESULTS,port,wait
from aihub_gpu_runner.api import Application,serve
from aihub_gpu_runner.core import RecoveryRequired

spec=importlib.util.spec_from_file_location('actual_ha_bridge',ROOT/'home_assistant/custom_components/aihub_recovery/bridge.py')
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)

class MediaIntegration(unittest.TestCase):
    def make_environment(self):return Environment(automatic=False)
    def setUp(self):
        self.e=self.make_environment();self.addCleanup(self.e.close)
        self.servers=[]
        app=Application(self.e.runner,'job-only-token-disposable-0123456789-0123456789')
        threading.Thread(target=serve,args=(app,'127.0.0.1',0),kwargs={'started':self.servers.append},daemon=True).start()
        wait(lambda:self.servers)
        self.addCleanup(self.servers[0].shutdown)
        runner='http://127.0.0.1:'+str(self.servers[0].server_port)
        self.runner_url=runner
        settings={'runner_origin':runner}
        for name in ('ha_read','ha_operate'):
            f=self.e.root/(name+'.secret');f.write_text(os.urandom(32).hex());settings[name+'_file']=str(f)
        settings.update(runner_read_file=self.e.settings['read_token_file'],runner_operate_file=self.e.settings['operate_token_file'])
        config=self.e.root/'media.json';config.write_text(json.dumps(settings))
        cert=self.e.root/'tls.crt';key=self.e.root/'tls.key'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),
            '-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],check=True,capture_output=True)
        media_port=port();origin='https://localhost:'+str(media_port)
        source=Path(os.environ['AIHUB_MEDIA_SOURCE']).resolve()
        if not (source/'recovery_media.py').is_file():raise AssertionError('actual Media source required')
        media_python=Path(os.environ.get('AIHUB_MEDIA_PYTHON',str(PY)))
        paths=[source] if os.environ.get('AIHUB_REQUIRE_INSTALLED_RUNNER')=='1' else [source,ROOT]
        env=dict(os.environ,PYTHONPATH=os.pathsep.join(map(str,paths)),
            AIHUB_MEDIA_RECOVERY_CONFIG=str(config),HA_URL='http://127.0.0.1:1',HA_TOKEN='disposable-ha-only',
            CALENDAR_ENTITY='calendar.fixture',OLLAMA_URL='http://127.0.0.1:1',COMFY_URL='http://127.0.0.1:1',
            GPU_RUNNER_URL=runner,GPU_RUNNER_TOKEN='job-only-token-disposable-0123456789-0123456789',RECOVERY_ACK_TOKEN='ack-only-disposable-token-'+os.urandom(32).hex(),
            GPU_RUNNER_CLIENT_STATE=str(self.e.root/'media-state'),OUT_PATH=str(self.e.root/'wallpaper.png'))
        self.media_env=env;self.media_python=media_python
        origins=subprocess.check_output([str(media_python),'-B','-c',
            'import json,recovery_media,main,fastapi,aihub_gpu_runner; print(json.dumps({m.__name__:m.__file__ for m in (recovery_media,main,fastapi,aihub_gpu_runner)}))'],
            cwd=self.e.root,env=env,text=True)
        (RESULTS/'media-import-origins.json').write_text(origins)
        self.log=(self.e.root/'media.log').open('w');self.addCleanup(self.log.close)
        self.media=subprocess.Popen([str(self.media_python),'-B','-m','uvicorn','recovery_media:app','--host','127.0.0.1','--port',str(media_port),
            '--ssl-certfile',str(cert),'--ssl-keyfile',str(key),'--timeout-graceful-shutdown','2'],cwd=self.e.root,env=env,stdout=self.log,stderr=subprocess.STDOUT)
        self.addCleanup(self.close_media)
        self.bridge=bridge.Bridge(dict(transport='media',url=origin+'/gpu-recovery',ca_file=str(cert),
            read_token=Path(settings['ha_read_file']).read_text(),operate_token=Path(settings['ha_operate_file']).read_text()))
        def ready():
            if self.media.poll() is not None:raise AssertionError((self.e.root/'media.log').read_text())
            try:return self.bridge.request('status','human')
            except Exception:return False
        wait(ready,30)

    def close_media(self):
        import shutil
        if self.media.poll() is None:
            self.media.terminate()
            try:self.media.wait(5)
            except subprocess.TimeoutExpired:self.media.kill();self.media.wait(5)
        self.log.flush()
        shutil.copyfile(self.e.root/'media.log',RESULTS/'real-media-last.log')

    async def dispatch(self,action,**fields):
        class Connection:
            user=SimpleNamespace(id='human',is_admin=True,is_active=True,system_generated=False)
            def send_result(self,id,value):self.result=value
            def send_error(self,id,code,message):self.result={'error':code,'message':message}
        c=Connection()
        async def executor(fn,*args):return await asyncio.to_thread(fn,*args)
        hass=SimpleNamespace(data={'aihub_recovery':self.bridge},async_add_executor_job=executor)
        await bridge.dispatch(hass,c,dict(id=1,type='aihub_recovery/control',action=action,**fields))
        return c.result

    def test_ha_media_runner_emergency_and_lost_response_verification(self):
        self.e.target.workflow['2']['inputs']['seconds']=120
        self.e.submit();self.e.active()
        checked=asyncio.run(self.dispatch('check-emergency'));self.assertTrue(checked['eligible'],checked)
        result=asyncio.run(self.dispatch('emergency',token=checked['token'],confirmed=True,accept_work_loss=True))
        self.assertEqual(result['result'],'released_readiness_pending',result)
        # Discard the original response: durable status supplies the operation.
        status=asyncio.run(self.dispatch('status'));self.assertEqual(status['attempt']['id'],checked['operation'])
        verified=asyncio.run(self.dispatch('verify',operation=status['attempt']['id']))
        self.assertEqual(verified['result'],result['result'])
        duplicate=asyncio.run(self.dispatch('emergency',token=checked['token'],confirmed=True,accept_work_loss=True))
        self.assertEqual(duplicate['error'],'recovery_refused')

    def test_ha_media_runner_metadata_only_unknown_capacity(self):
        self.e.observer.prestart_error='music_454_gt_450';self.e.submit();self.e.runner.future('owned').result(5)
        checked=asyncio.run(self.dispatch('check'));self.assertTrue(checked['eligible'],checked)
        result=asyncio.run(self.dispatch('recover',token=checked['token'],confirmed=True))
        self.assertEqual(result['result'],'released_readiness_pending',result)
        status=asyncio.run(self.dispatch('status'));self.assertTrue(status['admission_ready']);self.assertIsNone(status['readiness'])

    def test_auth_scope_unchanged_ack_and_unavailable_runner(self):
        import urllib.request,urllib.error
        # Job credentials have no recovery authority; scoped recovery token has
        # no inference/job authority even through the actual HTTP body reader.
        for path,token in [('/recovery-control/status','job-only-token-disposable-0123456789-0123456789'),
            ('/jobs',Path(self.e.settings['operate_token_file']).read_text())]:
            req=urllib.request.Request(self.runner_url+path,data=b'{}',headers={'Authorization':'Bearer '+token,'X-HA-User':'human'})
            with self.assertRaises(urllib.error.HTTPError) as exc:urllib.request.urlopen(req,timeout=3)
            self.assertEqual(exc.exception.code,401)
        req=urllib.request.Request(self.bridge.url.replace('/gpu-recovery','/recovery-counter/acknowledge'),data=b'{}')
        with self.assertRaises(urllib.error.HTTPError) as exc:self.bridge.opener.open(req,timeout=3)
        self.assertEqual(exc.exception.code,401)
        self.servers[0].shutdown();self.servers[0].server_close()
        result=asyncio.run(self.dispatch('status'));self.assertEqual(result['error'],'runner_response_unavailable')

    def test_explicit_private_http_media_transport(self):
        # Qualify the existing private-network Media deployment mode too.
        # This process is separate from the TLS transport already under test.
        import urllib.request,urllib.error
        media_port=port()
        with (self.e.root/'media-private-http.log').open('w') as log:
            process=subprocess.Popen([str(self.media_python),'-B','-m','uvicorn','recovery_media:app','--host','127.0.0.1',
                '--port',str(media_port),'--timeout-graceful-shutdown','2'],cwd=self.e.root,env=self.media_env,
                stdout=log,stderr=subprocess.STDOUT)
            try:
                private=bridge.Bridge(dict(self.bridge.settings,url='http://127.0.0.1:'+str(media_port)+'/gpu-recovery',
                    allow_private_http=True))
                def ready():
                    if process.poll() is not None:raise AssertionError('disposable Media server exited')
                    try:return private.request('status','human')
                    except Exception:return False
                self.assertTrue(wait(ready,30)['integrated'])
                self.e.observer.prestart_error='music454_gt450';self.e.submit();self.e.runner.future('owned').result(5)
                checked=private.request('check','human')
                self.assertTrue(checked['eligible'],checked)
                recovered=private.request('recover','human',{'token':checked['token'],'confirmed':True})
                self.assertEqual(recovered['result'],'released_readiness_pending',recovered)
                for url in ('http://192.0.2.1/gpu-recovery','http://example.com/gpu-recovery'):
                    with self.assertRaises(ValueError):bridge.Bridge(dict(private.settings,url=url))
                with self.assertRaises(ValueError):bridge.Bridge(dict(private.settings,allow_private_http=False))
            finally:
                process.terminate()
                try:process.wait(5)
                except subprocess.TimeoutExpired:process.kill();process.wait(5)

if __name__=='__main__':unittest.main(verbosity=2)
