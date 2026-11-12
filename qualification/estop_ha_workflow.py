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
    else resolve(v.result);}
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
        def close():lease.close();runner.close();tmp.cleanup()
        return SimpleNamespace(root=root,settings=settings,runner=runner,a=a,lease=lease,store=store,close=close)

    def test_actual_admin_denial_and_lost_ws_response_verify(self):
        async def qualify():
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async def connect(token):
                    ws=await session.ws_connect(self.ha_url+'/api/websocket');await ws.receive_json()
                    await ws.send_json({'type':'auth','access_token':token});self.assertEqual((await ws.receive_json())['type'],'auth_ok');return ws
                ws=await connect(self.ha_token);serial=0
                async def command(action,**fields):
                    nonlocal serial
                    serial+=1;await ws.send_json(dict(id=serial,type='aihub_recovery/control',action=action,**fields));return await ws.receive_json(timeout=30)
                async def raw(kind,**fields):
                    nonlocal serial
                    serial+=1;await ws.send_json(dict(id=serial,type=kind,**fields));return await ws.receive_json(timeout=30)
                created=await raw('config/auth/create',name='Member fixture',group_ids=['system-users']);self.assertTrue(created['success'])
                uid=created['result']['user']['id'];password=uuid.uuid4().hex
                linked=await raw('config/auth_provider/homeassistant/create',user_id=uid,username='estop-member',password=password);self.assertTrue(linked['success'])
                async with session.post(self.ha_url+'/auth/login_flow',json={'client_id':self.ha_url+'/','handler':['homeassistant',None],'redirect_uri':self.ha_url+'/','type':'authorize'}) as response:flow=await response.json()
                async with session.post(self.ha_url+'/auth/login_flow/'+flow['flow_id'],json={'client_id':self.ha_url+'/','username':'estop-member','password':password}) as response:flow=await response.json()
                async with session.post(self.ha_url+'/auth/token',data={'grant_type':'authorization_code','code':flow['result'],'client_id':self.ha_url+'/'}) as response:token=await response.json()
                member=await connect(token['access_token'])
                for i,action in enumerate(('estop-check','estop','estop-verify'),1):
                    fields={'token':'not-valid','confirmed':True} if action=='estop' else {'operation':'0'*32} if action=='estop-verify' else {}
                    await member.send_json(dict(id=i,type='aihub_recovery/control',action=action,**fields));r=await member.receive_json();self.assertFalse(r['success']);self.assertEqual(r['error']['code'],'unauthorized')
                await member.close()
                checked=(await command('estop-check'))['result'];self.assertTrue(checked['eligible'])
                before=self.e.a.snapshot()['admission_generation']['id']
                serial+=1;await ws.send_json(dict(id=serial,type='aihub_recovery/control',action='estop',token=checked['token'],confirmed=True));await ws.close()
                end=asyncio.get_running_loop().time()+10
                while checked['operation'] not in self.e.a.snapshot()['estop_attempts'] and asyncio.get_running_loop().time()<end:await asyncio.sleep(.02)
                ws=await connect(self.ha_token);serial=0
                verified=(await command('estop-verify',operation=checked['operation']))['result']
                self.assertTrue(verified['verified']);self.assertEqual(verified['revoked_leases'],1)
                self.assertNotEqual(self.e.a.snapshot()['admission_generation']['id'],before)
                self.assertEqual(len(self.e.a.snapshot()['estop_attempts']),1)
                self.assertFalse(NativeLock(self.e.lease.locks[0].path,create=False).acquire(0))
                await ws.close()
        asyncio.run(qualify())

    def test_actual_browser_confirmation_reload_verify_single_audit(self):
        async def qualify():
            from playwright.async_api import async_playwright
            async with async_playwright() as playwright:
                browser=await playwright.chromium.launch(headless=True,args=['--disable-gpu']);context=await browser.new_context();page=await context.new_page();dialogs=[]
                async def confirm(dialog):
                    dialogs.append(dialog.message);self.assertIn('GPU0 and GPU1',dialog.message);self.assertIn('fixture-active',dialog.message);self.assertIn('NOT stopped',dialog.message);await dialog.accept()
                page.on('dialog',confirm);await page.goto(self.ha_url+'/local/recovery-qualification.html');await page.evaluate(MOUNT,{'token':self.ha_token,'lose':True})
                await page.locator('aihub-recovery-card').locator('[data-action="estop-check"]').click();await page.wait_for_function('card.estop.checked?.eligible === true')
                await page.locator('aihub-recovery-card').locator('[data-action="estop"]').click();await page.wait_for_function('card.estop.pending && !card.estop.busy')
                pending=await page.evaluate('structuredClone(card.estop.pending)');self.assertNotIn('token',pending);self.assertEqual(len(dialogs),1)
                await page.reload();await page.evaluate(MOUNT,{'token':self.ha_token,'lose':False});self.assertEqual(await page.evaluate('card.estop.pending.operation'),pending['operation'])
                other=await context.new_page();await other.goto(self.ha_url+'/local/recovery-qualification.html');await other.evaluate(MOUNT,{'token':self.ha_token,'lose':False});self.assertTrue(await other.locator('aihub-recovery-card').locator('[data-action="estop"]').is_disabled())
                await other.evaluate('card.act("estop")');self.assertEqual(await other.evaluate('calls.filter(x=>x.action==="estop").length'),0)
                await page.locator('aihub-recovery-card').locator('[data-action="estop-verify"]').click();await page.wait_for_function('card.estop.pending === null')
                self.assertEqual(await page.evaluate('calls.filter(x=>x.action==="estop-verify")[0].operation'),pending['operation'])
                self.assertEqual(len(self.e.a.snapshot()['estop_attempts']),1);self.assertEqual(self.e.store.job('fixture-active')['state'],'failed')
                self.assertFalse(NativeLock(self.e.lease.locks[0].path,create=False).acquire(0))
                report=dict(browser=browser.version,transport='actual HA auth/WS -> Media ASGI -> runner/native',audit_count=1,confirmations=len(dialogs),
                    physical='not asserted; original native FD still held',tokens_persisted=False,card_sha256=hashlib.sha256((ROOT/'home_assistant/www/aihub-recovery-card.js').read_bytes()).hexdigest())
                (RESULTS/'estop-browser-result.json').write_text(json.dumps(report,indent=2)+'\n');await context.close();await browser.close()
        asyncio.run(qualify())

if __name__=='__main__':
    suite=unittest.TestSuite([EstopHA('test_actual_admin_denial_and_lost_ws_response_verify'),EstopHA('test_actual_browser_confirmation_reload_verify_single_audit')])
    sys.exit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
