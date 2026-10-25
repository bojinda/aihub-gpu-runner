"""Offline recovery budget exhaustion and fail-closed safety regressions."""
import time
import unittest
from unittest.mock import patch

from aihub_gpu_runner.backends import Uncertain, remaining
from aihub_gpu_runner.recovery import RecoveryObserver
from tests import test_wallpaper_lifecycle as fixtures


class RecoveryTimeoutBudgetTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.LifecycleTests('run')
        self.f.setUp()
        self.f.transport.history = 'missing'
        self.f.submit(self.f.runner())
        self.f.expire()

    def tearDown(self):
        try:
            self.f.tearDown()
        finally:
            self.f.doCleanups()

    def slow_history(self, prompt, deadline):
        time.sleep(remaining(deadline) + .01)
        raise Uncertain('history_socket_timeout')

    def assert_retained(self):
        self.f.held()
        self.assertTrue(self.f.observer.current['running'])
        self.assertEqual(self.f.observer.actions, ['start'])
        self.assertEqual(self.f.transport.submissions, 1)
        self.assertEqual(self.f.gate.snapshot().get('recovery_budget', []), [])

    def test_both_http_timeouts_leave_one_original_deadline_for_cleanup(self):
        f = self.f
        observer = RecoveryObserver(f.p, f.targets, f.backends)
        deadlines = []
        original_json, original_stop = f.transport.json, f.observer.signal
        original_cleanup = f.observer.stopped_ready

        def slow_queue(method, route, payload=None, timeout=5):
            if route == '/queue':
                time.sleep(timeout + .01)
                raise Uncertain('queue_socket_timeout')
            return original_json(method, route, payload, timeout)

        def queues(resources, deadline, **kwargs):
            deadlines.append(deadline)
            return observer.queues(resources, deadline, **kwargs)

        def stop(identity, signal, deadline):
            self.assertEqual(deadline, deadlines[0])
            self.assertGreater(remaining(deadline), 0)
            return original_stop(identity, signal, deadline)

        def cleanup(resources, deadline, processes=None):
            self.assertEqual(deadline, deadlines[0])
            remaining(deadline)
            return original_cleanup(resources, deadline, processes)

        with patch.object(f.backend, 'reconcile', side_effect=self.slow_history), \
             patch.object(f.transport, 'json', side_effect=slow_queue), \
             patch.object(f.observer, 'queues', side_effect=queues), \
             patch.object(f.observer, 'signal', side_effect=stop), \
             patch.object(f.observer, 'stopped_ready', side_effect=cleanup):
            f.tick()
        f.tick(3)
        self.assertEqual(f.observer.actions, ['start', 'TERM'])
        self.assertEqual(f.gate.snapshot()['owners'], {})
        self.assertEqual(f.store.job('job')['state'], 'failed')
        self.assertEqual(f.transport.submissions, 1)
        self.assertEqual(len(f.gate.snapshot()['recoveries']), 1)
        self.assertEqual(len(f.gate.snapshot()['recovery_budget']), 1)

    def test_overall_cleanup_deadline_exhaustion_retains_both_owners(self):
        f = self.f

        def exhausted_cleanup(resources, deadline, processes=None):
            time.sleep(remaining(deadline) + .01)
            remaining(deadline)

        with patch.object(f.backend, 'reconcile', side_effect=self.slow_history), \
             patch.object(f.observer, 'stopped_ready', side_effect=exhausted_cleanup):
            f.tick()
        f.tick(2)
        f.held()
        self.assertFalse(f.observer.current['running'])
        self.assertEqual(f.observer.actions, ['start', 'TERM'])
        self.assertEqual(f.store.job('job')['recovery_error'], 'backend_deadline')
        self.assertEqual(f.transport.submissions, 1)
        self.assertEqual(f.gate.snapshot().get('recoveries', []), [])

    def test_http_timeout_before_actual_execution_expiry_cannot_stop(self):
        f = self.f
        f.manager.update_job('job', execution_deadline=time.time() + 30)
        with patch.object(f.backend, 'reconcile', side_effect=self.slow_history):
            f.tick()
        self.assert_retained()
        self.assertNotEqual(f.store.job('job').get('recovery_status'), 'operator_required')

    def test_foreign_queue_still_blocks_after_history_timeout(self):
        f = self.f
        observer = RecoveryObserver(f.p, f.targets, f.backends)
        original_json = f.transport.json

        def foreign_queue(method, route, payload=None, timeout=5):
            if route == '/queue':
                return {'queue_running': [], 'queue_pending': [[1, 'foreign-prompt']]}
            return original_json(method, route, payload, timeout)

        with patch.object(f.backend, 'reconcile', side_effect=self.slow_history), \
             patch.object(f.transport, 'json', side_effect=foreign_queue), \
             patch.object(f.observer, 'queues', side_effect=observer.queues):
            f.tick()
        self.assert_retained()
        self.assertEqual(f.store.job('job')['recovery_error'], 'unexplained_backend_activity_wallpaper')

    def test_identity_drift_during_history_timeout_blocks_teardown(self):
        def drift(prompt, deadline):
            self.f.observer.current['id'] = 'e' * 64
            self.slow_history(prompt, deadline)

        with patch.object(self.f.backend, 'reconcile', side_effect=drift):
            self.f.tick()
        self.assert_retained()
        self.assertEqual(self.f.store.job('job')['recovery_error'],
                         'wallpaper_generation_changed_before_shutdown')

    def test_unhealthy_gpu_still_blocks_after_history_timeout(self):
        self.f.observer.fail = 'health'
        with patch.object(self.f.backend, 'reconcile', side_effect=self.slow_history):
            self.f.tick()
        self.assert_retained()
        self.assertEqual(self.f.store.job('job')['recovery_error'],
                         'physical_gpu_identity_unverified_health')


if __name__ == '__main__':
    unittest.main()
