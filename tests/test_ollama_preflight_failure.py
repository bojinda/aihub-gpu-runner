"""Read-only preflight failures: actual worker/host, durable store and native lease."""
import unittest
from unittest.mock import Mock, patch

from aihub_gpu_runner import ollama_cleanup as cleanup
from aihub_gpu_runner.backends import Uncertain
from aihub_gpu_runner.core import Lease
from aihub_gpu_runner.host_stage import HostStage
from tests import test_cleanup_budget as budget

# Also permits running these regressions against the accepted pre-correction wheel.
ENTRY = 'submission_failure' if hasattr(cleanup, 'submission_failure') else 'submission_ready'
PREFLIGHT = getattr(cleanup, ENTRY)
MARK, RELEASE = Lease.mark, Lease.release
NEVER_SUBMITTED = dict(completion_verified=True, cleanup_verified=True, policy='never_submitted')


class PreflightFailureTests(unittest.TestCase):
    def fixture(self, host=False, failure='api'):
        f = budget.WorkerBudgetTests.fixture(self, seconds=.1, history=20, authority_bytes=30_000)
        if host:
            f.lease.release(NEVER_SUBMITTED); f.lease.close()
            f.stage = HostStage(f.a, 'gpu1', 'preflight-host', 'synthetic-hash',
                ollama_target=f.target, transport_factory=lambda url: f.transport)
        self.enterContext(patch.object(cleanup, ENTRY, side_effect=PREFLIGHT))
        self.enterContext(patch.object(cleanup, 'RecoveryObserver', return_value=f.observer))
        f.child = self.enterContext(patch('subprocess.Popen', side_effect=AssertionError('child forbidden')))
        f.marks = self.enterContext(patch.object(Lease, 'mark', autospec=True, side_effect=MARK))
        f.releases = self.enterContext(patch.object(Lease, 'release', autospec=True, side_effect=RELEASE))
        f.observer.gpu_sample = Mock(return_value={})
        if failure == 'api':
            original = f.transport.json
            f.unavailable = True
            def request(method, route, *a, **kw):
                if f.unavailable and method == 'GET' and route == '/api/ps':
                    raise Uncertain('backend_transport_or_response_failure')
                return original(method, route, *a, **kw)
            f.transport.json = request
            f.expected = 'ollama_preflight_backend_transport_or_response_failure'
        else:
            error = (OSError('PRIVATE observation path') if failure == 'os' else
                     ValueError('PRIVATE malformed GPU response'))
            f.observer.gpu_sample.side_effect = error
            f.expected = 'ollama_preflight_' + ('os_error' if failure == 'os' else 'invalid_observation')
        return f

    def run_job(self, f, host):
        if host:
            self.assertEqual(f.stage.run(['never-launch'], {}, timeout=0), 75)
        else:
            f.runner._work('owned-job', f.lease)
        return f.store.job('preflight-host' if host else 'owned-job')

    def assert_no_effects(self, f):
        self.assertFalse(any(c.args[1] == 'submit_intent' for c in f.marks.call_args_list))
        self.assertFalse(any(c[0] == 'POST' for c in f.calls))
        f.child.assert_not_called()

    def check_failure(self, host, failure):
        f = self.fixture(host, failure)
        job = self.run_job(f, host)
        self.assertEqual(job['state'], 'failed')
        self.assertEqual(job['error_code'], f.expected)
        self.assertTrue(job['submission_skipped'])
        self.assertNotIn('PRIVATE', str(job))
        self.assertNotIn('physical_ollama_idle', str(job))
        self.assertNotIn('ollama_cleanup_context', job)
        self.assert_no_effects(f)
        f.releases.assert_called_once()
        self.assertEqual(f.releases.call_args.args[1], NEVER_SUBMITTED)
        self.assertEqual(f.a.snapshot()['owners'], {})
        # API service restoration needs no lease recovery, inference or unload.
        if failure == 'api':
            f.unavailable = False
            self.assertIsNone(PREFLIGHT(f.a, f.target, f.backend))
        following = f.a.try_acquire('following-job', 'next-hash', ('gpu1',), 'ollama')
        self.assertIsNotNone(following)
        following.release(NEVER_SUBMITTED); following.close()

    def test_worker_transient_api_unavailability(self): self.check_failure(False, 'api')
    def test_host_transient_api_unavailability(self): self.check_failure(True, 'api')
    def test_worker_observation_os_error(self): self.check_failure(False, 'os')
    def test_host_observation_os_error(self): self.check_failure(True, 'os')
    def test_worker_malformed_observation(self): self.check_failure(False, 'invalid')
    def test_host_malformed_observation(self): self.check_failure(True, 'invalid')

    def check_uncertain_write(self, host, job_write=False):
        f = self.fixture(host)
        original = f.store.write
        failures = []
        def write(name, value):
            selected = ((str(name).startswith('jobs/') and value.get('state') == 'failed') if job_write else
                        (name == 'ownership.json' and not value['owners']))
            if selected and not failures:
                failures.append(True)
                raise OSError('uncertain durable write')
            return original(name, value)
        f.store.write = write
        if host:
            with self.assertRaises(OSError): self.run_job(f, host)
        else:
            self.run_job(f, host)
        self.assertEqual(failures, [True])
        job = f.store.job('preflight-host' if host else 'owned-job')
        self.assertEqual(job['state'], 'reconciling')
        self.assertIn('gpu1', f.a.snapshot()['owners'])
        self.assert_no_effects(f)
        self.assertEqual(f.releases.call_count, 0 if job_write else 1)
        self.assertIsNone(f.a.try_acquire('following-job', 'next-hash', ('gpu1',), 'ollama'))

    def test_worker_uncertain_release_retains_ownership(self): self.check_uncertain_write(False)
    def test_host_uncertain_release_retains_ownership(self): self.check_uncertain_write(True)
    def test_worker_uncertain_failure_record_retains_ownership(self): self.check_uncertain_write(False, True)
    def test_host_uncertain_failure_record_retains_ownership(self): self.check_uncertain_write(True, True)


if __name__ == '__main__': unittest.main()
