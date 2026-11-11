"""Actual Chromium card/IndexedDB -> HA Core WS/auth -> Media -> native authority.

Only GPU telemetry is synthetic. Response loss and stale status delivery are
injected at the browser transport boundary, never by fabricating a release.
"""
import asyncio
import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))
from idle_ha_workflow import IdleHA
from integration_support import RESULTS,ROOT

MOUNT = r"""async ({token, stale, lose}) => {
  await import('/local/aihub-recovery-card.js');
  window.requestLog=[];
  const callWS=message=>new Promise((resolve,reject)=>{
    const socket=new WebSocket(location.origin.replace('http','ws')+'/api/websocket');
    socket.onerror=()=>reject(new Error('Fixture transport unavailable'));
    socket.onmessage=event=>{
      const value=JSON.parse(event.data);
      if(value.type==='auth_required') socket.send(JSON.stringify({type:'auth',access_token:token}));
      else if(value.type==='auth_ok') {
        window.requestLog.push({action:message.action,operation:message.operation});
        socket.send(JSON.stringify({id:1,...message}));
      } else if(value.type==='result') {
        socket.close();
        if(!value.success) {reject(new Error(value.error?.message||'HA refusal'));return;}
        // Fault injection: server completed, but card receives no POST result.
        if(lose && ['recover','emergency','abort'].includes(message.action)) {
          reject(new Error('Injected response loss; Verify only.'));return;
        }
        if(stale && message.action==='status')
          value.result.attempt={id:'fixture-prior-success',phase:'succeeded',checks:[]};
        resolve(value.result);
      }
    };
  });
  document.body.replaceChildren();
  window.card=document.createElement('aihub-recovery-card');
  card.hass={callWS,user:{is_admin:true}};document.body.append(card);
  await card.initialRefresh;
}"""

class BrowserHA(IdleHA):
    def test_card_confirm_lost_response_reload_multitab_exact_verify(self):
        async def qualify():
            from playwright.async_api import async_playwright
            async with async_playwright() as playwright:
                browser=await playwright.chromium.launch(headless=True,args=['--disable-gpu'])
                context=await browser.new_context()
                page=await context.new_page()
                dialogs=[]
                async def confirm(dialog):
                    dialogs.append(dialog.message)
                    self.assertIn(self.f.job['request_id'],dialog.message)
                    self.assertIn(self.f.job['lease_id'],dialog.message)
                    self.assertIn(self.f.job['request_hash'],dialog.message)
                    self.assertIn('release only its GPU1 lease',dialog.message)
                    await dialog.accept()
                page.on('dialog',confirm)
                await page.goto(self.ha_url+'/local/recovery-qualification.html')
                await page.evaluate(MOUNT,{'token':self.ha_token,'stale':True,'lose':True})
                await page.locator('aihub-recovery-card').locator('[data-action="check"]').click()
                await page.wait_for_function('card.flow.checked?.eligible === true')
                await page.locator('aihub-recovery-card').locator('[data-action="recover"]').click()
                await page.wait_for_function('card.flow.pending && !card.flow.busy')
                pending=await page.evaluate('structuredClone(card.flow.pending)')
                self.assertEqual(len(dialogs),1)
                self.assertEqual(pending['job_id'],self.f.job['request_id'])
                self.assertNotIn('token',json.dumps(pending))
                self.assertEqual(await page.evaluate('requestLog.filter(x=>x.action==="recover").length'),1)
                await page.evaluate('card.refresh()')
                self.assertEqual(await page.evaluate('card.flow.operation'),pending['operation'])
                self.assertTrue(await page.locator('aihub-recovery-card').locator('[data-action="recover"]').is_disabled())
                # Reload and another tab use the browser's real IndexedDB.
                await page.reload()
                await page.evaluate(MOUNT,{'token':self.ha_token,'stale':True,'lose':False})
                self.assertEqual(await page.evaluate('card.flow.operation'),pending['operation'])
                other=await context.new_page()
                await other.goto(self.ha_url+'/local/recovery-qualification.html')
                await other.evaluate(MOUNT,{'token':self.ha_token,'stale':True,'lose':False})
                self.assertTrue(await other.locator('aihub-recovery-card').locator('[data-action="recover"]').is_disabled())
                await other.evaluate('card.act("recover")')
                self.assertEqual(await other.evaluate('requestLog.filter(x=>x.action==="recover").length'),0)
                await page.locator('aihub-recovery-card').locator('[data-action="verify"]').click()
                await page.wait_for_function('card.flow.pending === null && !card.flow.busy')
                verifies=await page.evaluate('requestLog.filter(x=>x.action==="verify")')
                self.assertEqual(verifies,[{'action':'verify','operation':pending['operation']}])
                self.f.calls.assert_called_once()
                self.assertEqual(self.f.a.snapshot()['owners'],{})
                self.assertEqual(len(self.f.a.snapshot()['recoveries']),len(self.f.before['recoveries'])+1)
                report={'scenario':'real-browser-confirm-lost-response-reload-multitab-verify',
                    'browser':browser.version,'ha':'2026.10.4','native_calls':1,'audits_added':1,
                    'gpu_observations':'synthetic','transport':'real HA WS -> real Media ASGI -> runner HTTP',
                    'fault_injection':['POST result withheld from card','older successful status attempt'],
                    'indexeddb':'actual Chromium','tokens_persisted':False,
                    'card_sha256':hashlib.sha256((ROOT/'home_assistant/www/aihub-recovery-card.js').read_bytes()).hexdigest()}
                (RESULTS/'browser-ha-result.json').write_text(json.dumps(report,indent=2)+'\n')
                await context.close();await browser.close()
        asyncio.run(qualify())

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([
        BrowserHA('test_card_confirm_lost_response_reload_multitab_exact_verify')]))
    sys.exit(not result.wasSuccessful())
