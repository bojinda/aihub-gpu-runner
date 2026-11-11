"""Real disposable HA Core auth/WS -> deployed Media proxy -> candidate authority.
Only GPU telemetry/process lifetimes are synthetic; journal and locks are real,
temporary local files. No production endpoints, GPU devices or model backends.
"""
import asyncio,errno,json,os,sys,time,unittest,urllib.request
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
if os.environ.get('AIHUB_REQUIRE_INSTALLED_RUNNER')=='1':sys.path.append(str(Path(__file__).resolve().parents[1]))
else:sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_ha_core_integration import HACoreIntegration
from integration_support import RESULTS
from tests.test_idle_recovery import IdleFixture
from aihub_gpu_runner.integrated_runtime import IntegratedApplication
from aihub_gpu_runner.recovery import ProcessDisappeared

class IdleHA(HACoreIntegration):
 def make_environment(self):
  self.f=IdleFixture(self)
  # Unit fixture forbids transport. Here only the local HA/Media HTTP transport
  # is real, so remove those sentinels and reinstate synthetic process reads.
  patch.stopall()
  def birth(pid):
   if pid=='100':return '10'
   raise ProcessDisappeared(pid,errno.ENOENT)
  b=patch('aihub_gpu_runner.idle_recovery.process_birth',side_effect=birth);b.start();self.addCleanup(b.stop)
  root=self.f.store.root.parent;settings={'automatic_no_start':True,'destructive':False,'idle_only_manual':True}
  for key in ('read_token','operate_token','ticket_key'):
   p=root/(key+'.secret');p.write_text(os.urandom(32).hex());settings[key+'_file']=str(p)
  runner=self.f.runner;runner.recovery=SimpleNamespace(observer=self.f.observer);runner.handoffs=None
  app=IntegratedApplication(runner,settings,'job-only-token-0123456789');runner.recovery_application=app
  self.f.control=app.idle
  return SimpleNamespace(root=root,settings=settings,runner=runner,close=lambda:None)

 def test_real_ha_confirm_stale_unknown_ack_and_verified_release(self):
  async def qualify():
   import aiohttp
   async with aiohttp.ClientSession() as session:
    ws=await session.ws_connect(self.ha_url+'/api/websocket')
    self.assertEqual((await ws.receive_json())['type'],'auth_required')
    await ws.send_json({'type':'auth','access_token':self.ha_token});self.assertEqual((await ws.receive_json())['type'],'auth_ok')
    serial=0;transcript=[]
    async def action(action,**fields):
     nonlocal serial
     serial+=1;await ws.send_json(dict(id=serial,type='aihub_recovery/control',action=action,**fields))
     result=await ws.receive_json(timeout=95)
     transcript.append({'action':action,'success':result.get('success'),'result':{k:v for k,v in result.get('result',result.get('error',{})).items() if k!='token'}})
     return result.get('result',result.get('error',{}))
    # Real HA -> Media -> runner must expose the actual configuration blocker,
    # even when no candidate is available, without claiming operational status.
    self.f.a.control_required=False
    status=await action('status');self.assertFalse(status['recovery_operational'])
    self.assertEqual(status['capabilities'],[])
    denied=await action('check');self.assertEqual(denied['failed_checks'],['mandatory_admission_gate_not_configured'])
    self.f.calls.assert_not_called()
    self.f.a.control_required=True
    # Reproduce the actual omitted coordination prerequisite through real HA.
    lock=self.f.store.path('recovery-control.lock');before=lock.stat();lock.unlink()
    status=await action('status');self.assertFalse(status['recovery_operational'])
    self.assertEqual(status['incidents'][0]['classification'],'configuration_error')
    denied=await action('check');self.assertEqual(denied['failed_checks'],['idle_coordination_lock_missing_recovery_control'])
    self.assertFalse(lock.exists());self.f.calls.assert_not_called()
    # Only this disposable fixture performs the installation-only lock repair.
    from aihub_gpu_runner.core import NativeLock
    with NativeLock(lock).held():pass
    status=await action('status');self.assertTrue(status['recovery_operational']);self.assertEqual(status['incidents'][0]['classification'],'manual_idle_check_required')
    active=Future();self.f.runner.futures['active']=active
    denied=await action('check');self.assertEqual(denied['failed_checks'],['runner_supervision_or_queue_active']);active.set_result(None)
    checked=await action('check');self.assertTrue(checked['eligible'],checked)
    denied=await action('recover',token=checked['token'],confirmed=False);self.assertEqual(denied['code'],'invalid_request')
    self.f.calls.assert_not_called()
    original=self.f.store.job(self.f.job['request_id']);changed=dict(original,updated_at='stale-ui-test');self.f.store.put_job(changed)
    stale=await action('recover',token=checked['token'],confirmed=True);self.assertEqual(stale['failed_checks'],['approved_state_changed'])
    self.f.store.put_job(original);self.f.calls.assert_not_called()
    # Existing display acknowledgment is a separate authenticated Media route.
    owners=self.f.a.snapshot()['owners'];before=len(self.f.a.snapshot()['recoveries'])
    req=urllib.request.Request(self.bridge.url.replace('/gpu-recovery','/recovery-counter/acknowledge'),data=b'{}',
        headers={'Authorization':'Bearer '+self.media_env['RECOVERY_ACK_TOKEN']})
    try:
     with self.bridge.opener.open(req,timeout=10) as response:self.assertEqual(response.status,200)
    except urllib.error.HTTPError as exc:
     self.fail('acknowledgment failed: '+exc.read().decode())
    self.assertEqual(self.f.a.snapshot()['owners'],owners);self.assertEqual(len(self.f.a.snapshot()['recoveries']),before)
    # Lose the authority response after durable release, while retaining its
    # native receipt. HA sees operator_required; Verify must never release again.
    checked=await action('check');self.assertTrue(checked['eligible'])
    authority=self.f.a.recover
    def lost(*args,**kwargs):authority(*args,**kwargs);raise OSError('synthetic response lost after authority commit')
    self.f.a.recover=Mock(side_effect=lost)
    uncertain=await action('recover',token=checked['token'],confirmed=True);self.assertEqual(uncertain['result'],'operator_required')
    status=await action('status');self.assertEqual(status['attempt']['id'],checked['operation']);self.assertEqual(status['maintenance'],'closed')
    denied=await action('check');self.assertFalse(denied['eligible'])
    verified=await action('verify',operation=checked['operation']);self.assertTrue(verified['verified_release'])
    self.f.a.recover.assert_called_once()
    duplicate=await action('recover',token=checked['token'],confirmed=True);self.assertEqual(duplicate['failed_checks'],['duplicate_approval_consumed'])
    status=await action('status');self.assertEqual(status['owners'],{});self.assertEqual(status['attempt']['phase'],'succeeded');self.assertTrue(status['admission_ready'])
    self.assertEqual(self.f.store.job(self.f.job['request_id'])['error_code'],'operator_recovered_no_automatic_resubmission')
    self.assertEqual(len(self.f.a.snapshot()['recoveries']),before+1)
    emergency=await action('check-emergency');self.assertFalse(emergency['eligible'])
    self.assertEqual(emergency['failed_checks'],['no_retained_wallpaper_job'])
    await ws.close()
    (RESULTS/'ha-workflow-transcript.json').write_text(json.dumps(transcript,indent=2))
  asyncio.run(qualify())

 def test_real_ha_role_denial_and_lost_transport_response(self):
  async def qualify():
   import aiohttp,uuid
   async with aiohttp.ClientSession() as session:
    async def connect(token):
     ws=await session.ws_connect(self.ha_url+'/api/websocket')
     self.assertEqual((await ws.receive_json())['type'],'auth_required')
     await ws.send_json({'type':'auth','access_token':token})
     self.assertEqual((await ws.receive_json())['type'],'auth_ok')
     return ws
    ws=await connect(self.ha_token)
    counter=0
    async def command(kind,**fields):
     nonlocal counter
     counter+=1;await ws.send_json(dict(id=counter,type=kind,**fields))
     return await ws.receive_json(timeout=95)
    created=await command('config/auth/create',name='Non-admin fixture',group_ids=['system-users'])
    self.assertTrue(created['success'],created)
    uid=created['result']['user']['id'];password=uuid.uuid4().hex
    linked=await command('config/auth_provider/homeassistant/create',user_id=uid,username='fixture-member',password=password)
    self.assertTrue(linked['success'],linked)
    async with session.post(self.ha_url+'/auth/login_flow',json={
       'client_id':self.ha_url+'/', 'handler':['homeassistant',None],
       'redirect_uri':self.ha_url+'/', 'type':'authorize'}) as response:flow=await response.json()
    async with session.post(self.ha_url+'/auth/login_flow/'+flow['flow_id'],json={
       'client_id':self.ha_url+'/', 'username':'fixture-member','password':password}) as response:flow=await response.json()
    async with session.post(self.ha_url+'/auth/token',data={
       'grant_type':'authorization_code','code':flow['result'],'client_id':self.ha_url+'/'}) as response:token=await response.json()
    member=await connect(token['access_token'])
    for serial,action in enumerate(('status','check','recover','verify'),1):
     fields={'token':'not-a-ticket','confirmed':True} if action=='recover' else {'operation':'not-an-operation'} if action=='verify' else {}
     await member.send_json(dict(id=serial,type='aihub_recovery/control',action=action,**fields))
     refused=await member.receive_json(timeout=10)
     self.assertFalse(refused['success'],refused);self.assertEqual(refused['error']['code'],'unauthorized')
    await member.close();self.f.calls.assert_not_called()
    checked=await command('aihub_recovery/control',action='check')
    self.assertTrue(checked['success'],checked);checked=checked['result'];self.assertTrue(checked['eligible'],checked)
    operation=checked['operation']
    counter+=1;await ws.send_json(dict(id=counter,type='aihub_recovery/control',action='recover',token=checked['token'],confirmed=True))
    await ws.close()  # Deliberately lose only the local transport response.
    deadline=time.monotonic()+15
    while self.f.a.snapshot()['owners'] and time.monotonic()<deadline:await asyncio.sleep(.05)
    from aihub_gpu_runner.maintenance import read_gate
    self.assertEqual(self.f.a.snapshot()['owners'],{},read_gate(self.f.store,True)['attempts'].get(operation))
    ws=await connect(self.ha_token);counter=0
    verified=await command('aihub_recovery/control',action='verify',operation=operation)
    self.assertTrue(verified['success'],verified);self.assertTrue(verified['result']['verified_release'],verified)
    self.assertEqual(verified['result']['operation'],operation)
    self.f.calls.assert_called_once();self.assertEqual(len(self.f.a.snapshot()['recoveries']),len(self.f.before['recoveries'])+1)
    await ws.close()
  asyncio.run(qualify())

if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([
  IdleHA('test_real_ha_confirm_stale_unknown_ack_and_verified_release'),
  IdleHA('test_real_ha_role_denial_and_lost_transport_response')]))
 sys.exit(not result.wasSuccessful())
