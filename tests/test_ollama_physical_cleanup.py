"""Deterministic offline unload handoff and fail-closed regression coverage."""
import contextlib
import copy
import hashlib
import json
import threading
import types
import unittest
from unittest.mock import Mock, patch

from aihub_gpu_runner.backends import Target, OllamaBackend, CleanupPending, Uncertain
from aihub_gpu_runner.core import Runner, RecoveryRequired, encode
from aihub_gpu_runner.host_stage import HostStage
from aihub_gpu_runner.ollama_cleanup import OllamaCleanupGuard, cleanup_guard
from aihub_gpu_runner.recovery import RecoveryObserver, digest
from tests.test_container_contract import metadata

GPU0 = 'GPU-11111111-1111-4111-8111-111111111111'
GPU1 = 'GPU-22222222-2222-4222-8222-222222222222'
BOOT = '33333333-3333-4333-8333-333333333333'


class Clock:
    def __init__(self): self.t = .036442
    def monotonic(self): return self.t
    def sleep(self, duration): self.t += duration


class Mutex:
    def held(self): return contextlib.nullcontext()


class Store:
    def __init__(self): self.values = {}
    def job_name(self, name): return 'jobs/' + name + '.json'
    def read(self, name, default=None): return copy.deepcopy(self.values.get(name, default))
    def write(self, name, value): self.values[name] = copy.deepcopy(value)
    def job(self, name): return self.read(self.job_name(name))
    def put_job(self, value): self.write(self.job_name(value['request_id']), value)


class PhysicalCleanupTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        self.clock = Clock()
        patch('time.monotonic', self.clock.monotonic).start()
        patch('time.sleep', self.clock.sleep).start()
        patch('subprocess.run', side_effect=AssertionError('real process forbidden')).start()
        patch('subprocess.check_output', side_effect=AssertionError('real GPU forbidden')).start()
        patch('urllib.request.urlopen', side_effect=AssertionError('real HTTP forbidden')).start()
        self.target = Target('ollama', 'ollama', ('gpu1',), 'http://offline.invalid',
                             models=('synthetic',), cleanup_timeout=2)
        self.owner = dict(job_id='owned-job', request_hash='hash', lease_id='owned-lease',
                          target='ollama', boot_id=BOOT, phase='cleanup_pending')
        self.store = Store()
        self.job = dict(request_id='owned-job', request_hash='hash', target='ollama',
                        resources=['gpu1'], state='cleanup_pending', completion_verified=True,
                        lease_id='owned-lease', boot_id=BOOT)
        self.store.put_job(self.job)
        self.metas = {s: metadata(s, c) for s, c in [('wallpaper', 'a'), ('music', 'b'), ('ollama', 'c')]}
        for s, pid in [('music', 202), ('ollama', 303)]:
            self.metas[s]['State'].update(Running=True, Pid=pid)
        self.p = dict(scope='offline-scope', gpu_uuids={'gpu0': GPU0, 'gpu1': GPU1},
            docker='/offline/docker', nvidia_smi='/offline/nvidia-smi',
            samples=3, idle_utilization_pct=5, max_temperature_c=85,
            idle_memory_mb={'gpu0': 1200, 'gpu1': 256}, stopped_idle_memory_mb={'gpu0': 512, 'gpu1': 64},
            services={s:dict(name=m['Name'][1:], id=m['Id'], image=m['Image'],
                contract_sha256=RecoveryObserver.contract(m), identity_mode='exact' if s=='wallpaper' else 'contract',
                idle_memory_mb={'gpu0':450 if s=='music' else 64,'gpu1':64}) for s,m in self.metas.items()})
        self.state = dict(scope='offline-scope', ready=True, verified_boot=BOOT,
                          owners={'gpu1':copy.deepcopy(self.owner)},
                          recovery_policy={'policy':copy.deepcopy(self.p),'policy_id':digest(self.p)})
        self.a = types.SimpleNamespace(scope='offline-scope', store=self.store,
            snapshot=lambda:copy.deepcopy(self.state), boot_reader=lambda:BOOT,
            _check_physical_identities=Mock())
        self.calls, self.commands, self.records = [], [], []
        self.models = []
        self.transport_failure = None
        outer = self
        class Transport:
            def json(self, method, route, payload=None, timeout=1):
                outer.calls.append((method,route,copy.deepcopy(payload)))
                if outer.transport_failure: raise outer.transport_failure
                if (method,route)==('GET','/api/ps'): return {'models':copy.deepcopy(outer.models)}
                if (method,route)==('GET','/api/version'): return {'version':'synthetic'}
                if (method,route)==('POST','/api/generate'): return {'done':True,'response':'synthetic'}
                raise AssertionError('unexpected route')
        self.transport = Transport()
        self.backend = OllamaBackend(self.target,self.transport)
        self.observer = RecoveryObserver(self.p, {'ollama':self.target}, {'ollama':self.backend},poll_interval=.1)
        self.observer.command = Mock(side_effect=self.command)
        self.observer.driver_health = Mock(return_value={'reported_gpu_faults':False,'kernel_records_checked':10})
        self.births = {'202':'music-birth','303':'server-birth','304':'owned-runner-birth'}
        self.ollama_pids = {'303':'server-birth','304':'owned-runner-birth'}
        self.observer.processes = Mock(side_effect=lambda ident, deadline, **kwargs:
            dict(self.ollama_pids) if ident['id']==self.metas['ollama']['Id'] else
            {'202':'music-birth'} if ident['running'] else {})
        self.memory = lambda:96 if self.clock.t<.5 else 1
        self.apps = lambda:GPU0+',202,2000\n'+(GPU1+',304,96\n' if self.memory()>64 else '')
        self.gpu_override = None
        self.guard = self.new_guard()

    def new_guard(self, record=None):
        def persist(**fields):
            self.records.append(copy.deepcopy(fields))
            j=self.store.job('owned-job');j.update(fields);self.store.put_job(j)
        return OllamaCleanupGuard(self.a,self.target,self.owner,self.observer,
            record=record or persist,birth_reader=lambda pid:self.births[pid])

    def command(self, argv, deadline):
        self.commands.append(argv)
        self.assertGreater(deadline,self.clock.t)
        if argv[1]=='inspect':
            return json.dumps([next(m for m in self.metas.values() if argv[2] in (m['Id'],m['Name'][1:]))])
        if argv[1].startswith('--query-compute-apps='): return self.apps()
        if argv[1].startswith('--id='):
            self.assertEqual(argv[1],'--id='+GPU1,'GPU0 Music must not be forced idle')
            return self.gpu_override or f'{GPU1},{self.memory()},0,30,0,No'
        raise AssertionError('unexpected or mutating command')

    def prepare(self): self.guard.prepare(2)

    def test_36ms_handoff_waits_for_three_physical_samples_without_repeating_unload(self):
        self.prepare()
        self.assertTrue(self.backend.cleanup_models(['synthetic'],2)['cleanup_verified'])
        self.assertAlmostEqual(self.clock.t,.036442)
        proof=self.guard.verify(2)
        self.assertGreaterEqual(self.clock.t,.7)
        self.assertEqual(proof['stable_samples'],3)
        self.assertEqual(len([c for c in self.calls if c[:2]==('POST','/api/generate')]),1)
        self.assertTrue(any(x.get('ollama_cleanup_observation',{}).get('service_memory_mb',{}).get('ollama',{}).get('gpu1',0)>64 for x in self.records))

    def test_persistent_memory_breach_expires_without_success(self):
        self.prepare();self.memory=lambda:96
        with self.assertRaises(CleanupPending):self.guard.verify(.3)
        self.assertEqual(self.clock.t,.3)

    def test_intervening_memory_breach_resets_stability(self):
        self.memory=lambda:1 if self.clock.t<.2 or self.clock.t>=.3 else 96
        self.prepare();proof=self.guard.verify(2)
        self.assertEqual(proof['stable_samples'],3)
        self.assertGreaterEqual(self.clock.t,.5)

    def test_unknown_gpu_process_fails_immediately(self):
        self.prepare();self.apps=lambda:GPU1+',999,96\n'
        with self.assertRaisesRegex(RecoveryRequired,'unknown_or_duplicate'):self.guard.verify(2)
        self.assertAlmostEqual(self.clock.t,.036442)

    def test_new_and_reused_ollama_processes_fail_closed(self):
        for pids in ({'303':'server-birth','305':'new-client'},{'303':'server-birth','304':'reused'}):
            with self.subTest(pids=pids):
                self.ollama_pids={'303':'server-birth','304':'owned-runner-birth'}
                self.prepare();self.ollama_pids=pids
                with self.assertRaisesRegex(RecoveryRequired,'new_or_reused'):self.guard.verify(2)

    def test_pid_birth_change_after_gpu_sample_is_rejected(self):
        self.prepare();self.births['304']='reused-after-top'
        with self.assertRaisesRegex(RecoveryRequired,'process_birth_changed'):self.guard.verify(2)

    def test_generation_change_fails_closed(self):
        self.prepare();self.metas['ollama']['State']['StartedAt']='replacement'
        with self.assertRaisesRegex(RecoveryRequired,'generation_changed'):self.guard.verify(2)

    def test_on_demand_wallpaper_running_at_capture_fails_before_unload(self):
        self.metas['wallpaper']['State'].update(Running=True, Pid=101)
        with self.assertRaisesRegex(RecoveryRequired, 'on_demand_wallpaper_must_be_stopped'):
            self.prepare()
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.guard.context)
        self.assertIn('gpu1', self.state['owners'])

    def test_on_demand_wallpaper_started_after_capture_fails_closed(self):
        self.prepare()
        self.metas['wallpaper']['State'].update(Running=True, Pid=101)
        with self.assertRaisesRegex(RecoveryRequired, 'generation_changed'):
            self.guard.verify(2)

    def test_legacy_policy_without_ollama_binding_uses_original_cleanup(self):
        del self.state['recovery_policy']['policy']['services']['ollama']
        self.state['recovery_policy']['policy_id'] = digest(self.state['recovery_policy']['policy'])
        self.assertIsNone(cleanup_guard(self.a, self.target, self.owner, self.backend))

    def test_calibrated_policy_cannot_fallback_on_invalid_digest(self):
        self.state['recovery_policy']['policy_id'] = 'invalid'
        with self.assertRaisesRegex(RecoveryRequired, 'calibration_or_scope_unverified'):
            cleanup_guard(self.a, self.target, self.owner, self.backend, observer=self.observer)

    def test_policy_boot_and_lease_changes_fail_closed(self):
        original=copy.deepcopy(self.state)
        for mutate in (lambda:self.state['owners']['gpu1'].update(lease_id='foreign'),
                       lambda:self.state.update(verified_boot='other'),
                       lambda:self.state['recovery_policy'].update(policy_id='changed')):
            self.state=copy.deepcopy(original);self.prepare();mutate()
            with self.assertRaises(RecoveryRequired):self.guard.verify(2)

    def test_unknown_or_negative_memory_and_wrong_gpu_fail_closed(self):
        self.prepare()
        for row in (f'{GPU1},nan,0,30,0,No',f'{GPU1},-1,0,30,0,No',f'{GPU0},1,0,30,0,No'):
            self.gpu_override=row
            with self.subTest(row=row),self.assertRaises(RecoveryRequired):self.guard.verify(2)

    def test_driver_fault_temperature_invalid_utilization_and_ecc_fail_without_polling(self):
        self.prepare()
        self.observer.driver_health.side_effect=RecoveryRequired('kernel_gpu_driver_fault')
        with self.assertRaisesRegex(RecoveryRequired,'driver_fault'):self.guard.verify(2)
        self.observer.driver_health.side_effect=None
        for row in (f'{GPU1},1,101,30,0,No',f'{GPU1},1,0,99,0,No',f'{GPU1},1,0,30,1,No'):
            self.gpu_override=row
            with self.subTest(row=row),self.assertRaises(RecoveryRequired):self.guard.verify(2)
        self.assertAlmostEqual(self.clock.t,.036442)

    def test_foreign_residency_and_backend_timeout_are_not_memory_retries(self):
        self.prepare();self.models=[{'name':'foreign'}]
        with self.assertRaisesRegex(RecoveryRequired,'unexplained_backend_activity'):self.guard.verify(2)
        self.models=[];self.transport_failure=Uncertain('backend_timeout')
        with self.assertRaises(Uncertain):self.guard.verify(2)
        self.assertAlmostEqual(self.clock.t,.036442)

    def test_control_timeout_is_not_memory_retry(self):
        self.prepare();self.observer.command.side_effect=Uncertain('control_timeout')
        with self.assertRaises(Uncertain):self.guard.verify(2)
        self.assertAlmostEqual(self.clock.t,.036442)

    def test_unverified_participating_queue_is_not_excused(self):
        self.prepare()
        self.observer.targets['expected-comfy']=types.SimpleNamespace(resources=('gpu1',),mode_switch=None,kind='comfy')
        transport=Mock();transport.json.return_value={'queue_running':None,'queue_pending':[]}
        self.observer.backends['expected-comfy']=types.SimpleNamespace(transport=transport)
        with self.assertRaisesRegex(RecoveryRequired,'invalid_expected_queue'):self.guard.verify(2)

    def test_changed_job_or_observer_policy_is_rejected(self):
        self.prepare();j=self.store.job('owned-job');j['lease_id']='changed';self.store.put_job(j)
        with self.assertRaisesRegex(RecoveryRequired,'job_binding'):self.guard.verify(2)
        j['lease_id']='owned-lease';self.store.put_job(j)
        self.observer.p=copy.deepcopy(self.p);self.observer.p['samples']=2
        with self.assertRaises(RecoveryRequired):self.guard.verify(2)

    def test_other_service_residency_is_not_excused(self):
        self.prepare();self.apps=lambda:GPU1+',202,96\n'
        with self.assertRaisesRegex(RecoveryRequired,'baseline_music'):self.guard.verify(2)

    def test_ollama_on_an_unleased_gpu_is_rejected(self):
        self.prepare();self.apps=lambda:GPU0+',304,96\n'
        with self.assertRaisesRegex(RecoveryRequired,'unleased_gpu'):self.guard.verify(2)

    def test_observation_storage_failure_cannot_release(self):
        self.prepare();self.guard.record=Mock(side_effect=OSError('fsync uncertain'))
        with self.assertRaises(OSError):self.guard.verify(2)
        self.assertIn('gpu1',self.state['owners'])

    def worker(self):
        runner=Runner.__new__(Runner)
        runner.admission,runner.store,runner.targets,runner.backends=self.a,self.store,{'ollama':self.target},{'ollama':self.backend}
        runner.job_mutex=Mutex()
        runner.recovery=types.SimpleNamespace(observer=self.observer,prepare=Mock())
        runner.closed=False;runner.max_pending=5;runner.futures={};runner.pending={};runner.wake=threading.Event()
        payload={'model':'synthetic','prompt':'neutral'}
        j=self.store.job('owned-job');j.update(state='waiting',operation='generate',payload=payload,
            request_hash=hashlib.sha256(encode(dict(target='ollama',operation='generate',payload=payload))).hexdigest())
        self.store.put_job(j);self.owner['request_hash']=j['request_hash'];self.state['owners']['gpu1']['request_hash']=j['request_hash']
        def mark(phase,*args):self.state['owners']['gpu1']['phase']=phase
        def release(proof):
            self.assertGreaterEqual(self.clock.t,.7)
            self.assertEqual(proof['physical_ollama_idle']['stable_samples'],3)
            self.state['owners'].clear()
        lease=types.SimpleNamespace(owner=self.owner,resources=('gpu1',),mark=mark,release=Mock(side_effect=release),close=Mock())
        # Inject only the birth reader; exercise the actual factory and worker.
        patch('aihub_gpu_runner.ollama_cleanup.process_birth',side_effect=lambda pid:self.births[pid]).start()
        original=OllamaCleanupGuard.__init__
        def init(guard,*args,**kw):
            kw['birth_reader']=lambda pid:self.births[pid];original(guard,*args,**kw)
        patch.object(OllamaCleanupGuard,'__init__',init).start()
        return runner,lease,payload

    def test_runner_holds_lease_until_physical_proof_then_reconnect_does_not_submit(self):
        runner,lease,payload=self.worker();runner._work('owned-job',lease)
        self.assertEqual(self.store.job('owned-job')['state'],'success')
        lease.release.assert_called_once()
        self.assertEqual(self.state['owners'],{})
        old=len(self.calls);self.assertEqual(runner.submit('owned-job','ollama','generate',payload)['state'],'success')
        self.assertEqual(len(self.calls),old)
        posts=[x for x in self.calls if x[0]=='POST']
        self.assertEqual(len(posts),2) # One original inference, one owned unload.

    def test_runner_keeps_lease_and_unpublished_result_on_cleanup_deadline(self):
        runner,lease,payload=self.worker();self.memory=lambda:96
        runner._work('owned-job',lease)
        self.assertEqual(self.store.job('owned-job')['state'],'cleanup_pending')
        lease.release.assert_not_called();self.assertIn('gpu1',self.state['owners'])
        self.assertNotIn('results/owned-job.json',self.store.values)

    def test_lost_inference_ack_does_not_replay_or_unload(self):
        runner,lease,payload=self.worker();self.transport_failure=TimeoutError('lost ack')
        runner._work('owned-job',lease)
        self.assertEqual(self.store.job('owned-job')['state'],'reconciling')
        lease.release.assert_not_called()
        self.assertEqual(len([c for c in self.calls if c[0]=='POST']),1)
        runner.submit('owned-job','ollama','generate',payload)
        self.assertEqual(len([c for c in self.calls if c[0]=='POST']),1)

    def test_lost_unload_ack_is_not_reissued_and_retains_ownership(self):
        runner,lease,payload=self.worker()
        original=self.transport.json
        def lost_unload(method,route,payload=None,timeout=1):
            if method=='POST' and payload and payload.get('keep_alive')==0:
                self.calls.append((method,route,copy.deepcopy(payload)))
                raise TimeoutError('unload acknowledgement lost')
            return original(method,route,payload,timeout)
        self.transport.json=lost_unload
        runner._work('owned-job',lease)
        self.assertEqual(self.store.job('owned-job')['state'],'cleanup_pending')
        lease.release.assert_not_called()
        runner.submit('owned-job','ollama','generate',payload)
        self.assertEqual(len([c for c in self.calls if c[0]=='POST']),2)

    def test_worker_observation_persistence_uncertainty_keeps_owner(self):
        runner,lease,payload=self.worker();original=runner._update
        def write(**fields):
            if 'ollama_cleanup_observation' in fields:raise OSError('fsync uncertain')
            return original('owned-job',**fields)
        runner._update=lambda job_id,**fields:write(**fields)
        runner._work('owned-job',lease)
        self.assertEqual(self.store.job('owned-job')['state'],'reconciling')
        lease.release.assert_not_called();self.assertIn('gpu1',self.state['owners'])
        runner.submit('owned-job','ollama','generate',payload)
        self.assertEqual(len([c for c in self.calls if c[0]=='POST']),2)

    def test_host_stage_normal_cleanup_uses_same_physical_guard(self):
        j=self.store.job('owned-job');j.update(host_stage=True,host_exit_code=0,
            host_requests=[{'state':'completed','model':'synthetic'}]);self.store.put_job(j)
        stage=types.SimpleNamespace(store=self.store,job_id='owned-job',is_ollama=True,dual=False,
            admission=self.a,target=self.target,lease=types.SimpleNamespace(owner=self.owner),
            transport_factory=lambda url:self.transport,update=lambda **fields:None)
        original=OllamaCleanupGuard.__init__
        def init(guard,*a,**kw):kw['birth_reader']=lambda pid:self.births[pid];original(guard,*a,**kw)
        with patch('aihub_gpu_runner.ollama_cleanup.RecoveryObserver',return_value=self.observer),patch.object(OllamaCleanupGuard,'__init__',init):
            proof=HostStage._cleanup(stage)
        self.assertEqual(proof['physical_ollama_idle']['stable_samples'],3)
        self.assertGreaterEqual(self.clock.t,.7)

    def test_host_without_owned_requests_cannot_excuse_residency(self):
        j=self.store.job('owned-job');j.update(host_stage=True,host_exit_code=0,host_requests=[]);self.store.put_job(j)
        self.prepare()
        with self.assertRaisesRegex(RecoveryRequired,'baseline_ollama'):self.guard.verify(2)

    def test_missing_policy_keeps_legacy_path_and_dual_keeps_existing_restoration(self):
        self.state.pop('recovery_policy')
        self.assertIsNone(cleanup_guard(self.a,self.target,self.owner,self.backend))
        self.target.mode_switch={'synthetic':'already separately verified'}
        self.assertIsNone(cleanup_guard(self.a,self.target,self.owner,self.backend))


if __name__=='__main__':unittest.main()
