"""Synthetic GPU observations; actual worker, durable store, leases and locks."""
import unittest
from unittest.mock import patch

from aihub_gpu_runner.core import Conflict, NativeLock
from tests import test_cleanup_budget as budget

GPU1 = budget.GPU1


class IdleReleaseTests(unittest.TestCase):
    fixture = budget.WorkerBudgetTests.fixture
    run_worker = budget.WorkerBudgetTests.run_worker

    def small(self):
        return self.fixture(seconds=.1, history=20, authority_bytes=30_000)

    def assert_retained(self, f, reason):
        job = self.run_worker(f)
        self.assertEqual(job['error_code'], reason)
        self.assertEqual(f.a.snapshot()['owners']['gpu1']['lease_id'], f.lease.owner['lease_id'])
        with self.assertRaises(Conflict):
            f.runner.result('owned-job')
        return job

    def test_unknown_compute_then_disappears_requires_fresh_idle_and_one_release(self):
        f = self.small()
        owners = []
        def apps():
            owners.append(f.a.snapshot()['owners']['gpu1']['lease_id'])
            self.assertIsNone(f.a.try_acquire('competitor', 'hash', ('gpu1',), 'ollama'))
            self.assertFalse(NativeLock(f.a.lock_paths['gpu1'], create=False).acquire(0))
            # A different diagnostic child is no evidence about PID 999. Its
            # noncompute lifetime need not end before physical idle can count.
            f.ollama_pids['305'] = f.births['305'] = 'unrelated-probe-birth'
            return GPU1+',999,256\n' if f.samples == 1 else ''
        f.apps = apps
        f.memory = lambda: 256 if f.samples == 1 else 1
        with patch.object(f.lease, 'release', wraps=f.lease.release) as release:
            job = self.run_worker(f)
        release.assert_called_once()
        self.assertEqual(job['state'], 'success', job.get('error_code'))
        self.assertEqual(set(owners), {f.lease.owner['lease_id']})
        self.assertEqual(job['cleanup_proof']['physical_ollama_idle']['stable_samples'], 3)
        self.assertEqual(f.samples, 7)  # invalid + 3 consecutive + 3 final checks
        self.assertEqual(f.a.snapshot()['owners'], {})
        self.assertEqual(f.publications, 1)
        self.assertEqual(f.runner.result('owned-job')['value']['response'], 'synthetic')

    def test_unknown_compute_at_each_final_boundary_requires_three_new_idle_samples(self):
        for boundary in ('prepublication', 'postpublication', 'prerelease'):
            with self.subTest(boundary=boundary):
                f = self.small(); seen = []
                def apps():
                    if f.boundaries[-1] == boundary and not seen:
                        seen.append(True)
                        return GPU1+',999,0\n'
                    return ''
                f.apps = apps
                job = self.run_worker(f)
                self.assertEqual(job['state'], 'success', job.get('error_code'))
                self.assertEqual(seen, [True]); self.assertEqual(f.samples, 9)
                self.assertEqual(f.publications, 1); self.assertEqual(f.a.snapshot()['owners'], {})

    def test_contradictory_gpu_health_is_not_hidden_by_unknown_compute(self):
        f = self.small(); f.apps = lambda: GPU1+',999,0\n'
        f.gpu_override = GPU1+',1,0,30,1,No'
        self.assert_retained(f, 'ollama_cleanup_gpu_health_unverified')

    def test_persistent_unknown_compute_retains_the_original_lease(self):
        f = self.small(); f.target.cleanup_timeout = 2
        f.apps = lambda: GPU1+',999,0\n'
        self.assert_retained(f, 'ollama_cleanup_process_settling_pending')
        self.assertEqual(f.publications, 0)

    def test_known_foreign_compute_is_a_refusal_even_if_it_would_disappear(self):
        f = self.small(); f.apps = lambda: GPU1+',202,0\n' if f.samples == 1 else ''
        self.assert_retained(f, 'ollama_cleanup_gpu_activity_unexplained')
        self.assertEqual(f.samples, 1)

    def test_competing_model_is_a_refusal_even_with_an_empty_gpu(self):
        f = self.small()
        original = f.observer.queues
        def queues(*a, **kw):
            f.models = [{'name': 'synthetic-foreign'}]
            return original(*a, **kw)
        f.observer.queues = queues
        self.assert_retained(f, 'unexplained_backend_activity_ollama')

    def test_duplicate_compute_rows_remain_a_refusal(self):
        f = self.small(); f.apps = lambda: 2*(GPU1+',999,0\n')
        self.assert_retained(f, 'ollama_cleanup_unknown_or_duplicate_gpu_process')

    def test_reused_captured_pid_remains_a_refusal(self):
        f = self.small()
        original = f.observer.processes.side_effect
        def processes(*a, **kw):
            result = original(*a, **kw)
            if f.samples and '304' in result: result['304'] = 'reused'
            return result
        f.observer.processes.side_effect = processes
        self.assert_retained(f, 'ollama_cleanup_new_or_reused_process')

    def test_container_replacement_remains_a_refusal(self):
        f = self.small(); original = f.observer.queues
        def queues(*a, **kw):
            f.metas['ollama']['State']['StartedAt'] = 'replaced'
            return original(*a, **kw)
        f.observer.queues = queues
        self.assert_retained(f, 'ollama_cleanup_generation_changed')


if __name__ == '__main__': unittest.main()
