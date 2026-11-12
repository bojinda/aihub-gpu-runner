"""Actual HA/Media/browser E-stop. CPU-only native journal/FD; no GPU backend."""
import asyncio,hashlib,json,os,sys,tempfile,unittest,uuid
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_ha_core_integration import HACoreIntegration
from integration_support import RESULTS,ROOT
from aihub_gpu_runner.core import AtomicStore,Admission,Runner,NativeLock
from aihub_gpu_runner.estop import activate_generations
from aihub_gpu_runner.maintenance import initialize
from aihub_gpu_runner.integrated_runtime import IntegratedApplication

MOUNT=r'''async ({token,lose})=>{
 await import('/local/aihub-recovery-card.js');window.calls=[];
 const callWS=message=>new Promise((resolve,reject)=>{
  const socket=new WebSocket(location.origin.replace('http','ws')+'/api/websocket');
  socket.onerror=()=>reject(Error('fixture unavailable'));
  socket.onmessage=event=>{
   const v=JSON.parse(event.data);
   if(v.type==='auth_required')socket.send(JSON.stringify({type:'auth',access_token:token}));
   else if(v.type==='auth_ok'){calls.push(message);socket.send(JSON.stringify({id:1,...message}));}
   else if(v.id===1){socket.close();if(!v.success)reject(Error(v.error?.message));
    else if(message.action==='estop' && lose)reject(Error('fixture lost POST response'));
    else {if(message.action==='status')v.result.attempt={id:'historical-recovery',status:'succeeded'};resolve(v.result);}}
  };
 });
 document.body.replaceChildren();window.card=document.createElement('aihub-recovery-card');
 card.setConfig({});document.body.append(card);card.hass={callWS};await card.initialRefresh;
}'''

class EstopHA(HACoreIntegration):
    def make_environment(self):
        tmp=tempfile.TemporaryDirectory(prefix='estop-ha-');root=Path(tmp.name);store=AtomicStore(root/'state')
        paths={r:root/'physical-locks'/(r+'.lock') for r in ('gpu0','gpu1')}
        for p in paths.values():p.parent.mkdir(exist_ok=True);p.touch()
        a=Admission(store,paths,'ha-estop-fixture',True,control_required=True)
        a.bootstrap(dict(approval_ref='fixture',bootstrap_id=a.snapshot()['bootstrap_id'],backend_quiescent=True,cleanup_verified=True))
        activate_generations(a,confirmed_coordinated_shutdown=True);initialize(a)
        runner=Runner(a,{}, {},poll_interval=.01)
        settings={'schema':1,'automatic_no_start':False,'destructive':False}
        for name in ('read_token','operate_token','ticket_key'):
            p=root/(name+'.secret');p.write_text(os.urandom(32).hex());settings[name+'_file']=str(p)
        runner.recovery_application=IntegratedApplication(runner,settings,'job-only-token-disposable-0123456789-0123456789')
        store.put_job(dict(request_id='fixture-active',request_hash='payload',state='running',target='ollama',resources=['gpu1']))
        lease=a.try_acquire('fixture-active','payload',('gpu1',),'ollama');job=store.job('fixture-active');job['lease_id']=lease.owner['lease_id'];store.put_job(job)
        # Populate after constructor startup: qualify the operation itself under
        # historical load without turning this into another startup investigation.
        with runner.job_mutex.held():
            for n in range(5000):
                store.put_job(dict(request_id=f'history-{n}',request_hash='synthetic',state='success',target='ollama',resources=['gpu1']))
            with a.mutex.held():
                state=a.snapshot();state['qualification_padding']='x'*1_450_000;store.write('ownership.json',state)
        def close():lease.close();runner.close();tmp.cleanup()
        return SimpleNamespace(root=root,settings=settings,runner=runner,a=a,lease=lease,store=store,close=close)

    def test_actual_active_admin_and_single_request(self):
        async def qualify():
            import aiohttp
            async with aiohttp.ClientSession() as session:
                ws=await session.ws_connect(self.ha_url+'/api/websocket');await ws.receive_json()
                await ws.send_json({'type':'auth','access_token':self.ha_token});self.assertEqual((await ws.receive_json())['type'],'auth_ok')
                await ws.send_json(dict(id=10,type='config/auth/create',name='Member fixture',group_ids=['system-users']))
                created=await ws.receive_json();self.assertTrue(created['success']);uid=created['result']['user']['id'];password=uuid.uuid4().hex
                await ws.send_json(dict(id=11,type='config/auth_provider/homeassistant/create',user_id=uid,username='estop-member',password=password));self.assertTrue((await ws.receive_json())['success'])
                async with session.post(self.ha_url+'/auth/login_flow',json={'client_id':self.ha_url+'/','handler':['homeassistant',None],'redirect_uri':self.ha_url+'/','type':'authorize'}) as response:flow=await response.json()
                async with session.post(self.ha_url+'/auth/login_flow/'+flow['flow_id'],json={'client_id':self.ha_url+'/','username':'estop-member','password':password}) as response:flow=await response.json()
                async with session.post(self.ha_url+'/auth/token',data={'grant_type':'authorization_code','code':flow['result'],'client_id':self.ha_url+'/'}) as response:token=await response.json()
                member=await session.ws_connect(self.ha_url+'/api/websocket');await member.receive_json();await member.send_json({'type':'auth','access_token':token['access_token']});await member.receive_json()
                await member.send_json(dict(id=1,type='aihub_recovery/control',action='estop'))
                denied=await member.receive_json();self.assertFalse(denied['success']);self.assertEqual(denied['error']['code'],'unauthorized');await member.close()
                self.assertEqual(self.e.a.snapshot()['admission_generation']['sequence'],0)
                await ws.send_json(dict(id=20,type='aihub_recovery/control',action='estop'))
                response=await ws.receive_json(timeout=30);self.assertTrue(response['success'],response)
                self.assertEqual(response['result']['outcome'],'success');self.assertEqual(response['result']['released_leases'],1)
                self.assertEqual(self.e.a.snapshot()['owners'],{})
                self.assertFalse(NativeLock(self.e.lease.locks[0].path,create=False).acquire(0))
                await ws.send_json(dict(id=21,type='aihub_recovery/control',action='estop',token='obsolete',confirmed=True))
                denied=await ws.receive_json();self.assertFalse(denied['success'])
                self.assertEqual(self.e.a.snapshot()['admission_generation']['sequence'],1)
                await ws.close()
        asyncio.run(qualify())

    def test_actual_browser_one_click_twice_and_unknown_no_retry(self):
        async def qualify():
            import time
            from playwright.async_api import async_playwright
            async with async_playwright() as playwright:
                browser=await playwright.chromium.launch(headless=True,args=['--disable-gpu']);context=await browser.new_context();page=await context.new_page();dialogs=[]
                async def unexpected(dialog):dialogs.append(dialog.message);await dialog.dismiss()
                page.on('dialog',unexpected);await page.goto(self.ha_url+'/local/recovery-qualification.html');await page.evaluate(MOUNT,{'token':self.ha_token,'lose':False})
                root=page.locator('aihub-recovery-card');self.assertEqual(await root.locator('[data-action="estop-check"]').count(),0);self.assertEqual(await root.locator('[data-action="estop-verify"]').count(),0)
                times=[];sizes=[]
                for count in (1,2):
                    begin=time.perf_counter();await root.locator('[data-action="estop"]').click()
                    await page.wait_for_function('calls.filter(x=>x.action==="estop").length === '+str(count)+' && card.estop.result?.outcome === "success" && !card.estop.busy')
                    times.append(time.perf_counter()-begin);sizes.append(self.e.store.path('ownership.json').stat().st_size)
                self.assertEqual(dialogs,[]);self.assertLessEqual(abs(sizes[1]-sizes[0]),20)
                self.assertEqual(self.e.a.snapshot()['admission_generation']['sequence'],2)
                self.assertFalse(NativeLock(self.e.lease.locks[0].path,create=False).acquire(0))
                fresh=self.e.a.try_acquire('fresh','fresh',('gpu1',),'ollama');self.assertIsNotNone(fresh);fresh.close()
                await page.reload();await page.evaluate(MOUNT,{'token':self.ha_token,'lose':True})
                await root.locator('[data-action="estop"]').click();await page.wait_for_function('card.estop.result?.outcome === "unknown" && !card.estop.busy')
                seq=self.e.a.snapshot()['admission_generation']['sequence'];self.assertEqual(seq,3)
                await page.evaluate('card.refresh()');await asyncio.sleep(.2)
                self.assertEqual(await page.evaluate('card.estop.result.outcome'),'unknown')
                self.assertEqual(await page.evaluate('calls.filter(x=>x.action==="estop").length'),1)
                await page.reload();await page.evaluate(MOUNT,{'token':self.ha_token,'lose':False})
                self.assertEqual(await page.evaluate('calls.filter(x=>x.action==="estop").length'),0)
                self.assertEqual(self.e.a.snapshot()['admission_generation']['sequence'],seq)
                report=dict(browser=browser.version,transport='actual HA auth/WS -> Media ASGI -> runner/native',history_jobs=5000,
                    one_click_seconds=times,authority_sizes=sizes,confirmations=len(dialogs),lost_response='UNKNOWN, no retry',
                    physical='unverified; original native FD still held',card_sha256=hashlib.sha256((ROOT/'home_assistant/www/aihub-recovery-card.js').read_bytes()).hexdigest())
                (RESULTS/'estop-browser-result.json').write_text(json.dumps(report,indent=2)+'\n');await context.close();await browser.close()
        asyncio.run(qualify())

if __name__=='__main__':
    suite=unittest.TestSuite([EstopHA('test_actual_active_admin_and_single_request'),EstopHA('test_actual_browser_one_click_twice_and_unknown_no_retry')])
    sys.exit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
