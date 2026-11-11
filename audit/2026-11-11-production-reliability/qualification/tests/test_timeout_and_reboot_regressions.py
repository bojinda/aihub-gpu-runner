"""Independent offline regressions for the supplied on-demand candidate.

Run with the patched runner repository on PYTHONPATH. All Docker, GPU and
backend observations are synthetic; the repository's private native-lock
fixtures are reused. No production services or network endpoints are used.
"""
import time
import unittest
from unittest.mock import patch

from aihub_gpu_runner.backends import Uncertain, remaining
from tests import test_wallpaper_lifecycle as lifecycle_fixtures
from aihub_gpu_runner.recovery import RecoveryObserver
from tests import test_automatic_recovery as recovery_fixtures


class TimeoutAndRebootRegressions(unittest.TestCase):
    def setUp(self):
        self.f = lifecycle_fixtures.LifecycleTests('run')
        self.f.setUp()

    def tearDown(self):
        try:
            self.f.tearDown()
        finally:
            self.f.doCleanups()

    def test_history_timeout_must_leave_time_for_owned_teardown(self):
        f = self.f
        f.transport.history = 'missing'
        runner = f.runner()
        f.submit(runner)
        f.expire()

        # Real command observations refuse calls after the supplied deadline.
        # Add this behaviour to the otherwise instant synthetic observer.
        original_container = f.observer.container
        def deadline_checked_container(service, deadline):
            remaining(deadline)
            return original_container(service, deadline)

        # A wedged HTTP endpoint can consume its full caller-supplied timeout.
        # A separate capped history deadline should leave a cleanup budget.
        def slow_history(prompt, deadline):
            time.sleep(remaining(deadline) + .01)
            raise Uncertain('simulated_history_socket_timeout')

        with patch.object(f.observer, 'container', side_effect=deadline_checked_container), \
             patch.object(f.backend, 'reconcile', side_effect=slow_history):
            f.tick()

        detail = {
            'controls': f.observer.actions,
            'recovery_error': f.store.job('job').get('recovery_error'),
            'held_resources': sorted(f.gate.snapshot()['owners']),
        }
        self.assertFalse(f.observer.current['running'], detail)
        self.assertEqual(f.gate.snapshot()['owners'], {}, detail)
        self.assertEqual(f.store.job('job')['state'], 'failed', detail)
        self.assertEqual(f.transport.submissions, 1)

    def test_queue_timeout_must_leave_time_for_owned_teardown(self):
        f = self.f
        f.transport.history = 'missing'
        runner = f.runner()
        f.submit(runner)
        f.expire()

        # Use the production queue observer with a simulated socket timeout.
        queue_observer = RecoveryObserver(f.p, f.targets, f.backends)
        original_json = f.transport.json
        original_sample = f.observer.gpu_sample
        def slow_queue(method, route, payload=None, timeout=5):
            if route == '/queue':
                time.sleep(timeout + .01)
                raise Uncertain('simulated_queue_socket_timeout')
            return original_json(method, route, payload, timeout)
        def deadline_checked_sample(resources, deadline, executing=False):
            remaining(deadline)
            return original_sample(resources, deadline, executing=executing)

        with patch.object(f.transport, 'json', side_effect=slow_queue), \
             patch.object(f.observer, 'queues', side_effect=queue_observer.queues), \
             patch.object(f.observer, 'gpu_sample', side_effect=deadline_checked_sample):
            f.tick()

        detail = {
            'controls': f.observer.actions,
            'recovery_error': f.store.job('job').get('recovery_error'),
            'held_resources': sorted(f.gate.snapshot()['owners']),
        }
        self.assertFalse(f.observer.current['running'], detail)
        self.assertEqual(f.gate.snapshot()['owners'], {}, detail)
        self.assertEqual(f.store.job('job')['state'], 'failed', detail)
        self.assertEqual(f.transport.submissions, 1)

    def test_mid_generation_reboot_clears_stale_work_and_next_new_job_runs(self):
        f = self.f
        f.transport.history = 'missing'
        old_runner = f.runner()
        f.submit(old_runner, 'interrupted')
        self.assertTrue(f.observer.current['running'])
        f.store.put_job({
            'request_id': 'old-waiter', 'request_hash': 'queued-hash',
            'target': 'wallpaper', 'resources': ['gpu0', 'gpu1'],
            'state': 'waiting', 'boot_id': recovery_fixtures.BOOT1,
            'error_code': None,
        })
        old_runner.close()

        # Simulate the new VM boot and Docker restart=no: the old container
        # process is gone; the same container object remains stopped.
        f.boot[0] = recovery_fixtures.BOOT2
        f.observer.current.update(running=False, pid=0)
        actions_before = list(f.observer.actions)
        f.manager = f.new_manager()
        restarted = f.runner()

        self.assertEqual(f.observer.actions, actions_before)
        self.assertFalse(f.observer.current['running'])
        self.assertEqual(f.gate.snapshot()['owners'], {})
        self.assertEqual(f.gate.snapshot()['verified_boot'], recovery_fixtures.BOOT2)
        self.assertEqual(f.store.job('interrupted')['error_code'], 'guest_reboot_interrupted')
        self.assertEqual(f.store.job('old-waiter')['state'], 'failed')
        self.assertEqual(f.transport.submissions, 1)

        f.transport.history = 'success'
        self.assertEqual(f.submit(restarted, 'new-job')['state'], 'success')
        self.assertEqual(f.transport.submissions, 2)
        self.assertFalse(f.observer.current['running'])
        self.assertEqual(f.gate.snapshot()['owners'], {})
        self.assertEqual(f.summary()['incidents_7d'], 1)
        self.assertFalse(f.summary()['operator_required'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
