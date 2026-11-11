"""Actual Home Assistant Core process, real WS/auth, Media and native runner fixture.

No GPU or production endpoints. GPU observations in the authority fixture are synthetic.
"""
import asyncio,json,os,subprocess,sys,time,unittest,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from integration_support import ROOT,BASE,PY,RESULTS,port,wait
from test_media_integration import MediaIntegration

class HACoreIntegration(MediaIntegration):
    def setUp(self):
        super().setUp()
        root=self.e.root/'ha';root.mkdir();(root/'custom_components').mkdir()
        import shutil
        shutil.copytree(ROOT/'home_assistant/custom_components/aihub_recovery',root/'custom_components/aihub_recovery')
        (root/'www').mkdir()
        shutil.copy2(ROOT/'home_assistant/www/aihub-recovery-card.js',root/'www/aihub-recovery-card.js')
        (root/'www/recovery-qualification.html').write_text('<!doctype html><title>Disposable recovery qualification</title><body></body>')
        self.ha_port=port();self.ha_url='http://127.0.0.1:'+str(self.ha_port)
        settings=self.bridge.settings
        config={'homeassistant':{'name':'Disposable recovery qualification','latitude':0,'longitude':0,'elevation':0,
            'unit_system':'metric','currency':'USD','time_zone':'UTC'},
            'http':{'server_host':'127.0.0.1','server_port':self.ha_port},
            'api':{},'websocket_api':{},'onboarding':{},'frontend':{},'config':{},
            'aihub_recovery':dict(transport='media',url=settings['url'],ca_file=str(self.e.root/'tls.crt'),
                read_token=settings['read_token'],operate_token=settings['operate_token'])}
        # JSON is a strict YAML subset and preserves the exact secret strings.
        (root/'configuration.yaml').write_text(json.dumps(config))
        self.ha_log=(self.e.root/'ha.log').open('w')
        self.ha=subprocess.Popen([str(PY),'-B','-m','homeassistant','--config',str(root)],
            cwd=self.e.root,env=dict(os.environ,CUDA_VISIBLE_DEVICES=''),
            stdout=self.ha_log,stderr=subprocess.STDOUT)
        self.addCleanup(self.close_ha)
        import requests
        def ready():
            if self.ha.poll() is not None:raise AssertionError((self.e.root/'ha.log').read_text()[-8000:])
            try:return requests.get(self.ha_url+'/api/onboarding',timeout=1).status_code==200
            except requests.RequestException:return False
        wait(ready,120)
        client=self.ha_url+'/'
        response=requests.post(self.ha_url+'/api/onboarding/users',json=dict(name='Recovery Qualifier',username='fixture',
            password=uuid.uuid4().hex+uuid.uuid4().hex,language='en',client_id=client),timeout=20)
        self.assertEqual(response.status_code,200,response.text)
        token=requests.post(self.ha_url+'/auth/token',data=dict(grant_type='authorization_code',
            code=response.json()['auth_code'],client_id=client),timeout=10)
        self.assertEqual(token.status_code,200,token.text);self.ha_token=token.json()['access_token']
        def loaded():
            try:
                status=requests.get(self.ha_url+'/api/config',headers={'Authorization':'Bearer '+self.ha_token},timeout=2)
                return status.status_code==200 and 'aihub_recovery' in status.json().get('components',[])
            except requests.RequestException:return False
        wait(loaded,120)  # Onboarding HTTP starts before all integrations finish.
        origins=subprocess.check_output([str(PY),'-c',
            'import json,homeassistant,importlib.metadata; print(json.dumps({"version":importlib.metadata.version("homeassistant"),"origin":homeassistant.__file__}))'],text=True)
        (RESULTS/'ha-import-origin.json').write_text(origins)
    def close_ha(self):
        if self.ha.poll() is None:
            self.ha.terminate()
            try:self.ha.wait(20)
            except subprocess.TimeoutExpired:self.ha.kill();self.ha.wait(5)
        self.ha_log.close()
        import shutil
        shutil.copyfile(self.e.root/'ha.log',RESULTS/'real-ha-last.log')

    def test_actual_ha_websocket_metadata_and_emergency_lost_response(self):
        self.e.observer.prestart_error='music454_gt450';self.e.submit();self.e.runner.future('owned').result(5)
        async def qualify():
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async def connection():
                    ws=await session.ws_connect(self.ha_url+'/api/websocket')
                    self.assertEqual((await ws.receive_json())['type'],'auth_required')
                    await ws.send_json({'type':'auth','access_token':self.ha_token})
                    self.assertEqual((await ws.receive_json())['type'],'auth_ok');return ws
                ws=await connection();counter=0
                async def action(action,**kwargs):
                    nonlocal counter
                    counter+=1
                    await ws.send_json(dict(id=counter,type='aihub_recovery/control',action=action,**kwargs))
                    value=await ws.receive_json(timeout=90)
                    self.assertTrue(value.get('success'),value);return value['result']
                checked=await action('check');self.assertTrue(checked['eligible'],checked)
                result=await action('recover',token=checked['token'],confirmed=True)
                self.assertEqual(result['result'],'released_readiness_pending',result)
                self.assertTrue((await action('status'))['admission_ready'])
                self.e.observer.prestart_error=None
                self.e.target.workflow['2']['inputs']['seconds']=120
                self.e.submit('second');await asyncio.to_thread(self.e.active,'second')
                checked=await action('check-emergency');self.assertTrue(checked['eligible'],checked)
                counter+=1
                await ws.send_json(dict(id=counter,type='aihub_recovery/control',action='emergency',
                    token=checked['token'],confirmed=True,accept_work_loss=True))
                await ws.close()  # Lose only the response; never repeat the POST.
                ws=await connection();counter=0
                end=time.monotonic()+30
                while time.monotonic()<end:
                    status=await action('status')
                    if status.get('attempt',{}).get('status')=='succeeded':break
                    await asyncio.sleep(.1)
                else:self.fail(status)
                result=await action('verify',operation=checked['operation'])
                self.assertEqual(result['result'],'released_readiness_pending',result)
                duplicate=await action('emergency',token=checked['token'],confirmed=True,accept_work_loss=True)
                self.assertEqual(duplicate['error'],'recovery_refused')
                await ws.close()
        asyncio.run(qualify())

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([HACoreIntegration('test_actual_ha_websocket_metadata_and_emergency_lost_response')]))
    sys.exit(not result.wasSuccessful())
