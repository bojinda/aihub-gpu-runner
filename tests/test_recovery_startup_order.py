"""Real RecoveryManager.startup with private journals; only external tick is stubbed."""
import copy
import hashlib
import unittest
from unittest.mock import Mock, patch

from aihub_gpu_runner.core import Runner, RecoveryRequired, lock_identity, encode
from aihub_gpu_runner.recovery import RecoveryManager
from tests.test_music_release import Fixture


class RecoveryStartupOrderTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture(self)
        self.f.memory = lambda:454  # One execution/free, then bounded cleanup failure.
        self.execute = Mock(wraps=self.f.backend.execute)
        self.f.backend.execute = self.execute
        self.f.run()
        self.assertEqual(self.execute.call_count,1)
        self.assertEqual(self.f.store.job('music-job')['state'],'cleanup_pending')
        self.evidence = dict({k:self.f.lease.owner[k] for k in
                            ('job_id','request_hash','lease_id','target')},
                            approval_ref='OFFLINE-STARTUP-ORDER',
                            operator_approved_backend_recovery=True,
                            backend_quiescent=True,cleanup_verified=True)
        self.locks = {r:lock_identity(p.stat()) for r,p in self.f.a.lock_paths.items()}

    def commit_then_crash(self):
        # This is the precise crash edge: do NOT call CLI/job finalization.
        self.f.a.recover('music-job',self.evidence)
        state=self.f.a.snapshot()
        self.assertEqual(len(state['recoveries']),1)
        self.assertFalse(state['owners'])
        self.assertEqual(self.f.store.job('music-job')['state'],'cleanup_pending')
        return self.f.store.path('ownership.json').read_bytes()

    def start(self):
        # Construct a fresh real manager. startup and Wallpaper finalize_released
        # stay real. External polling/control is the only startup stub.
        manager=RecoveryManager(self.f.a,self.f.targets,self.f.backends,observer=self.f.obs)
        manager.tick=Mock()
        manager.start=Mock()
        manager.close=Mock()
        manager.startup=Mock(wraps=manager.startup)
        manager.lifecycle.finalize_released=Mock(wraps=manager.lifecycle.finalize_released)
        self.last_manager=manager
        runner=Runner(self.f.a,self.f.targets,self.f.backends,recovery=manager,poll_interval=.005)
        self.addCleanup(runner.close)
        return runner,manager

    def assert_preserved(self,ownership):
        self.assertEqual(self.f.store.path('ownership.json').read_bytes(),ownership)
        self.assertEqual({r:lock_identity(p.stat()) for r,p in self.f.a.lock_paths.items()},self.locks)
        self.assertEqual(len(self.f.a.snapshot()['recoveries']),1)
        self.assertFalse(self.f.a.snapshot()['owners'])
        self.assertEqual(self.execute.call_count,1)
        self.assertEqual(self.f.free,1)

    def test_actual_startup_finalizes_audit_clear_crash_before_generic_rewrite(self):
        ownership=self.commit_then_crash()
        runner,manager=self.start()
        manager.startup.assert_called_once_with()
        manager.lifecycle.finalize_released.assert_called_once_with()
        manager.tick.assert_called_once_with(force=True)
        job=self.f.store.job('music-job')
        self.assertEqual(job['state'],'failed')
        self.assertEqual(job['error_code'],'operator_recovered_no_automatic_resubmission')
        self.assertEqual(runner.result('music-job')['state'],'failed')
        self.assertIsNone(runner.result('music-job')['value'])
        self.assert_preserved(ownership)
        first=self.f.store.path('jobs/music-job.json').read_bytes()
        runner.close()
        second,second_manager=self.start()
        self.assertEqual(self.f.store.path('jobs/music-job.json').read_bytes(),first)
        self.assertEqual(second.result('music-job')['error_code'],'operator_recovered_no_automatic_resubmission')
        second_manager.lifecycle.finalize_released.assert_called_once_with()
        self.assert_preserved(ownership)

    def test_wrong_audit_job_owner_and_identity_fail_before_real_startup(self):
        self.commit_then_crash()
        saved=self.f.a.snapshot();original=self.f.store.job('music-job')
        for mode in ('wrong_audit','changed_job','owner_retained','wrong_boot','wrong_lease','wrong_request','wrong_target'):
            with self.subTest(mode=mode):
                state=copy.deepcopy(saved);job=copy.deepcopy(original)
                record=state['recovery_dispositions'][self.f.lease.owner['lease_id']]
                if mode=='wrong_audit':state['recoveries'][0]['approval_ref']='wrong'
                elif mode=='changed_job':job['phase']='unexpected mutation'
                elif mode=='owner_retained':state['owners']['gpu0']=dict(self.f.lease.owner)
                else:
                    key={'wrong_boot':'boot_id','wrong_lease':'lease_id','wrong_request':'request_hash','wrong_target':'target'}[mode]
                    record['owner'][key]='wrong'
                self.f.store.write('ownership.json',state);self.f.store.put_job(job)
                before=self.f.store.path('ownership.json').read_bytes();job_before=self.f.store.path('jobs/music-job.json').read_bytes()
                with self.assertRaises(RecoveryRequired):self.start()
                self.last_manager.startup.assert_not_called()
                self.assertEqual(self.f.store.path('ownership.json').read_bytes(),before)
                self.assertEqual(self.f.store.path('jobs/music-job.json').read_bytes(),job_before)
                self.assertEqual({r:lock_identity(p.stat()) for r,p in self.f.a.lock_paths.items()},self.locks)
                self.assertEqual(self.execute.call_count,1);self.assertEqual(self.f.free,1)

    def test_failed_finalization_persistence_is_retryable_on_real_startup(self):
        ownership=self.commit_then_crash()
        put=self.f.store.put_job
        for after in (False,True):
            with self.subTest(after_effect=after):
                # Restore only disposable fixture job bytes to the original edge.
                record=self.f.a.snapshot()['recovery_dispositions'][self.f.lease.owner['lease_id']]
                original=copy.deepcopy(self.f.store.job('music-job'))
                if after:
                    # Separate case uses a fresh original job saved before the first startup.
                    original=copy.deepcopy(self.original_job)
                    put(original)
                else:self.original_job=copy.deepcopy(original)
                self.assertEqual(hashlib.sha256(encode(original)).hexdigest(),record['job_sha256'])
                def fail(value):
                    if value.get('error_code')=='operator_recovered_no_automatic_resubmission':
                        if after:put(value)
                        raise OSError('offline finalizer persistence failure')
                    return put(value)
                with patch.object(self.f.store,'put_job',side_effect=fail),self.assertRaises(OSError):self.start()
                self.last_manager.startup.assert_not_called()
                runner,manager=self.start()
                self.assertEqual(runner.result('music-job')['state'],'failed')
                manager.tick.assert_called_once_with(force=True)
                self.assert_preserved(ownership)
                runner.close()


class WallpaperStartupFinalizationTests(unittest.TestCase):
    def test_real_startup_still_finalizes_committed_paired_wallpaper_receipt(self):
        from tests import test_wallpaper_lifecycle as wallpaper
        from tests.test_automatic_recovery import Crash
        case=wallpaper.LifecycleTests('runTest');case.setUp()
        self.addCleanup(case.doCleanups);self.addCleanup(case.tearDown)
        first=case.runner()
        with patch.object(case.manager.lifecycle,'finalize_released',side_effect=Crash('after paired receipt')):
            with self.assertRaises(Crash):case.submit(first)
        first.close()
        state=case.gate.snapshot();self.assertFalse(state['owners'])
        self.assertEqual(len(state['wallpaper_lifecycle_receipts']),1)
        self.assertEqual(case.store.job('job')['state'],'cleanup_pending')
        ownership=case.store.path('ownership.json').read_bytes();actions=list(case.observer.actions)
        locks={r:lock_identity(p.stat()) for r,p in case.gate.lock_paths.items()}
        manager=case.new_manager()
        with patch.object(manager,'tick') as tick,patch.object(manager,'start'):
            second=Runner(case.gate,case.targets,case.backends,recovery=manager,poll_interval=.005)
        case.runners.append(second)
        tick.assert_called_once_with(force=True)
        self.assertEqual(second.result('job')['state'],'success')
        self.assertEqual(case.store.job('job')['wallpaper_lifecycle']['phase'],'released')
        self.assertEqual(case.store.path('ownership.json').read_bytes(),ownership)
        self.assertEqual(case.observer.actions,actions)
        self.assertEqual(case.transport.submissions,1)
        self.assertEqual({r:lock_identity(p.stat()) for r,p in case.gate.lock_paths.items()},locks)


if __name__=='__main__':unittest.main(verbosity=2)
