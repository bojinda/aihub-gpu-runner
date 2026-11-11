import asyncio
import copy
import importlib.util
import socket
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from aihub_gpu_runner.core import Runner, NativeLock, lock_identity
from aihub_gpu_runner.ingress import server
from aihub_gpu_runner.maintenance import FILE, IngressGate
from aihub_gpu_runner.recovery_observer import IsolationVerifier, COVERAGE, ReadOnlyObserver, UnavailableObserver
from tests.recovery_fixture import Fixture

BRIDGE = Path(__file__).parents[1]/'home_assistant/custom_components/aihub_recovery/bridge.py'
spec=importlib.util.spec_from_file_location('ha_bridge_fixture',BRIDGE)
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)


class BoundaryTests(unittest.TestCase):
    def setUp(self):self.f=Fixture(self)

    def test_real_runner_restart_preserves_closed_attempt_and_original_job(self):
        self.f.observe_hook=lambda fenced: (_ for _ in ()).throw(TimeoutError()) if fenced else None
        self.f.run();self.f.supervisor.close()
        before=self.f.store.job('fixture-wallpaper')
        manager=SimpleNamespace(start=Mock(),close=Mock(),startup=Mock())
        runner=Runner(self.f.a,{}, {},recovery=manager,poll_interval=.01)
        try:
            self.assertEqual(self.f.store.job('fixture-wallpaper'),before)
            manager.startup.assert_not_called()
            self.assertTrue(self.f.a.maintenance_blocked())
        finally:runner.close()

    def test_real_automatic_recovery_tick_is_suppressed(self):
        from aihub_gpu_runner.recovery import RecoveryManager
        self.f.observe_hook=lambda fenced: (_ for _ in ()).throw(TimeoutError()) if fenced else None
        self.f.run()
        manager=SimpleNamespace(a=self.f.a,last_tick=0,mutex=NativeLock(self.f.store.path('recovery.lock')),
                                lifecycle=SimpleNamespace(finalize_released=Mock()))
        RecoveryManager.tick(manager,force=True)
        manager.lifecycle.finalize_released.assert_not_called()

    def test_positive_isolation_evidence_and_exact_route_inventory_required(self):
        manifest=dict(paths={k:{'path':k,'uid':123} for k in COVERAGE},kernel_sha256='kernel',
                      access_sha256='acl',observer_routes={'music':'bound-direct-IP'},gateway_generation='service-generation')
        raw=dict(at=self.f.now,kernel_sha256='kernel',access_sha256='acl',observer_routes=manifest['observer_routes'],
                 gateway_generation='service-generation',unmanaged_clients=[],observer_readiness=True,
                 gate_closed=True,gateway_denial_verified=True,
                 paths={k:dict(identity=v,enforced=True,positive_deny_evidence=True) for k,v in manifest['paths'].items()})
        collector=SimpleNamespace(collect=lambda **kw:copy.deepcopy(raw))
        verifier=IsolationVerifier(collector,manifest,clock=lambda:self.f.now)
        self.assertTrue(verifier.verify(fenced=True)['verified'])
        for key in ('kernel_sha256','access_sha256','gateway_generation','observer_routes'):
            old=raw[key];raw[key]='drift'
            with self.assertRaises(ValueError):verifier.verify(fenced=True)
            raw[key]=old
        raw['paths']['manual_comfyui']['positive_deny_evidence']=False
        with self.assertRaisesRegex(ValueError,'manual_comfyui'):verifier.verify(fenced=True)
        raw['paths']['manual_comfyui']['positive_deny_evidence']=True
        raw['gateway_denial_verified']=False
        with self.assertRaises(ValueError):verifier.verify(fenced=True)

    def test_readonly_observer_uses_stopped_ready_and_direct_route_proof(self):
        existing=SimpleNamespace(p={'observation_timeout':30},stopped_ready=Mock(return_value=self.f.observation['proof']))
        identities=SimpleNamespace(capture=Mock(return_value={'containers':self.f.observation['containers'],'manual_activity':False}))
        exclusion=SimpleNamespace(verify=Mock(return_value=self.f.observation['isolation']))
        observer=ReadOnlyObserver(existing,identities,exclusion,source_digest=lambda:'source',
            configuration_digest=lambda:'configuration',boot_reader=lambda:self.f.boot,clock=lambda:self.f.now)
        value=observer.observe(fenced=True)
        self.assertEqual(value['proof'],self.f.observation['proof'])
        self.assertEqual(existing.stopped_ready.call_count,1)
        self.assertEqual(existing.stopped_ready.call_args.kwargs,{'processes':{}})
        exclusion.verify.assert_called_with(fenced=True)

    def test_default_observer_fails_closed(self):
        self.f.control.observer=UnavailableObserver()
        self.assertFalse(self.f.control.check('operator1')['eligible'])

    def test_loopback_tcp_gateway_forwards_only_while_gate_is_open(self):
        upstream=socket.socket();upstream.bind(('127.0.0.1',0));upstream.listen();upstream.settimeout(2)
        self.addCleanup(upstream.close)
        accepted=[]
        def backend():
            sock,_=upstream.accept();accepted.append(True)
            with sock:
                data=sock.recv(1024)
                sock.sendall(b'fixture:'+data)
                sock.shutdown(socket.SHUT_WR)
                while sock.recv(1024):pass
        thread=threading.Thread(target=backend);thread.start()
        gateway=server(('127.0.0.1',0),upstream.getsockname(),IngressGate(self.f.a,'manual-comfyui'))
        worker=threading.Thread(target=gateway.serve_forever);worker.start()
        try:
            with socket.create_connection(gateway.server_address,timeout=2) as client:
                client.sendall(b'read-only-fixture');client.shutdown(socket.SHUT_WR)
                self.assertEqual(client.recv(1024),b'fixture:read-only-fixture')
            thread.join(2)
            deadline=time.monotonic()+2
            while self.f.gate()['permits'] and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(self.f.gate()['permits'],{})
            self.f.observe_hook=lambda fenced: (_ for _ in ()).throw(TimeoutError()) if fenced else None
            self.f.run()
            with socket.create_connection(gateway.server_address,timeout=2) as client:
                self.assertIn(b'503 Service Unavailable',client.recv(1024))
            self.assertEqual(len(accepted),1)
        finally:
            gateway.shutdown();gateway.server_close();worker.join(2);thread.join(2)

    def test_stale_gateway_permit_survives_restart_and_blocks(self):
        gate=self.f.gate();gate['permits']['abandoned']={'client':'manual-comfyui','boot':self.f.boot};
        self.f.store.write(FILE,gate)
        self.assertIn('client_connections_not_drained',self.f.control.check('operator1')['failed_checks'])
        self.f.now+=999999
        self.assertIn('client_connections_not_drained',self.f.control.check('operator1')['failed_checks'])

    def test_new_submission_during_observation_invalidates_check(self):
        def hook(fenced):
            if not fenced:
                with IngressGate(self.f.a,'manual-comfyui').connection():pass
        self.f.observe_hook=hook
        self.assertIn('state_changed_during_observation',self.f.control.check('operator1')['failed_checks'])

    def test_failure_reopening_gate_can_be_verified_without_releasing_again(self):
        original=self.f.store.write
        def write(name,value):
            if name==FILE and value['mode']=='open' and value['attempts']:raise OSError('durability failure')
            original(name,value)
        with patch.object(self.f.store,'write',side_effect=write):result=self.f.run()
        self.assertEqual(result['result'],'operator_required')
        self.assertTrue(self.f.a.maintenance_blocked())
        self.assertEqual(self.f.control.reconcile(result['operation'])['result'],'succeeded')
        self.assertEqual(self.f.calls.call_count,1)


class HATests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.transport=SimpleNamespace(request=Mock(return_value={'available':True}))
        async def execute(fn,*args):return fn(*args)
        self.hass=SimpleNamespace(data={'aihub_recovery':self.transport},async_add_executor_job=execute)
        self.connection=SimpleNamespace(user=SimpleNamespace(id='actual-user',is_admin=True,is_active=True,system_generated=False),
                                       send_error=Mock(),send_result=Mock())
    async def test_actor_comes_from_authenticated_connection(self):
        await bridge.dispatch(self.hass,self.connection,{'id':1,'type':'aihub_recovery/control','action':'check'})
        self.transport.request.assert_called_once_with('check','actual-user',{})
    async def test_nonadmin_inactive_system_and_missing_user_denied(self):
        for values in ({'is_admin':False},{'is_active':False},{'system_generated':True}):
            old=copy.deepcopy(self.connection.user)
            for k,v in values.items():setattr(self.connection.user,k,v)
            await bridge.dispatch(self.hass,self.connection,{'id':1,'type':'aihub_recovery/control','action':'check'})
            self.connection.user=old
        self.connection.user=None
        await bridge.dispatch(self.hass,self.connection,{'id':1,'type':'aihub_recovery/control','action':'check'})
        self.transport.request.assert_not_called()
        self.assertEqual(self.connection.send_error.call_count,4)
    async def test_actor_override_arbitrary_command_and_missing_confirmation_denied(self):
        for fields in ({'action':'check','actor':'someone-else'},{'action':'shell'},
                       {'action':'recover','token':'bound','confirmed':'true'},
                       {'action':'recover','token':'bound'}):
            await bridge.dispatch(self.hass,self.connection,dict(id=1,type='aihub_recovery/control',**fields))
        self.transport.request.assert_not_called()
    async def test_exact_confirmed_token_forwarded_once(self):
        await bridge.dispatch(self.hass,self.connection,dict(id=1,type='aihub_recovery/control',
                              action='recover',token='exact-frozen-ticket',confirmed=True))
        self.transport.request.assert_called_once_with('recover','actual-user',{'token':'exact-frozen-ticket','confirmed':True})
    async def test_timeout_has_no_retry_or_secret_in_output(self):
        self.transport.request.side_effect=TimeoutError('PRIVATE credential')
        await bridge.dispatch(self.hass,self.connection,dict(id=1,type='aihub_recovery/control',
                              action='recover',token='exact-frozen-ticket',confirmed=True))
        self.transport.request.assert_called_once()
        self.assertNotIn('PRIVATE',str(self.connection.send_error.call_args))
    async def test_emergency_requires_explicit_work_loss_and_exact_token(self):
        msg=dict(id=1,type='aihub_recovery/control',action='emergency',token='exact',confirmed=True)
        await bridge.dispatch(self.hass,self.connection,msg)
        self.transport.request.assert_not_called()
        await bridge.dispatch(self.hass,self.connection,dict(msg,accept_work_loss=True))
        self.transport.request.assert_called_once_with('emergency','actual-user',
            {'token':'exact','confirmed':True,'accept_work_loss':True})
    async def test_automatic_authority_is_never_exposed_to_ha(self):
        await bridge.dispatch(self.hass,self.connection,dict(id=1,type='aihub_recovery/control',action='automatic'))
        self.transport.request.assert_not_called()
    def test_http_bad_origin_shared_credentials_and_redirects_refused(self):
        settings=dict(url='http://unsafe',read_token='r'*43,operate_token='o'*43)
        with self.assertRaises(ValueError):bridge.Bridge(settings)
        for origin in ('https://user:pass@example.org','https://example.org/?token=bad','https://example.org/action'):
            with self.assertRaises(ValueError):bridge.Bridge(dict(settings,url=origin))
        with self.assertRaises(ValueError):bridge.Bridge(dict(settings,url='https://example.org',operate_token='r'*43))
        with self.assertRaises(ValueError):bridge.NoRedirect().redirect_request(None)


if __name__=='__main__':unittest.main()
