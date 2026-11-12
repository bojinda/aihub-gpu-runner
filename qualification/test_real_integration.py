import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
from integration_support import Environment,Comfy,BASE,ROOT,wait,port
from aihub_gpu_runner.core import RecoveryRequired
from aihub_gpu_runner.practical_recovery import Refused

class RealIntegration(unittest.TestCase):
    def env(self,**kwargs):
        e=Environment(**kwargs);self.addCleanup(e.close);return e

    def test_automatic_454_over_450_unavailable_observers(self):
        e=self.env();e.observer.prestart_error='music_454_MiB_exceeds_450'
        e.submit();e.runner.future('owned').result(5)
        with patch.object(e.observer,'container',side_effect=AssertionError('backend read prohibited')):
            wait(lambda:not e.a.snapshot()['owners'],5)
        job=e.store.job('owned');self.assertEqual(job['state'],'failed')
        self.assertEqual(job['error_code'],'automatic_prestart_failed_no_replay')
        self.assertFalse(e.a.maintenance_blocked());self.assertIsNone(e.comfy.proc)

    def test_real_normal_lifecycle_preserves_result_and_restores_access(self):
        e=self.env();e.submit();e.runner.future('owned').result(110)
        job=e.store.job('owned');self.assertEqual(job['state'],'success',job)
        self.assertEqual(e.a.snapshot()['owners'],{});self.assertFalse(e.a.maintenance_blocked())
        self.assertTrue(e.runner.artifact('owned')[0].startswith(b'\x89PNG'))
        self.assertEqual(e.comfy.barrier.read()['mode'],'open')
        self.assertEqual(len(e.a.snapshot()['normal_stop_intents']),1)

    def test_normal_completion_preserves_existing_force_policy_native_signals(self):
        e=self.env(stubborn='native');e.submit();e.runner.future('owned').result(110)
        self.assertEqual(e.store.job('owned')['state'],'success',e.store.job('owned'))
        self.assertEqual(e.a.snapshot()['owners'],{});self.assertEqual(e.comfy.barrier.read()['mode'],'open')
        intent=next(iter(e.a.snapshot()['normal_stop_intents'].values()))
        self.assertEqual(intent['stage'],'force')
        signals=e.comfy.barrier.read()['signals'][intent['id']]
        self.assertTrue(signals['term_intent'])
        if hasattr(e.comfy,'id'):self.assertTrue(signals['kill_intent'])  # Real namespace PID 1, no fixture signal handler.
        self.assertTrue(e.runner.artifact('owned')[0].startswith(b'\x89PNG'))

    def test_emergency_live_worker_handoff_real_pidfd_no_foreign_kill(self):
        e=self.env(seconds=120,stubborn=True);e.submit();e.active()
        checked=e.control.check('emergency','human')
        self.assertTrue(checked['eligible'],checked)
        result=e.control.execute(checked['token'],'human',True,True,expected_mode='emergency')
        self.assertEqual(result['result'],'released_readiness_pending',result)
        state=e.a.snapshot();self.assertEqual(state['owners'],{})
        self.assertIn(checked['operation'],state['worker_handoffs'])
        self.assertEqual(e.store.job('owned')['state'],'failed')
        self.assertEqual(e.comfy.barrier.read()['mode'],'open')
        sig=e.comfy.barrier.read()['signals'][checked['operation']]
        self.assertTrue(sig['term_intent']);self.assertTrue(sig['kill_intent'])
        self.assertEqual(e.control.verify_interrupted(checked['operation'])['result'],result['result'])
        with self.assertRaises(Refused):e.control.execute(checked['token'],'human',True,True,expected_mode='emergency')

    def test_real_barrier_all_writes_get_and_idle_websocket_regression(self):
        e=self.env(seconds=120);e.submit();e.active()
        async def sockets():
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(e.comfy.url+'/ws?clientId=idle-observer') as ws:
                    s=e.comfy.barrier.read();e.comfy.barrier.fence('b'*32,s['epoch'],s['generation'])
                    for route in ('/queue','/history','/aihub/recovery/status','/system_stats'):
                        async with session.get(e.comfy.url+route) as r:self.assertEqual(r.status,200)
                    for route in ('/prompt','/queue','/history','/interrupt','/free'):
                        async with session.post(e.comfy.url+route,json={}) as r:self.assertEqual(r.status,409)
                    await ws.ping();self.assertFalse(ws.closed)
                    e.comfy.barrier.restore('b'*32)
        asyncio.run(sockets())

    def test_concurrent_direct_manual_prompt_refused_and_preserved(self):
        e=self.env(seconds=120);e.submit();e.active()
        checked=e.control.check('emergency','human');self.assertTrue(checked['eligible'],checked)
        manual=e.comfy.manual(0)
        with self.assertRaises(Refused):e.control.execute(checked['token'],'human',True,True,expected_mode='emergency')
        result=e.control.check('emergency','human')
        self.assertFalse(result['eligible']);self.assertIn('unrelated_target_work',result['failed_checks'])
        self.assertIn(manual,e.comfy.barrier.read()['pending']);self.assertIsNone(e.comfy.proc.poll())
        self.assertEqual(len(e.a.snapshot()['owners']),2);self.assertFalse(e.a.maintenance_blocked())

    def test_recover_now_expired_execution_accepts_loss_without_replay(self):
        e=self.env(seconds=120)
        e.target.execution_timeout=2
        e.submit();e.active();e.runner.future('owned').result(10)
        job=e.store.job('owned')
        self.assertLess(job['execution_deadline'],time.time())
        self.assertFalse(e.control.artifacts(job)[0])
        checked=e.control.check('recover','human');self.assertTrue(checked['eligible'],checked)
        self.assertTrue(checked['loss_required']);self.assertEqual(checked['plan']['case'],'abandoned')
        with self.assertRaises(Refused):e.control.execute(checked['token'],'human',True)
        self.assertFalse(e.comfy.barrier.read()['signals'])
        result=e.control.execute(checked['token'],'human',True,True)
        self.assertEqual(result['result'],'released_readiness_pending',result)
        self.assertEqual(e.store.job('owned')['state'],'failed')
        self.assertEqual(e.store.job('owned')['error_code'],'operator_recovered_no_replay')
        self.assertEqual(e.a.snapshot()['owners'],{});self.assertFalse(e.a.maintenance_blocked())
        self.assertEqual(len(e.comfy.barrier.read()['prompts']),1)
        signal=e.comfy.barrier.read()['signals'][checked['operation']]
        self.assertTrue(signal['term_intent']);self.assertFalse(signal.get('kill_intent',False))

    def test_running_unrelated_manual_prompt_refuses_both_actions(self):
        e=self.completed()
        artifact=e.store.path('results/owned.bin').read_bytes()
        prompt=e.comfy.manual(120)
        wait(lambda:prompt in e.comfy.barrier.read()['running'].values())
        for mode in ('recover','emergency'):
            checked=e.control.check(mode,'human')
            self.assertFalse(checked['eligible'],checked)
            self.assertIn('unrelated_target_work',checked['failed_checks'])
        self.assertIn(prompt,e.comfy.barrier.read()['running'].values())
        self.assertFalse(e.comfy.barrier.read()['signals'])
        self.assertEqual(e.store.path('results/owned.bin').read_bytes(),artifact)
        self.assertEqual(len(e.a.snapshot()['owners']),2);self.assertFalse(e.a.maintenance_blocked())

    def completed(self,**kwargs):
        e=self.env(**kwargs)
        with patch.object(e.manager.lifecycle,'complete',side_effect=RecoveryRequired('qualification_interruption_before_cleanup')):
            e.submit();e.runner.future('owned').result(110)
        self.assertTrue(e.control.artifacts(e.store.job('owned'))[0],e.store.job('owned'))
        return e

    def test_recover_now_preserves_completed_output_with_health_churn(self):
        e=self.completed(stubborn='native');before=e.runner.store.path('results/owned.bin').read_bytes()
        checked=e.control.check('recover','human');self.assertTrue(checked['eligible'],checked)
        # Real Docker's Health metadata is outside the kernel execution anchor;
        # this CPU observer is forbidden after capture, including during finish.
        with patch.object(e.observer,'container',side_effect=AssertionError('unrelated observer unavailable')):
            result=e.control.execute(checked['token'],'human',True)
        self.assertEqual(result['result'],'released_readiness_pending',result)
        self.assertEqual(e.runner.artifact('owned')[0],before)
        self.assertEqual(e.store.job('owned')['state'],'success')
        self.assertFalse(e.a.maintenance_blocked())

    def test_remote_completed_output_is_saved_before_real_stop(self):
        e=self.env()
        original=e.store.write_bytes
        def fail_output(name,data):
            if name=='results/owned.bin':raise OSError('qualification unavailable output directory')
            return original(name,data)
        with patch.object(e.store,'write_bytes',side_effect=fail_output):
            e.submit();e.runner.future('owned').result(110)
        checked=e.control.check('recover','human');self.assertTrue(checked['eligible'],checked)
        self.assertEqual(checked['plan']['case'],'completed_remote')
        result=e.control.execute(checked['token'],'human',True)
        self.assertEqual(result['result'],'released_readiness_pending',result)
        self.assertTrue(e.runner.artifact('owned')[0].startswith(b'\x89PNG'))

    def test_unavailable_completed_output_is_not_reclassified_as_abandoned(self):
        e=self.completed(execution_timeout=2)
        job=e.store.job('owned');before=e.store.path('results/owned.bin').read_bytes()
        remote=e.root/'output'/job['wallpaper_lifecycle']['result']['value']['filename']
        held=remote.with_suffix('.held')
        # Disposable storage failure: the real history still reports success,
        # while its view request returns 404 and no validated local copy exists.
        remote.rename(held)
        for suffix in ('.bin','.json'):e.store.path('results/owned'+suffix).unlink()
        wait(lambda:time.time()>job['execution_deadline'],5)
        checked=e.control.check('recover','human')
        self.assertFalse(checked['eligible']);self.assertIn('completed_output_preservation_required',checked['failed_checks'])
        emergency=e.control.check('emergency','human')
        self.assertTrue(emergency['eligible'],emergency)
        self.assertIn('Known completed output is not safely saved',emergency['effect'])
        self.assertTrue(emergency['loss_required']);self.assertFalse(e.comfy.barrier.read()['signals'])
        held.rename(remote)
        checked=e.control.check('recover','human');self.assertTrue(checked['eligible'],checked)
        result=e.control.execute(checked['token'],'human',True)
        self.assertEqual(result['result'],'released_readiness_pending',result)
        self.assertEqual(e.runner.artifact('owned')[0],before)

    def test_native_finalization_interruption_verifies_without_repeating_signal(self):
        e=self.completed();checked=e.control.check('recover','human');self.assertTrue(checked['eligible'],checked)
        original=e.store.put_job
        def fail_terminal(job):
            if job.get('native_settlement'):raise OSError('qualification interrupted terminal write')
            return original(job)
        with patch.object(e.store,'put_job',side_effect=fail_terminal):
            result=e.control.execute(checked['token'],'human',True)
        self.assertEqual(result['result'],'operator_required',result)
        self.assertEqual(e.a.snapshot()['owners'],{})
        before=copy.deepcopy(e.comfy.barrier.read()['signals'])
        result=e.control.verify_interrupted(checked['operation'])
        self.assertEqual(result['result'],'released_readiness_pending',result)
        self.assertEqual(e.comfy.barrier.read()['signals'],before)
        self.assertFalse(e.a.maintenance_blocked())

    def test_missing_anchor_and_stale_confirmation_refuse_without_effect(self):
        e=self.completed();checked=e.control.check('recover','human');self.assertTrue(checked['eligible'],checked)
        job=e.store.job('owned');job.pop('execution_anchor');e.store.put_job(job)
        result=e.control.check('emergency','human')
        self.assertFalse(result['eligible']);self.assertIn('recovery_capture_requires_inactive_ready_without_submission',result['failed_checks'])
        with self.assertRaises(Refused):e.control.execute(checked['token'],'human',True)
        self.assertIsNone(e.comfy.proc.poll());self.assertFalse(e.a.maintenance_blocked())

    def test_partial_term_failure_then_confirmed_emergency_override(self):
        e=self.completed(stubborn=True,allow_force=False);normal=e.control.check('recover','human')
        result=e.control.execute(normal['token'],'human',True)
        self.assertEqual(result['result'],'operator_required',result)
        signals=copy.deepcopy(e.comfy.barrier.read()['signals'])
        self.assertFalse(signals[normal['operation']].get('kill_intent'))
        with self.assertRaises(Refused):e.control.verify_interrupted(normal['operation'])
        self.assertEqual(e.comfy.barrier.read()['signals'],signals)
        emergency=e.control.check('emergency','human');self.assertTrue(emergency['eligible'],emergency)
        result=e.control.execute(emergency['token'],'human',True,True,expected_mode='emergency')
        self.assertEqual(result['result'],'released_readiness_pending',result)
        self.assertEqual(e.store.job('owned')['state'],'success')

    def test_process_death_after_native_commit_reopens_and_verifies_receipt(self):
        e=self.completed();checked=e.control.check('recover','human');self.assertTrue(checked['eligible'],checked)
        e.runner.close()  # Stop disposable threads before fork; backend remains.
        child=os.fork()
        if child==0:
            original=e.store.put_job
            def power_loss(job):
                if job.get('native_settlement'):os._exit(73)
                return original(job)
            e.store.put_job=power_loss
            try:e.control.execute(checked['token'],'human',True)
            except BaseException:os._exit(74)
            os._exit(75)
        _,code=os.waitpid(child,0);self.assertEqual(os.waitstatus_to_exitcode(code),73)
        self.assertEqual(e.a.snapshot()['owners'],{})
        self.assertFalse(e.a.snapshot()['ready'])
        # The separately represented installed client SDK predates practical_control. Its
        # native ready bit still excludes acquisition during interrupted finish.
        import importlib.util
        spec=importlib.util.spec_from_file_location('aihub_gpu_runner.legacy_qualification_core',
            ROOT/'components/client-sdk/aihub_gpu_runner/core.py')
        legacy=importlib.util.module_from_spec(spec);spec.loader.exec_module(legacy)
        if os.environ.get('AIHUB_QUALIFY_GENERATIONS')=='1':
            # Coordinated upgrade excludes obsolete native writers. An old SDK
            # cannot consume schema2; it is not a supported mixed-version mode.
            with self.assertRaises(legacy.RecoveryRequired):
                old=legacy.Admission(legacy.AtomicStore(e.store.root),e.a.lock_paths,e.a.scope,require_existing_locks=True)
                old.try_acquire('unrelated','d'*64,('gpu0','gpu1'),'wallpaper')
        else:
            old=legacy.Admission(legacy.AtomicStore(e.store.root),e.a.lock_paths,e.a.scope,require_existing_locks=True)
            self.assertIsNone(old.try_acquire('unrelated','d'*64,('gpu0','gpu1'),'wallpaper'))
        signals=copy.deepcopy(e.comfy.barrier.read()['signals'])
        e.reopen()
        self.assertEqual(e.store.job('owned')['state'],'success')
        result=e.control.verify_interrupted(checked['operation'])
        self.assertEqual(result['result'],'released_readiness_pending',result)
        self.assertEqual(e.comfy.barrier.read()['signals'],signals)
        self.assertTrue(e.a.snapshot()['ready'])
        with self.assertRaises(Refused):e.control.execute(checked['token'],'human',True)

    def test_original_exit_after_partial_stop_restores_existing_target_fence(self):
        e=self.completed(stubborn=True,allow_force=False);normal=e.control.check('recover','human')
        self.assertEqual(e.control.execute(normal['token'],'human',True)['result'],'operator_required')
        # Simulate the owned process finally exiting independently, then verify
        # the existing operation; no new signal or blanket unfencing is sent.
        import signal
        os.kill(e.store.job('owned')['execution_anchor']['pid'],signal.SIGKILL)
        wait(lambda:not e.control.adapter.original_container(e.store.job('owned'),time.monotonic()+1)['running'])
        signals=copy.deepcopy(e.comfy.barrier.read()['signals'])
        result=e.control.verify_interrupted(normal['operation'])
        self.assertEqual(result['result'],'released_readiness_pending',result)
        self.assertEqual(e.comfy.barrier.read()['mode'],'open')
        self.assertEqual(e.comfy.barrier.read()['signals'],signals)

if __name__=='__main__':unittest.main(verbosity=2)
