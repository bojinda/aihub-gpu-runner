"""Disposable stores + real admission/physical locks; all target I/O simulated.

The target double models a durable application submission barrier. This tests
the contract, not installed ComfyUI enforcement or actual termination.
"""
import copy
import hashlib
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from aihub_gpu_runner.core import NativeLock
from aihub_gpu_runner.practical_recovery import PracticalRecovery, Refused
from aihub_gpu_runner.recovery_summary import read_recovery_summary
from tests.recovery_fixture import Fixture, BOOT


class Crash(BaseException):
    pass


class Target:
    def __init__(self, fixture):
        self.f = fixture
        self.lock = threading.RLock()
        self.calls = []
        self.hook = lambda stage: None
        self.obs = dict(boot=BOOT, observed_at=fixture.now, container=fixture.original,
            worker_absent=True, original_execution_gone=True, process_identity_verified=True,
            queue_available=True, queue_running=[], queue_pending=[], write_epoch=0,
            write_barrier_available=True, stop_capability='reviewed_generation_bound',
            readiness={'ready':True}, health={'counter':0}, idle_websockets=0,
            music_memory_mib=454, gpu_telemetry_available=True)
        self.f.store.write('fixture-target.json', dict(operation=None, epoch=0))

    def observe(self, *, timeout):
        self.hook('observe')
        with self.lock:
            result = copy.deepcopy(self.obs)
            result['observed_at'] = self.f.now
            return result

    def fence(self, operation, epoch, container, *, timeout):
        self.hook('before_fence')
        with self.lock:
            if self.obs['write_epoch'] != epoch or self.obs['container'] != container:
                raise Refused('write_or_generation_raced')
            self.f.store.write('fixture-target.json', dict(operation=operation, epoch=epoch))
        self.hook('after_fence')

    def fence_verified(self, operation):
        return self.f.store.read('fixture-target.json')['operation'] == operation

    def submit_manual(self, prompt):
        with self.lock:
            if self.f.store.read('fixture-target.json')['operation'] is not None:
                return False
            self.obs['write_epoch'] += 1
            self.obs['queue_pending'].append(prompt)
            return True

    def stop_exact(self, container, lease, stage, operation, *, deadline):
        self.hook('before_stop')
        with self.lock:
            if (self.f.now >= deadline or not self.fence_verified(operation) or self.obs['container'] != container or
                    lease != self.f.job['lease_id'] or
                    set(self.obs['queue_running']+self.obs['queue_pending']) - {'owned-prompt'}):
                raise Refused('actuator_binding_or_scope_changed')
            self.calls.append(stage)
            self.obs['container'] = dict(container, running=False, pid=0)
            self.obs.update(original_execution_gone=True, queue_running=[], queue_pending=[])
        self.hook('after_stop')

    def unfence(self, operation):
        self.hook('before_unfence')
        with self.lock:
            if self.f.store.read('fixture-target.json')['operation'] not in (None, operation):
                raise Refused('different_fence_owner')
            self.f.store.write('fixture-target.json', dict(operation=None, epoch=self.obs['write_epoch']))
        self.hook('after_unfence')

    def access_restored(self, operation):
        return self.f.store.read('fixture-target.json')['operation'] is None


class PracticalFixture(Fixture):
    def __init__(self, case):
        super().__init__(case)
        self.target = Target(self)
        self.control = PracticalRecovery(self.a, self.target, b'offline-key'*5,
            clock=lambda:self.now, monotonic=lambda:self.now)
        self.control.provision_fixture()

    def running(self, completed=False, expired=True):
        self.job = self.store.job(self.job['request_id'])
        container = dict(self.original, running=True, pid=71, started_at='owned-generation')
        self.job.update(execution_deadline=self.now-10 if expired else self.now+100,
                        execution_container=container, backend_detail={'prompt_id':'owned-prompt'})
        life = self.job['wallpaper_lifecycle']
        life.update(start_intent='start-once', submission_intent='submit-once',
                    running_container=container, prompt='owned-prompt', original_pids={'71':'birth'},
                    phase='artifact_saved' if completed else 'submit_intent')
        self.target.obs.update(container=container, original_execution_gone=False,
                               queue_running=[] if completed else ['owned-prompt'])
        if completed:
            self.store.write_bytes('results/'+self.job['request_id']+'.bin', b'fixture-output')
            life['result']={'artifact_available':True}
            self.store.write('results/'+self.job['request_id']+'.json', life['result'])
            for ext, key in (('.bin','artifact_sha256'),('.json','result_sha256')):
                life[key] = hashlib.sha256(self.store.path('results/'+self.job['request_id']+ext).read_bytes()).hexdigest()
            self.job['completion_verified']=True
        self.store.put_job(self.job)

    def check(self, mode='recover'):
        c = self.control.check(mode, 'admin')
        assert c['eligible'], c
        return c

    def execute(self, checked=None, loss=True):
        c=checked or self.check()
        return self.control.execute(c['token'],'admin',True,accept_work_loss=loss,expected_mode=c['action'])

    def journal(self):
        return self.a.snapshot()['practical_control']


class PracticalTests(unittest.TestCase):
    def fixture(self):
        return PracticalFixture(self)

    def test_check_is_read_only(self):
        f=self.fixture()
        before={str(p):p.read_bytes() for p in f.store.root.rglob('*') if p.is_file()}
        self.assertTrue(f.check()['eligible'])
        self.assertEqual(before,{str(p):p.read_bytes() for p in f.store.root.rglob('*') if p.is_file()})

    def test_prestart_454_mib_auto_retires_without_operator_or_backend_calls(self):
        f=self.fixture(); original=copy.deepcopy(f.job)
        f.target.obs.update(gpu_telemetry_available=False,queue_available=False,idle_websockets=12)
        result=f.control.automatic()
        self.assertEqual(result['terminal'],'failed'); self.assertEqual(f.a.snapshot()['owners'],{})
        self.assertEqual(f.target.calls,[])
        job=f.store.job(f.job['request_id'])
        self.assertEqual(job['error_code'],'automatic_prestart_failed_no_replay')
        self.assertEqual(job['wallpaper_lifecycle'],original['wallpaper_lifecycle'])
        self.assertEqual(f.a.snapshot()['recoveries'][-1]['provenance'],'automatic_policy')
        self.assertTrue(f.a.snapshot()['recoveries'][-1]['no_replay'])
        self.assertFalse(read_recovery_summary(f.store)['operator_required'])

    def test_automatic_refuses_any_intent_even_false(self):
        for field in ('start_intent','submission_intent','term_intent','running_container','prompt'):
            with self.subTest(field=field):
                f=self.fixture(); f.job['wallpaper_lifecycle'][field]=False; f.store.put_job(f.job)
                self.assertFalse(f.control.check('automatic','runner-policy')['eligible'])

    def test_automatic_refuses_started_execution(self):
        f=self.fixture();f.running()
        self.assertFalse(f.control.automatic()['eligible']);self.assertEqual(f.target.calls,[])

    def test_readiness_pending_does_not_retain_dead_lease_or_claim_ready(self):
        f=self.fixture();f.target.obs['readiness']={'ready':None,'reason':'GPU telemetry unavailable'}
        result=f.control.automatic()
        self.assertEqual(result['result'],'released_readiness_pending')
        self.assertEqual(f.a.snapshot()['owners'],{})
        self.assertFalse(f.a.maintenance_blocked())

    def test_complete_stuck_preserves_success_and_artifact(self):
        f=self.fixture();f.running(completed=True)
        before=f.store.path('results/'+f.job['request_id']+'.bin').read_bytes()
        result=f.execute()
        self.assertEqual(result['terminal'],'success');self.assertEqual(f.target.calls,['graceful'])
        self.assertEqual(f.store.path('results/'+f.job['request_id']+'.bin').read_bytes(),before)
        self.assertEqual(f.journal()['admission'],'open')

    def test_known_abandoned_manual_needs_work_loss_acceptance(self):
        f=self.fixture();f.running();c=f.check()
        self.assertTrue(c['loss_required'])
        with self.assertRaises(Refused):f.execute(c,loss=False)
        result=f.execute(c)
        self.assertEqual(result['terminal'],'failed');self.assertEqual(f.target.calls,['graceful'])

    def test_live_unexpired_execution_requires_emergency(self):
        f=self.fixture();f.running(expired=False)
        self.assertFalse(f.control.check('recover','admin')['eligible'])
        result=f.execute(f.check('emergency'))
        self.assertEqual(result['terminal'],'failed');self.assertEqual(f.target.calls,['force'])

    def test_emergency_requires_explicit_work_loss_even_with_complete_output(self):
        f=self.fixture();f.running(completed=True);c=f.check('emergency')
        with self.assertRaises(Refused):f.execute(c,loss=False)
        self.assertEqual(f.execute(c)['terminal'],'success')

    def test_action_token_cannot_cross_recover_and_emergency_routes(self):
        f=self.fixture();f.running();c=f.check('emergency')
        with self.assertRaises(Refused):
            f.control.execute(c['token'],'admin',True,True,expected_mode='recover')
        self.assertEqual(f.target.calls,[])

    def test_emergency_bypasses_cache_health_and_telemetry_not_identity(self):
        f=self.fixture();f.running(expired=False)
        f.target.obs.update(music_memory_mib=20000,gpu_telemetry_available=False,health={'status':'unhealthy'},idle_websockets=20)
        self.assertEqual(f.execute(f.check('emergency'))['terminal'],'failed')

    def test_emergency_queue_unavailable_needs_continuously_verified_exclusivity(self):
        f=self.fixture();f.running();f.target.obs['queue_available']=False
        self.assertFalse(f.control.check('emergency','admin')['eligible'])
        f.target.obs['exclusive_execution_lease']=f.job['lease_id']
        self.assertEqual(f.execute(f.check('emergency'))['terminal'],'failed')

    def test_unrelated_manual_target_work_refuses_both_modes(self):
        for mode in ('recover','emergency'):
            with self.subTest(mode=mode):
                f=self.fixture();f.running();f.target.submit_manual('unrelated')
                c=f.control.check(mode,'admin')
                self.assertIn('unrelated_target_work',c['failed_checks']);self.assertEqual(f.target.calls,[])

    def test_idle_websockets_and_health_churn_do_not_stale_approval(self):
        f=self.fixture();f.running(completed=True);c=f.check()
        f.target.obs.update(idle_websockets=99,health={'counter':1000},music_memory_mib=454)
        self.assertEqual(f.execute(c)['terminal'],'success')

    def test_lease_only_retirement_does_not_stop_unrelated_music(self):
        f=self.fixture();f.target.obs['readiness']={'ready':False,'reason':'Music active'}
        f.target.obs['unrelated_music_running']=True
        self.assertEqual(f.control.automatic()['result'],'released_readiness_pending')
        self.assertEqual(f.target.calls,[])

    def test_essential_drift_invalidates_approval(self):
        for key,value in (('write_epoch',1),('execution_progress',50),('process_identity_verified',False)):
            with self.subTest(key=key):
                f=self.fixture();f.running();c=f.check();f.target.obs[key]=value
                with self.assertRaises(Refused):f.execute(c)
                self.assertEqual(f.target.calls,[])

    def test_container_generation_drift_blocks_even_emergency(self):
        f=self.fixture();f.running();c=f.check('emergency')
        f.target.obs['container']=dict(f.target.obs['container'],started_at='replacement')
        with self.assertRaises(Refused):f.execute(c)
        self.assertEqual(f.target.calls,[])

    def test_actual_docker_id_only_stop_capability_is_not_qualified(self):
        f=self.fixture();f.running();f.target.obs['stop_capability']='docker-id-only'
        self.assertFalse(f.control.check('emergency','admin')['eligible'])

    def test_missing_queue_fields_do_not_mean_empty(self):
        f=self.fixture();f.running();f.target.obs.pop('queue_running')
        self.assertFalse(f.control.check('emergency','admin')['eligible'])

    def test_submission_between_check_and_fence_prevents_stop(self):
        f=self.fixture();f.running();c=f.check()
        f.target.hook=lambda s:f.target.submit_manual('foreign') if s=='before_fence' else None
        self.assertEqual(f.execute(c)['result'],'operator_required')
        self.assertEqual(f.target.calls,[]);self.assertTrue(f.a.maintenance_blocked())

    def test_bounded_stop_budget_expiring_before_intent_does_not_signal(self):
        f=self.fixture();f.running();c=f.check()
        def hook(stage):
            if stage=='after_fence':f.now+=46
        f.target.hook=hook
        self.assertEqual(f.execute(c)['result'],'operator_required')
        self.assertEqual(f.target.calls,[])

    def test_concurrent_direct_submission_after_fence_is_excluded(self):
        f=self.fixture();f.running();answers=[]
        def hook(stage):
            if stage=='after_fence':
                t=threading.Thread(target=lambda:answers.append(f.target.submit_manual('foreign')))
                t.start();t.join(2);self.assertFalse(t.is_alive())
                self.assertTrue(f.a.maintenance_blocked())
        f.target.hook=hook
        self.assertEqual(f.execute()['terminal'],'failed');self.assertEqual(answers,[False])
        self.assertTrue(f.target.submit_manual('new-work-after-restoration'))

    def test_concurrent_double_execution_has_only_one_effect(self):
        f=self.fixture();f.running();c=f.check()
        def execute():
            try:return f.execute(c)
            except Refused:return 'refused'
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:execute(),range(2)))
        self.assertEqual(f.target.calls,['graceful'])
        self.assertEqual(sum(isinstance(r,dict) for r in results),1)
        self.assertEqual(len(f.a.snapshot()['recoveries']),2)

    def test_partial_pair_must_not_be_released(self):
        f=self.fixture();s=f.a.snapshot();s['owners'].pop('gpu1');f.store.write('ownership.json',s)
        self.assertFalse(f.control.check('emergency','admin')['eligible'])
        self.assertEqual(set(f.a.snapshot()['owners']),{'gpu0'})

    def test_different_paired_owner_is_not_touched(self):
        f=self.fixture();s=f.a.snapshot();s['owners']['gpu1']['lease_id']='foreign';f.store.write('ownership.json',s)
        self.assertFalse(f.control.check('emergency','admin')['eligible'])

    def test_physical_worker_lock_blocks_recovery(self):
        f=self.fixture()
        with NativeLock(f.a.lock_paths['gpu0'],create=False).held():
            self.assertFalse(f.control.check('emergency','admin')['eligible'])

    def test_physical_lock_replacement_during_stop_prevents_pair_release(self):
        f=self.fixture();f.running();c=f.check()
        def hook(stage):
            if stage=='after_stop':
                path=f.a.lock_paths['gpu0'];path.rename(path.with_suffix('.old'));path.write_bytes(b'')
        f.target.hook=hook
        self.assertEqual(f.execute(c)['result'],'operator_required')
        self.assertEqual(set(f.a.snapshot()['owners']),{'gpu0','gpu1'})

    def test_ticket_expiring_during_last_observation_is_not_consumed(self):
        f=self.fixture();f.running();c=f.check()
        def hook(stage):
            if stage=='observe':f.now+=60
        f.target.hook=hook
        with self.assertRaises(Refused):f.execute(c)
        self.assertEqual(f.journal()['admission'],'open');self.assertEqual(f.target.calls,[])

    def test_lost_atomic_release_reply_is_verified_without_second_audit(self):
        f=self.fixture();f.running();c=f.check();write=f.store.write;failed=False
        def fault(name,value):
            nonlocal failed
            write(name,value)
            if name=='ownership.json' and value.get('practical_receipts') and not failed:
                failed=True;raise OSError('commit reply lost after durable write')
        with patch.object(f.store,'write',side_effect=fault):result=f.execute(c)
        self.assertEqual(result['result'],'operator_required')
        self.assertEqual(f.control.verify_interrupted(c['operation'])['terminal'],'failed')
        self.assertEqual(len(f.a.snapshot()['recoveries']),2);self.assertEqual(f.target.calls,['graceful'])

    def test_stale_actor_clock_session_and_signature(self):
        for variant in ('actor','time','restart','signature','unconfirmed'):
            with self.subTest(variant=variant):
                f=self.fixture();c=f.check()
                if variant=='time':f.now+=60
                if variant=='restart':f.control.session='new'
                if variant=='signature':c['token']+='a'
                with self.assertRaises(Refused):f.control.execute(c['token'],'other' if variant=='actor' else 'admin',variant!='unconfirmed')
                self.assertTrue(f.a.snapshot()['owners'])

    def test_essential_backend_unavailable_fails_closed(self):
        f=self.fixture();f.running();f.target.hook=lambda stage:(_ for _ in ()).throw(OSError('unavailable'))
        self.assertFalse(f.control.check('emergency','admin')['eligible'])
        self.assertTrue(f.a.snapshot()['owners'])

    def test_no_start_auto_can_retire_when_backend_is_unavailable(self):
        f=self.fixture();f.target.hook=lambda stage:(_ for _ in ()).throw(OSError('unavailable'))
        self.assertEqual(f.control.automatic()['result'],'released_readiness_pending')
        self.assertEqual(f.a.snapshot()['owners'],{});self.assertEqual(f.target.calls,[])

    def test_no_start_retirement_ignores_unrelated_new_target_generation(self):
        f=self.fixture();c=f.check()
        f.target.obs.update(container={'id':'unrelated-new-container','running':True,'pid':99},
                            queue_running=['unrelated-prompt'],write_epoch=1,readiness={'ready':False})
        self.assertEqual(f.execute(c)['result'],'released_readiness_pending');self.assertEqual(f.target.calls,[])

    def test_pre_effect_failure_can_be_verified_aborted(self):
        f=self.fixture();f.running();c=f.check()
        f.target.hook=lambda s:(_ for _ in ()).throw(OSError('fence reply lost')) if s=='after_fence' else None
        result=f.execute(c);self.assertEqual(result['result'],'operator_required')
        f.target.hook=lambda s:None
        self.assertEqual(f.control.abort_before_effect(result['operation'])['result'],'aborted_verified')
        self.assertEqual(f.target.calls,[]);self.assertTrue(f.a.snapshot()['owners']);self.assertFalse(f.a.maintenance_blocked())

    def test_uncertain_stop_cannot_abort_or_repeat(self):
        f=self.fixture();f.running();c=f.check()
        f.target.hook=lambda s:(_ for _ in ()).throw(OSError('stop request unknown')) if s=='before_stop' else None
        result=f.execute(c);f.target.hook=lambda s:None
        with self.assertRaises(Refused):f.control.abort_before_effect(result['operation'])
        with self.assertRaises(Refused):f.execute(c)
        self.assertEqual(f.target.calls,[]);self.assertTrue(f.a.maintenance_blocked())

    def test_normal_failure_allows_fresh_confirmed_emergency_escalation(self):
        f=self.fixture();f.running();c=f.check()
        f.target.hook=lambda s:(_ for _ in ()).throw(OSError('graceful unavailable')) if s=='before_stop' else None
        first=f.execute(c);f.target.hook=lambda s:None
        second=f.execute(f.check('emergency'))
        self.assertEqual(second['terminal'],'failed');self.assertEqual(f.target.calls,['force'])
        self.assertEqual(f.journal()['attempts'][first['operation']]['superseded_by'],second['operation'])

    def test_new_emergency_confirmation_is_not_permanently_consumed_by_prior_failure(self):
        f=self.fixture();f.running();c=f.check('emergency')
        f.target.hook=lambda s:(_ for _ in ()).throw(OSError('unknown force response')) if s=='before_stop' else None
        first=f.execute(c);f.target.hook=lambda s:None
        with self.assertRaises(Refused):f.execute(c)
        second=f.execute(f.check('emergency'))
        self.assertEqual(second['terminal'],'failed');self.assertEqual(f.target.calls,['force'])
        self.assertNotEqual(first['operation'],second['operation'])

    def test_crash_after_stop_only_verifies_does_not_repeat_effect(self):
        f=self.fixture();f.running();c=f.check()
        f.target.hook=lambda s:(_ for _ in ()).throw(Crash()) if s=='after_stop' else None
        with self.assertRaises(Crash):f.execute(c)
        f.target.hook=lambda s:None
        f.control=PracticalRecovery(f.a,f.target,b'offline-key'*5,clock=lambda:f.now,monotonic=lambda:f.now)
        self.assertEqual(f.control.verify_interrupted(c['operation'])['terminal'],'failed')
        self.assertEqual(f.target.calls,['graceful']);self.assertFalse(f.a.maintenance_blocked())

    def test_job_write_interruption_recovers_from_atomic_paired_receipt(self):
        f=self.fixture();f.running(completed=True);c=f.check()
        with patch.object(f.store,'put_job',side_effect=OSError('disk full')):
            result=f.execute(c)
        self.assertEqual(result['result'],'operator_required');self.assertEqual(f.a.snapshot()['owners'],{})
        self.assertTrue(f.a.maintenance_blocked())
        self.assertIsNone(f.a.try_acquire('concurrent-job','request-hash',['gpu0'],'music'))
        self.assertEqual(f.control.verify_interrupted(c['operation'])['terminal'],'success')
        self.assertEqual(f.target.calls,['graceful'])

    def test_restoration_failure_never_claims_success_and_can_verify(self):
        for stage in ('before_unfence','after_unfence'):
            with self.subTest(stage=stage):
                f=self.fixture();f.running();c=f.check()
                f.target.hook=lambda s:(_ for _ in ()).throw(OSError('lost restoration')) if s==stage else None
                result=f.execute(c);self.assertEqual(result['result'],'operator_required')
                self.assertTrue(f.a.maintenance_blocked());f.target.hook=lambda s:None
                self.assertEqual(f.control.verify_interrupted(c['operation'])['terminal'],'failed')
                self.assertEqual(f.target.calls,['graceful'])

    def test_missing_artifact_is_not_completed_success(self):
        f=self.fixture();f.running(completed=True,expired=False)
        f.store.path('results/'+f.job['request_id']+'.bin').unlink()
        self.assertFalse(f.control.check('recover','admin')['eligible'])
        self.assertEqual(f.execute(f.check('emergency'))['terminal'],'failed')

    def test_corrupt_or_unavailable_journal_does_not_release(self):
        f=self.fixture()
        with self.assertRaises(ValueError):f.store.write_bytes('ownership.json',b'{broken')
        f.store.path('ownership.json').write_bytes(b'{broken') # explicit filesystem corruption injection
        self.assertTrue(f.a.maintenance_blocked())
        self.assertFalse(f.control.check('emergency','admin')['eligible'])

    def test_history_and_unrelated_artifact_preserved(self):
        f=self.fixture();before=copy.deepcopy(f.a.snapshot());f.control.automatic()
        after=f.a.snapshot()
        self.assertEqual(after['recoveries'][:-1],before['recoveries'])
        self.assertEqual(after['automatic_recovery'],before['automatic_recovery'])
        self.assertEqual(f.store.path('results/preserved.bin').read_bytes(),b'preserved artifact')

    def test_verifying_historical_success_does_not_claim_new_boot_ready(self):
        f=self.fixture();result=f.control.automatic();f.boot='22222222-2222-4222-8222-222222222222'
        verified=f.control.verify_interrupted(result['operation'])
        self.assertEqual(verified['result'],'released_readiness_pending')
        self.assertEqual(verified['readiness']['reason'],'boot_changed_since_recovery')

    def test_historical_receipt_does_not_claim_new_owners_ready(self):
        f=self.fixture();result=f.control.automatic();state=f.a.snapshot()
        state['owners']={'gpu0':dict(f.before['owners']['gpu0'],lease_id='new-lease',job_id='new-job')}
        f.store.write('ownership.json',state)
        verified=f.control.verify_interrupted(result['operation'])
        self.assertEqual(verified['result'],'released_readiness_pending')
        self.assertEqual(verified['readiness']['reason'],'new_work_owns_resources')
