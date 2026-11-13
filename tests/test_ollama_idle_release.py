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

    def test_unknown_compute_then_disappears_requires_fresh_idle_and_one_release(self):
        # Preserve the accepted strict-idle reproduction without opting into
        # acknowledged-expiration handoff. A different probe cannot identify 999.
        from aihub_gpu_runner.ollama_cleanup import OllamaCleanupGuard
        f = self.small()
        f.apps = lambda: GPU1+',999,256\n' if f.samples == 1 else ''
        with patch.object(OllamaCleanupGuard, 'acknowledge_expiration', return_value=None, create=True):
            job = self.run_worker(f)
        self.assertTrue(job['cleanup_verified'])
        self.assertEqual(job['cleanup_proof']['physical_ollama_idle']['stable_samples'], 3)
        self.assertEqual(f.samples, 7)
        self.assertEqual(f.a.snapshot()['owners'], {})

    def assert_retained(self, f, reason):
        job = self.run_worker(f)
        self.assertEqual(job['error_code'], reason)
        self.assertEqual(f.a.snapshot()['owners']['gpu1']['lease_id'], f.lease.owner['lease_id'])
        with self.assertRaises(Conflict):
            f.runner.result('owned-job')
        return job

    def test_unknown_compute_then_disappears_releases_once_without_claiming_origin_or_idle(self):
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
        self.assertFalse(job['cleanup_verified'])
        self.assertNotIn('physical_ollama_idle', job['cleanup_proof'])
        self.assertEqual(f.samples, 4)  # busy observation + three final checks
        self.assertEqual(f.a.snapshot()['owners'], {})
        self.assertEqual(f.publications, 1)
        self.assertEqual(f.runner.result('owned-job')['value']['response'], 'synthetic')

    def test_unknown_compute_at_each_final_boundary_downgrades_idle_claim(self):
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
                self.assertEqual(seen, [True]); self.assertEqual(f.samples, 6)
                self.assertEqual(f.publications, 1); self.assertEqual(f.a.snapshot()['owners'], {})

    def test_contradictory_gpu_health_is_not_hidden_by_unknown_compute(self):
        f = self.small(); f.apps = lambda: GPU1+',999,0\n'
        f.gpu_override = GPU1+',1,0,30,1,No'
        self.assert_retained(f, 'ollama_cleanup_gpu_health_unverified')

    def test_persistent_compute_relinquishes_without_claiming_idle(self):
        f = self.small()
        f.apps = lambda: GPU1+',999,0\n'
        job = self.run_worker(f)
        self.assertEqual(job['state'], 'success')
        self.assertFalse(job['cleanup_verified'])
        self.assertEqual(job['cleanup_proof']['release_outcome'], 'completed_without_idle')
        self.assertNotIn('physical_ollama_idle', job['cleanup_proof'])
        self.assertEqual(f.a.snapshot()['owners'], {})
        self.assertEqual(f.runner.result('owned-job')['value']['response'], 'synthetic')
        self.assertEqual(f.samples, 4)

    def test_known_foreign_compute_does_not_become_trusted_work(self):
        f = self.small(); f.apps = lambda: GPU1+',202,0\n' if f.samples == 1 else ''
        job = self.run_worker(f)
        self.assertFalse(job['cleanup_verified'])
        self.assertEqual(f.samples, 4)

    def test_competing_model_cannot_strand_completed_work(self):
        f = self.small()
        original = f.observer.queues
        def queues(*a, **kw):
            f.models = [{'name': 'synthetic-foreign'}]
            return original(*a, **kw)
        f.observer.queues = queues
        job = self.run_worker(f)
        self.assertFalse(job['cleanup_verified'])
        self.assertEqual(f.a.snapshot()['owners'], {})

    def test_lost_expiration_acknowledgment_retains_lease_without_retry(self):
        f = self.small(); original = f.transport.json
        def request(method, route, payload=None, **kw):
            value = original(method, route, payload, **kw)
            if method == 'POST' and payload.get('keep_alive') == 0:
                raise TimeoutError('lost acknowledgment')
            return value
        f.transport.json = request
        self.assert_retained(f, 'ollama_cleanup_uncertain')

    def test_continued_activity_at_each_final_boundary_has_honest_durable_outcome(self):
        for boundary in ('prepublication', 'postpublication', 'prerelease'):
            with self.subTest(boundary=boundary):
                f = self.small(); seen = []
                def apps():
                    if f.boundaries[-1] == boundary: seen.append(True)
                    return GPU1+',999,256\n' if seen else ''
                f.apps = apps
                job = self.run_worker(f)
                self.assertEqual(job['state'], 'success', job.get('error_code'))
                self.assertFalse(job['cleanup_verified'])
                self.assertNotIn('physical_ollama_idle', job['cleanup_proof'])
                self.assertEqual(f.a.snapshot()['owners'], {})

    def test_busy_admission_has_no_submit_intent_post_or_cleanup(self):
        from aihub_gpu_runner import ollama_cleanup
        # Reach the real preflight through the cleanup fixture's patched entry point.
        real = ORIGINAL_SUBMISSION_FAILURE
        f = self.small(); f.p['observation_timeout'] = 2
        f.models = [{'name': 'direct-client'}]
        with patch.object(ollama_cleanup, 'RecoveryObserver', return_value=f.observer), \
                patch.object(ollama_cleanup, 'submission_failure', side_effect=real), \
                patch.object(f.lease, 'mark', wraps=f.lease.mark) as mark:
            f.runner._work('owned-job', f.lease)
        self.assertFalse(any(c.args[0] == 'submit_intent' for c in mark.call_args_list))
        job = f.store.job('owned-job')
        self.assertEqual(job['error_code'], 'gpu_busy_not_submitted')
        self.assertTrue(job['submission_skipped'])
        self.assertFalse(any(c[0] == 'POST' for c in f.calls))
        self.assertNotIn('ollama_cleanup_context', job)
        self.assertEqual(f.a.snapshot()['owners'], {})

    def test_physical_busy_preflight_skips_without_post(self):
        from aihub_gpu_runner import ollama_cleanup
        f = self.small(); f.p['observation_timeout'] = 2
        f.memory = lambda: 1
        f.apps = lambda: GPU1+',304,0\n'
        original = f.observer.command.side_effect
        def command(argv, deadline):
            if argv[1] == 'top':
                return 'PID\n303\n304\n' if argv[2] == f.metas['ollama']['Id'] else 'PID\n202\n'
            return original(argv, deadline)
        f.observer.command.side_effect = command
        with patch.object(ollama_cleanup, 'RecoveryObserver', return_value=f.observer), \
                patch.object(ollama_cleanup, 'submission_failure', side_effect=ORIGINAL_SUBMISSION_FAILURE):
            f.runner._work('owned-job', f.lease)
        self.assertEqual(f.store.job('owned-job')['error_code'], 'gpu_busy_not_submitted')
        self.assertFalse(any(c[0] == 'POST' for c in f.calls))
        self.assertEqual(f.a.snapshot()['owners'], {})

    def test_busy_release_publication_failure_retains_owner(self):
        f = self.small(); f.models = [{'name': 'external'}]
        original = f.store.write
        def write(name, value):
            if name.startswith('results/'):
                raise OSError('synthetic publication failure')
            return original(name, value)
        f.store.write = write
        self.assert_retained(f, 'ollama_cleanup_os_error')

    def test_completed_release_lost_clear_ack_preserves_published_result(self):
        f = self.small(); f.models = [{'name': 'external'}]
        original = f.store.write
        def write(name, value):
            result = original(name, value)
            if name == 'ownership.json' and not value['owners']:
                raise OSError('clear committed, acknowledgment lost')
            return result
        f.store.write = write
        job = self.run_worker(f)
        self.assertEqual(job['state'], 'success')
        self.assertEqual(job['publication_warning'], 'ownership_release_sync_unconfirmed')
        self.assertEqual(f.a.snapshot()['owners'], {})
        result = f.runner.result('owned-job')
        self.assertEqual(result['value']['response'], 'synthetic')
        self.assertFalse(result['cleanup_verified'])

    def test_host_cleanup_busy_release_uses_real_lease_and_binding(self):
        from aihub_gpu_runner.host_stage import HostStage
        import types
        f = self.small(); f.models = [{'name': 'external'}]
        job = f.store.job('owned-job')
        job.update(host_stage=True, state='cleanup_pending', host_exit_code=0,
                   host_requests=[{'state': 'completed', 'model': 'synthetic'}])
        f.store.put_job(job); f.lease.mark('cleanup_pending')
        stage = types.SimpleNamespace(store=f.store, job_id='owned-job', is_ollama=True,
            dual=False, admission=f.a, target=f.target, lease=f.lease,
            transport_factory=lambda url: f.transport,
            update=lambda **kw: f.runner._update('owned-job', **kw))
        with patch('aihub_gpu_runner.ollama_cleanup.RecoveryObserver', return_value=f.observer):
            proof = HostStage._cleanup(stage)
        self.assertFalse(proof['cleanup_verified'])
        stage.update(state='success', cleanup_verified=False, cleanup_proof=proof)
        f.lease.release(proof,
            before_release=lambda: stage.cleanup_guard.commit_check(stage.cleanup_deadline, terminal=True),
            validate_clear=lambda: stage.cleanup_guard.release_binding(stage.cleanup_deadline))
        self.assertEqual(f.a.snapshot()['owners'], {})

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


from aihub_gpu_runner.ollama_cleanup import submission_failure as ORIGINAL_SUBMISSION_FAILURE

if __name__ == '__main__': unittest.main()
