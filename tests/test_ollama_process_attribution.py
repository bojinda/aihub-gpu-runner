"""Offline process convergence is not request attribution; no backend access."""
import copy
import errno
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from aihub_gpu_runner.core import AtomicStore, RecoveryRequired, Runner
from aihub_gpu_runner.ollama_cleanup import cleanup_failure
from tests import test_ollama_exit_races as races
from tests.test_ollama_physical_cleanup import GPU1


class ProcessAttributionTests(unittest.TestCase):
    def setUp(self):
        # Reuse the real Docker-top parser and /proc birth reader with controlled
        # kernel/process snapshots; all HTTP, subprocess and time are simulated.
        self.r = races.ExitRaceTests('runTest')
        self.r.setUp()
        self.addCleanup(self.r.doCleanups)
        self.c = self.r.c

    def new_child(self, *, overlap=False):
        if not overlap:
            self.r.exit()
        self.r.live['305'] = '50'
        self.r.tops['ollama'].append('305')

    def detail(self):
        return self.c.store.job('owned-job')['ollama_cleanup_failure']

    def assert_identity(self, pid, comparison, expected, observed, basis='captured_ollama'):
        detail = self.detail()
        self.assertEqual(detail['pid'], pid)
        self.assertEqual(detail['process_identity'], dict(comparison=comparison,
            basis=basis, expected_birth=expected, observed_birth=observed))

    def test_normal_discovery_child_turnover_cannot_be_attributed_by_idle_telemetry(self):
        self.c.prepare()
        self.new_child()  # Synthetic known-benign origin is NOT visible to guard.
        self.r.rejected('new_or_reused_process')
        self.assert_identity('305', 'new_pid', None, '50')
        self.assertEqual(self.detail()['phase'], 'service_processes')
        self.assertEqual(self.detail()['service'], 'ollama')
        self.assertEqual(self.r.observations(), [])
        self.assertFalse(any(a[1].startswith('--query-compute') for a in self.c.commands))

    def test_overlapping_owned_runner_and_new_discovery_child_are_rejected(self):
        self.c.prepare()
        self.new_child(overlap=True)
        self.r.rejected('new_or_reused_process')
        self.assert_identity('305', 'new_pid', None, '50')
        self.assertEqual(self.r.live['304'], '40')

    def test_short_lived_unknown_child_missing_before_proc_is_not_excused(self):
        self.c.prepare()
        self.new_child()
        self.r.arm_exit('305')
        self.r.rejected('unverified_process_disappearance')
        self.assert_identity('305', 'uncaptured_pid_disappeared', None, None)

    def test_reused_child_pid_retains_both_births(self):
        self.c.prepare()
        self.r.live['304'] = '41'
        self.r.rejected('new_or_reused_process')
        self.assert_identity('304', 'birth_changed', '40', '41')

    def test_reused_main_pid_remains_rejected(self):
        self.c.prepare()
        self.r.live['303'] = '31'
        self.r.rejected('new_or_reused_process')
        self.assert_identity('303', 'birth_changed', '30', '31')

    def test_pid_reuse_after_compute_sample_has_distinct_comparison_basis(self):
        self.c.prepare()
        self.c.apps = lambda: GPU1 + ',304,1\n'
        self.r.gpu_hook = lambda: self.r.live.update({'304': '41'})
        self.r.rejected('process_birth_changed')
        self.assert_identity('304', 'birth_changed', '40', '41', 'service_snapshot')

    def test_ambiguous_cross_container_pid_is_not_attributed(self):
        self.c.prepare()
        self.r.tops['ollama'].append('202')
        self.r.rejected('ambiguous_process')
        self.assert_identity('202', 'duplicate_pid', '20', '20', 'service_snapshot')

    def test_unknown_compute_process_diagnostic_does_not_guess_birth(self):
        self.c.prepare()
        self.c.apps = lambda: GPU1 + ',999,1\n'
        self.r.rejected('unknown_or_duplicate_gpu_process')
        self.assert_identity('999', 'unknown_compute_pid', None, None, 'service_snapshot')

    def test_child_created_during_single_unload_keeps_durable_failure_and_owner(self):
        runner, lease = self.r.worker()
        original = self.c.transport.json
        def transport(method, route, payload=None, timeout=1):
            reply = original(method, route, payload, timeout)
            if method == 'POST' and payload.get('keep_alive') == 0:
                self.new_child()
            return reply
        self.c.transport.json = transport
        runner._work('owned-job', lease)
        job = self.c.store.job('owned-job')
        self.assertEqual(job['state'], 'reconciling')
        self.assertEqual(job['error_code'], 'ollama_cleanup_new_or_reused_process')
        self.assert_identity('305', 'new_pid', None, '50')
        self.assertEqual(job['ollama_cleanup_context']['ollama_processes'], {'303': '30', '304': '40'})
        self.assertNotIn('ollama_cleanup_observation', job)
        self.assertNotIn('cleanup_proof', job)
        lease.release.assert_not_called()
        self.assertIn('gpu1', self.c.state['owners'])
        # Eventual child exit and request reconnection cannot replay work or
        # retrospectively convert the durable failure into cleanup success.
        self.r.exit('305')
        runner.submit('owned-job', 'ollama', 'generate', job['payload'])
        self.assertEqual(len([x for x in self.c.calls if x[0] == 'POST']), 2)
        self.assertEqual(self.c.store.job('owned-job')['state'], 'reconciling')
        lease.release.assert_not_called()

    def test_direct_client_load_between_empty_residency_and_top_fails_closed(self):
        self.c.prepare()
        def concurrent_request(service):
            if service == 'music':  # After /api/ps, before Ollama top.
                self.r.top_hook = None
                self.new_child(overlap=True)
        self.r.top_hook = concurrent_request
        self.r.rejected('new_or_reused_process')
        self.assert_identity('305', 'new_pid', None, '50')

    def test_identity_failure_survives_disk_reopen_and_restart_without_replay(self):
        self.r.after_capture = self.new_child
        runner, lease = self.r.worker()
        runner._work('owned-job', lease)
        failed = self.c.store.job('owned-job')
        with tempfile.TemporaryDirectory() as tmp:
            AtomicStore(Path(tmp) / 'state').put_job(failed)
            disk = AtomicStore(Path(tmp) / 'state')
            gate = types.SimpleNamespace(store=disk, snapshot=self.c.a.snapshot)
            with patch('aihub_gpu_runner.core.threading.Thread.start'):
                restarted = Runner(gate, {'ollama': self.c.target},
                                   {'ollama': self.c.backend}, recovery=Mock())
            try:
                count = len(self.c.calls)
                job = restarted.submit('owned-job', 'ollama', 'generate', failed['payload'])
                self.assertEqual(job['state'], 'reconciling')
                self.assertEqual(job['ollama_cleanup_failure'], failed['ollama_cleanup_failure'])
                self.assertEqual(len(self.c.calls), count)
                self.assertIn('gpu1', gate.snapshot()['owners'])
                lease.release.assert_not_called()
            finally:
                restarted.pool.shutdown()
                restarted.service_lock.close()

    def test_direct_client_residency_is_not_excused_as_process_convergence(self):
        self.c.prepare()
        self.new_child()
        self.c.models = [{'model': 'synthetic-direct-client'}]
        self.r.rejected('unexplained_backend_activity_ollama')
        self.assertEqual(self.detail()['phase'], 'queues')
        self.assertNotIn('process_identity', self.detail())

    def test_diagnostic_write_retry_keeps_pair_but_never_releases(self):
        runner, lease = self.r.worker()
        self.r.after_capture = self.new_child
        update = runner._update
        attempts = []
        def write(job_id, **fields):
            if 'ollama_cleanup_failure' in fields:
                attempts.append(copy.deepcopy(fields['ollama_cleanup_failure']))
                if len(attempts) == 1:
                    raise OSError(errno.ENOSPC, 'PRIVATE storage path')
            return update(job_id, **fields)
        runner._update = write
        runner._work('owned-job', lease)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]['process_identity'], attempts[1]['process_identity'])
        self.assert_identity('305', 'new_pid', None, '50')
        self.assertNotIn('PRIVATE', json.dumps(self.detail()))
        lease.release.assert_not_called()

    def test_total_diagnostic_storage_failure_cannot_release(self):
        runner, lease = self.r.worker()
        self.r.after_capture = self.new_child
        update = runner._update
        def write(job_id, **fields):
            if 'ollama_cleanup_failure' in fields:
                raise OSError(errno.ENOSPC, 'PRIVATE storage path')
            return update(job_id, **fields)
        runner._update = write
        with self.assertRaises(OSError):
            runner._work('owned-job', lease)
        lease.release.assert_not_called()
        self.assertIn('gpu1', self.c.state['owners'])
        self.assertNotIn('cleanup_proof', self.c.store.job('owned-job'))

    def test_phase_change_clears_previous_comparison(self):
        self.c.prepare()
        self.new_child()
        self.r.rejected('new_or_reused_process')
        self.c.guard.mark_phase('observation_persistence')
        detail = self.c.guard.failure(OSError(errno.ENOSPC, 'PRIVATE'))
        self.assertNotIn('process_identity', detail)
        self.assertIsNone(detail['pid'])

    def test_diagnostic_values_are_bounded_numeric_and_allowlisted(self):
        for value in ('PRIVATE bearer/prompt', '9' * 5000, '１２３', '-1',
                      str(2**64), {'PRIVATE': 'secret'}, True):
            with self.subTest(value_type=type(value).__name__):
                detail = cleanup_failure(RecoveryRequired('ollama_cleanup_new_or_reused_process'),
                    'service_processes', service='ollama', pid=value,
                    process_identity=dict(comparison='new_pid', basis='captured_ollama',
                                          observed_birth=value, expected_birth=value))
                self.assertIsNone(detail['pid'])
                self.assertIsNone(detail['process_identity']['observed_birth'])
                self.assertIsNone(detail['process_identity']['expected_birth'])
                self.assertLess(len(json.dumps(detail)), 700)
                self.assertNotIn('PRIVATE', json.dumps(detail))
        for value in ([], {}, 'PRIVATE'):
            detail = cleanup_failure(RuntimeError('PRIVATE'), 'service_processes',
                process_identity=dict(comparison=value, basis=value))
            self.assertNotIn('process_identity', detail)


if __name__ == '__main__':
    unittest.main()
