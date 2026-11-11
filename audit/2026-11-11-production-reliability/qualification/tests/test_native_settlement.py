import copy
import unittest
from unittest.mock import patch

from aihub_gpu_runner.core import NativeLock
from aihub_gpu_runner.recovery_summary import read_recovery_summary
from tests.recovery_fixture import Fixture
from tests import test_wallpaper_lifecycle as lifecycle_fixture


class NativeSettlementTests(unittest.TestCase):
    def test_atomic_release_and_receipt_without_backend_or_maintenance(self):
        f=Fixture(self);before=copy.deepcopy(f.a.snapshot());job=copy.deepcopy(f.job)
        self.assertTrue(f.a.retire_prepared_wallpaper(job['request_id']))
        s=f.a.snapshot();out=f.store.job(job['request_id'])
        self.assertEqual(s['owners'],{});self.assertEqual(s['recoveries'][:-1],before['recoveries'])
        self.assertEqual(out['state'],'failed');self.assertEqual(out['wallpaper_lifecycle'],job['wallpaper_lifecycle'])
        self.assertFalse(f.a.maintenance_blocked());f.calls.assert_not_called()
        self.assertFalse(read_recovery_summary(f.store)['operator_required'])
        self.assertTrue(f.a.retire_prepared_wallpaper(job['request_id']))
        self.assertEqual(s,f.a.snapshot())

    def test_active_original_lock_prevents_retirement(self):
        f=Fixture(self)
        with NativeLock(f.a.lock_paths['gpu1'],create=False).held():
            self.assertFalse(f.a.retire_prepared_wallpaper(f.job['request_id']))
        self.assertEqual(len(f.a.snapshot()['owners']),2)

    def test_start_or_submission_intent_never_retires(self):
        for field in ('start_intent','submission_intent'):
            f=Fixture(self);f.job['wallpaper_lifecycle'][field]=False;f.store.put_job(f.job)
            self.assertFalse(f.a.retire_prepared_wallpaper(f.job['request_id']))

    def test_interrupted_finalization_rolls_forward_receipt_no_replay(self):
        f=Fixture(self)
        with patch.object(f.store,'put_job',side_effect=OSError('interrupted job write')):
            with self.assertRaises(OSError):f.a.retire_prepared_wallpaper(f.job['request_id'])
        self.assertEqual(f.a.snapshot()['owners'],{})
        self.assertFalse(f.a.maintenance_blocked())
        f.a.finalize_native_settlements()
        self.assertEqual(f.store.job(f.job['request_id'])['state'],'failed')
        self.assertEqual(len(f.a.snapshot()['recoveries']),2)


class NativeLifecycleIntegrationTests(unittest.TestCase):
    setUp=lifecycle_fixture.LifecycleTests.setUp
    tearDown=lifecycle_fixture.LifecycleTests.tearDown
    new_manager=lifecycle_fixture.LifecycleTests.new_manager
    runner=lifecycle_fixture.LifecycleTests.runner
    submit=lifecycle_fixture.LifecycleTests.submit
    tick=lifecycle_fixture.LifecycleTests.tick
    # Reuse fixture setup without duplicating predecessor test discovery.
    def test_prestart_readiness_failure_enters_native_automatic_authority(self):
        self.gate.automatic_no_start_enabled=True
        runner=self.runner()
        with patch.object(self.observer,'stopped_ready',side_effect=RuntimeError('music 454 MiB > 450')):
            self.submit(runner,'prepared-resource-failure')
        actions=list(self.observer.actions)
        with patch.object(self.observer,'container',side_effect=AssertionError('no backend observation')):
            self.tick()
        self.assertEqual(self.gate.snapshot()['owners'],{})
        self.assertEqual(self.store.job('prepared-resource-failure')['state'],'failed')
        self.assertEqual(actions,[])
        self.assertFalse(self.gate.maintenance_blocked())
