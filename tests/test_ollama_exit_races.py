"""Deterministic process-exit interleavings; no GPU, Docker or network access."""
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

from aihub_gpu_runner.backends import CleanupPending
from aihub_gpu_runner.core import RecoveryRequired
from aihub_gpu_runner.host_stage import HostStage
from aihub_gpu_runner.ollama_cleanup import OllamaCleanupGuard, cleanup_failure
from aihub_gpu_runner.recovery import ProcessDisappeared, RecoveryObserver, process_birth
from tests import test_ollama_physical_cleanup as fixture


REAL_INIT = OllamaCleanupGuard.__init__


class ExitRaceTests(unittest.TestCase):
    def setUp(self):
        self.c = fixture.PhysicalCleanupTests('runTest')
        self.c.setUp()
        self.addCleanup(self.c.doCleanups)
        self.live = {'202': '20', '303': '30', '304': '40'}
        self.tops = {'music': ['202'], 'ollama': ['303', '304']}
        self.top_hook = self.stat_hook = self.gpu_hook = self.after_capture = None
        original = self.c.command

        def command(argv, deadline):
            if argv[1] == 'top':
                service = next(s for s, m in self.c.metas.items() if m['Id'] == argv[2])
                rows = list(self.tops[service])
                if self.top_hook:
                    self.top_hook(service)
                return 'PID\n' + '\n'.join(rows) + '\n'
            result = original(argv, deadline)
            if argv[1].startswith('--id=') and self.gpu_hook:
                self.gpu_hook()
            return result

        self.c.observer.command.side_effect = command
        self.c.observer.processes = types.MethodType(RecoveryObserver.processes, self.c.observer)

        def stat(path, *args, **kwargs):
            pid = str(path).split('/')[-2]
            if self.stat_hook:
                value = self.stat_hook(pid)
                if value is not None:
                    return value
            if pid not in self.live:
                raise FileNotFoundError(errno.ENOENT, 'PRIVATE /proc path')
            # Linux comm can contain spaces and closing parentheses.
            return pid + ' (synthetic comm)) ' + ' '.join(['S'] + ['0'] * 18 + [self.live[pid]])

        patch.object(Path, 'read_text', stat).start()
        self.c.guard.birth_reader = process_birth
        self.c.memory = lambda: 1
        self.c.apps = lambda: fixture.GPU0 + ',202,2000\n'

    def exit(self, pid='304', *, remove_top=True):
        self.live.pop(pid, None)
        if remove_top:
            for rows in self.tops.values():
                if pid in rows:
                    rows.remove(pid)

    def arm_exit(self, pid='304'):
        def once(service):
            if pid in self.tops[service]:
                self.top_hook = None
                self.exit(pid)
        self.top_hook = once

    def observations(self):
        return [r['ollama_cleanup_observation'] for r in self.c.records if 'ollama_cleanup_observation' in r]

    def verify(self):
        return self.c.guard.verify(2)

    def rejected(self, reason, exception=RecoveryRequired):
        with self.assertRaisesRegex(exception, reason):
            self.verify()
        self.assertIn('gpu1', self.c.state['owners'])

    def worker(self):
        self.c.clock.t = 1
        runner, lease, _ = self.c.worker()

        def init(guard, *args, **kwargs):
            kwargs['birth_reader'] = process_birth
            REAL_INIT(guard, *args, **kwargs)
        patch.object(OllamaCleanupGuard, '__init__', init).start()
        update = runner._update

        def write(job_id, **fields):
            value = update(job_id, **fields)
            if 'ollama_cleanup_context' in fields and self.after_capture:
                self.after_capture()
            return value
        runner._update = write
        return runner, lease

    def test_exit_between_docker_top_and_proc_requires_three_new_samples(self):
        self.c.prepare(); self.arm_exit()
        self.assertEqual(self.verify()['stable_samples'], 3)
        obs = self.observations()
        self.assertEqual([o['process_snapshot_discarded'] for o in obs], [True, False, False, False])
        self.assertEqual(obs[-1]['exited_ollama_processes'], {'304': '40'})

    def test_exit_during_capture_is_not_excused(self):
        self.arm_exit()
        with self.assertRaises(ProcessDisappeared):
            self.c.prepare()
        self.assertIsNone(self.c.guard.context)
        self.assertEqual(self.c.records[-1]['ollama_cleanup_failure']['pid'], '304')

    def test_main_exit_is_not_excused(self):
        self.c.prepare(); self.arm_exit('303')
        self.rejected('container_process_disappeared', ProcessDisappeared)

    def test_other_service_exit_is_not_excused(self):
        self.c.prepare(); self.arm_exit('202')
        self.rejected('container_process_disappeared', ProcessDisappeared)
        self.assertEqual(self.c.records[-1]['ollama_cleanup_failure']['service'], 'music')

    def test_unknown_child_disappearance_is_not_excused(self):
        self.c.prepare(); self.tops['ollama'].append('305')
        self.rejected('unverified_process_disappearance')

    def test_permission_error_is_not_disappearance_and_is_sanitized(self):
        self.c.prepare()
        def stat(pid):
            if pid == '304': raise PermissionError(errno.EACCES, 'PRIVATE secret')
        self.stat_hook = stat
        self.rejected('PRIVATE', PermissionError)
        detail = self.c.records[-1]['ollama_cleanup_failure']
        self.assertEqual(detail['reason'], 'ollama_cleanup_permission_denied')
        self.assertEqual(detail['errno'], errno.EACCES)
        self.assertEqual(detail['phase'], 'service_processes')
        self.assertNotIn('PRIVATE', json.dumps(detail))

    def test_io_error_is_not_disappearance(self):
        self.c.prepare()
        def stat(pid):
            if pid == '304': raise OSError(errno.EIO, 'read failed')
        self.stat_hook = stat
        self.rejected('read failed', OSError)

    def test_exact_esrch_is_an_expected_child_exit(self):
        self.c.prepare(); self.arm_exit()
        def stat(pid):
            if pid == '304' and pid not in self.live:
                raise ProcessLookupError(errno.ESRCH, 'process gone')
        self.stat_hook = stat
        self.assertEqual(self.verify()['stable_samples'], 3)
        self.assertTrue(self.observations()[0]['process_snapshot_discarded'])

    def test_missing_evidence_for_another_pid_is_rejected(self):
        self.c.prepare(); self.arm_exit()
        self.c.guard.birth_reader = lambda pid: (_ for _ in ()).throw(ProcessDisappeared('999', errno.ENOENT))
        self.rejected('unverified_process_disappearance')

    def test_malformed_stat_is_not_disappearance(self):
        self.c.prepare(); self.stat_hook = lambda pid: 'malformed' if pid == '304' else None
        self.rejected('container_process_stat_invalid')

    def test_live_pid_reuse_is_rejected(self):
        self.c.prepare(); self.live['304'] = '41'
        self.rejected('new_or_reused_process')

    def test_pid_reuse_during_disappearance_confirmation_is_rejected(self):
        self.c.prepare()
        def stat(pid):
            if pid == '304':
                self.stat_hook = None; self.live[pid] = '41'
                raise ProcessLookupError(errno.ESRCH, 'vanished')
        self.stat_hook = stat
        self.rejected('process_birth_changed')

    def test_transient_missing_read_is_not_positive_exclusion(self):
        self.c.prepare()
        def stat(pid):
            if pid == '304':
                self.stat_hook = None
                raise FileNotFoundError(errno.ENOENT, 'vanished')
        self.stat_hook = stat
        self.rejected('unverified_process_disappearance')

    def test_exit_between_compute_snapshot_and_final_birth_read(self):
        self.c.prepare()
        self.c.apps = lambda: fixture.GPU1 + ',304,32\n' if '304' in self.live else ''
        self.gpu_hook = lambda: self.exit()
        self.assertEqual(self.verify()['stable_samples'], 3)
        self.assertEqual([o['process_snapshot_discarded'] for o in self.observations()], [True, False, False, False])

    def test_stale_compute_row_discards_sample(self):
        self.c.prepare(); self.exit()
        rows = iter([fixture.GPU1 + ',304,32\n', '', '', ''])
        self.c.apps = lambda: next(rows)
        self.assertEqual(self.verify()['stable_samples'], 3)
        self.assertTrue(self.observations()[0]['process_snapshot_discarded'])

    def test_stale_compute_row_cannot_hide_invalid_memory(self):
        self.c.prepare(); self.exit(); self.c.apps = lambda: fixture.GPU1 + ',304,NaN\n'
        self.rejected('invalid_gpu_number')

    def test_stale_compute_row_cannot_hide_duplicate_pid(self):
        self.c.prepare(); self.exit(); self.c.apps = lambda: (fixture.GPU1 + ',304,32\n') * 2
        self.rejected('unknown_or_duplicate_gpu_process')

    def test_persistent_stale_compute_snapshot_expires_with_owner_held(self):
        self.c.prepare(); self.exit(); self.c.apps = lambda: fixture.GPU1 + ',304,32\n'
        with self.assertRaises(CleanupPending): self.verify()
        self.assertIn('gpu1', self.c.state['owners'])
        self.assertTrue(all(o['process_snapshot_discarded'] for o in self.observations()))

    def test_persistent_stale_top_expires_with_owner_held(self):
        self.c.prepare(); self.exit(remove_top=False)
        with self.assertRaises(CleanupPending): self.verify()
        self.assertIn('gpu1', self.c.state['owners'])

    def test_foreign_activity_is_not_hidden_by_expected_exit(self):
        self.c.prepare(); self.arm_exit(); self.c.apps = lambda: fixture.GPU1 + ',999,1\n'
        self.rejected('unknown_or_duplicate_gpu_process')

    def test_exited_child_on_unleased_gpu_is_rejected(self):
        self.c.prepare(); self.exit(); self.c.apps = lambda: fixture.GPU0 + ',304,1\n'
        self.rejected('unleased_gpu_activity')

    def test_generation_change_during_exit_is_rejected(self):
        self.c.prepare()
        def changed(service):
            if service == 'ollama':
                self.exit(); self.c.metas['ollama']['State']['StartedAt'] = 'changed'
        self.top_hook = changed
        self.rejected('generation_changed')

    def test_retired_pid_reuse_outside_docker_and_gpu_is_rejected(self):
        self.c.prepare(); self.arm_exit()
        record = self.c.guard.record
        def write(**fields):
            record(**fields)
            if fields.get('ollama_cleanup_observation', {}).get('process_snapshot_discarded'):
                self.live['304'] = '41'
        self.c.guard.record = write
        self.rejected('process_birth_changed')

    def test_retired_pid_reappearance_even_same_birth_is_rejected(self):
        self.c.prepare(); self.arm_exit()
        record = self.c.guard.record
        def write(**fields):
            record(**fields)
            if fields.get('ollama_cleanup_observation', {}).get('process_snapshot_discarded'):
                self.live['304'] = '40'
        self.c.guard.record = write
        self.rejected('unverified_process_disappearance')

    def test_host_without_completed_owned_requests_cannot_excuse_exit(self):
        job = self.c.store.job('owned-job')
        job.update(host_stage=True, host_exit_code=0, host_requests=[]); self.c.store.put_job(job)
        self.c.prepare(); self.arm_exit()
        self.rejected('unverified_process_disappearance')

    def test_exit_resets_previously_good_samples(self):
        self.c.prepare()
        record = self.c.guard.record
        def write(**fields):
            record(**fields)
            if len(self.observations()) == 2: self.arm_exit()
        self.c.guard.record = write
        self.verify()
        self.assertEqual([o['process_snapshot_discarded'] for o in self.observations()],
                         [False, False, True, False, False, False])

    def test_worker_safe_exit_releases_once_without_replay(self):
        self.after_capture = self.arm_exit
        runner, lease = self.worker(); runner._work('owned-job', lease)
        self.assertEqual(self.c.store.job('owned-job')['state'], 'success')
        lease.release.assert_called_once()
        self.assertEqual(len([c for c in self.c.calls if c[0] == 'POST']), 2)

    def test_worker_foreign_gpu_failure_has_specific_durable_reason(self):
        runner, lease = self.worker(); self.c.apps = lambda: fixture.GPU1 + ',999,1\n'
        runner._work('owned-job', lease)
        job = self.c.store.job('owned-job')
        self.assertEqual(job['state'], 'reconciling')
        self.assertEqual(job['error_code'], 'ollama_cleanup_unknown_or_duplicate_gpu_process')
        self.assertEqual(job['ollama_cleanup_failure']['phase'], 'compute_apps')
        self.assertNotIn('ollama_cleanup_observation', job)
        lease.release.assert_not_called()

    def test_worker_storage_failure_has_sanitized_phase_and_errno(self):
        runner, lease = self.worker(); update = runner._update
        def write(job_id, **fields):
            if 'ollama_cleanup_observation' in fields: raise OSError(errno.ENOSPC, 'PRIVATE disk path')
            return update(job_id, **fields)
        runner._update = write; runner._work('owned-job', lease)
        job = self.c.store.job('owned-job'); detail = job['ollama_cleanup_failure']
        self.assertEqual(job['state'], 'reconciling')
        self.assertEqual(job['error_code'], 'ollama_cleanup_persistence_error')
        self.assertEqual(detail['phase'], 'observation_persistence')
        self.assertEqual(detail['errno'], errno.ENOSPC)
        self.assertNotIn('PRIVATE', json.dumps(detail))
        lease.release.assert_not_called()

    def test_worker_context_storage_failure_has_specific_reason(self):
        runner, lease = self.worker(); update = runner._update
        def write(job_id, **fields):
            if 'ollama_cleanup_context' in fields: raise OSError(errno.ENOSPC, 'PRIVATE disk path')
            return update(job_id, **fields)
        runner._update = write; runner._work('owned-job', lease)
        job = self.c.store.job('owned-job')
        self.assertEqual(job['error_code'], 'ollama_cleanup_persistence_error')
        self.assertEqual(job['ollama_cleanup_failure']['phase'], 'context_persistence')
        self.assertNotIn('ollama_cleanup_context', job)
        self.assertEqual(len([c for c in self.c.calls if c[0] == 'POST']), 1)
        lease.release.assert_not_called()

    def test_diagnostic_write_failure_preserves_original_error_and_owner(self):
        self.c.prepare()
        self.c.apps = lambda: fixture.GPU1 + ',999,1\n'
        def write(**fields): raise OSError(errno.ENOSPC, 'diagnostic storage unavailable')
        self.c.guard.record = write
        self.rejected('unknown_or_duplicate_gpu_process')

    def test_worker_main_exit_has_specific_reason_and_pid(self):
        self.after_capture = lambda: self.arm_exit('303')
        runner, lease = self.worker(); runner._work('owned-job', lease)
        job = self.c.store.job('owned-job')
        self.assertEqual(job['error_code'], 'ollama_cleanup_unverified_process_disappearance')
        self.assertEqual(job['ollama_cleanup_failure']['pid'], '303')
        self.assertEqual(job['ollama_cleanup_failure']['errno'], errno.ENOENT)
        lease.release.assert_not_called()

    def test_lost_unload_ack_retains_specific_failure_and_owner(self):
        self.after_capture = lambda: setattr(self.c, 'transport_failure', TimeoutError('PRIVATE lost ack'))
        runner, lease = self.worker(); runner._work('owned-job', lease)
        job = self.c.store.job('owned-job')
        self.assertEqual(job['state'], 'cleanup_pending')
        self.assertEqual(job['error_code'], 'ollama_cleanup_uncertain')
        self.assertEqual(job['ollama_cleanup_failure']['phase'], 'owned_unload')
        lease.release.assert_not_called()

    def test_host_stage_failure_is_durably_reported(self):
        job = self.c.store.job('owned-job')
        job.update(host_stage=True, host_exit_code=0, host_requests=[{'state': 'completed', 'model': 'synthetic'}])
        self.c.store.put_job(job)
        def update(**fields):
            job = self.c.store.job('owned-job'); job.update(fields); self.c.store.put_job(job)
            if 'ollama_cleanup_context' in fields: self.arm_exit('303')
        stage = types.SimpleNamespace(store=self.c.store, job_id='owned-job', is_ollama=True,
            dual=False, admission=self.c.a, target=self.c.target,
            lease=types.SimpleNamespace(owner=self.c.owner),
            transport_factory=lambda url: self.c.transport, update=update)
        with patch('aihub_gpu_runner.ollama_cleanup.RecoveryObserver', return_value=self.c.observer):
            with self.assertRaises(ProcessDisappeared): HostStage._cleanup(stage)
        self.assertEqual(self.c.store.job('owned-job')['ollama_cleanup_failure']['pid'], '303')

    def test_arbitrary_exception_strings_are_never_persisted(self):
        for exc in (RuntimeError('Bearer PRIVATE'), RecoveryRequired('PRIVATE_reason'),
                    OSError(errno.EIO, 'PRIVATE /secret/path')):
            with self.subTest(exception=type(exc).__name__):
                detail = cleanup_failure(exc, 'service_processes', service='PRIVATE', pid='PRIVATE')
                self.assertNotIn('PRIVATE', json.dumps(detail))
                self.assertIsNone(detail['pid']); self.assertIsNone(detail['service'])


@unittest.skipUnless(sys.platform == 'linux', 'native /proc interleaving requires Linux')
class NativeProcessExitTests(unittest.TestCase):
    def observer(self):
        child = subprocess.Popen([sys.executable, '-I', '-B', '-c', 'import sys; sys.stdin.buffer.read()'], stdin=subprocess.PIPE)
        def cleanup():
            if not child.stdin.closed: child.stdin.close()
            child.wait(timeout=5)
        self.addCleanup(cleanup)
        pid = str(child.pid)
        self.assertTrue(process_birth(pid).isdecimal())
        identity = {'running': True, 'id': 'synthetic', 'pid': os.getpid()}
        observer = RecoveryObserver({'docker': '/unused'}, {}, {})
        def top(argv, deadline):
            child.stdin.close(); child.wait(timeout=5)
            return 'PID\n' + str(os.getpid()) + '\n' + pid + '\n'
        observer.command = top
        return observer, identity, pid

    def test_native_child_exit_between_enumeration_and_proc_is_strict_by_default(self):
        observer, identity, pid = self.observer()
        with self.assertRaises(ProcessDisappeared) as result: observer.processes(identity, 10)
        self.assertEqual(result.exception.pid, pid)
        self.assertEqual(result.exception.errno, errno.ENOENT)

    def test_native_exit_requires_explicit_exact_pid_confirmation(self):
        observer, identity, pid = self.observer()
        confirmed = []
        def absent(candidate):
            self.assertEqual(candidate, pid)
            with self.assertRaises(ProcessDisappeared): process_birth(candidate)
            confirmed.append(candidate)
            return True
        actual = observer.processes(identity, 10, on_missing=absent)
        self.assertEqual(actual, {str(os.getpid()): process_birth(os.getpid())})
        self.assertEqual(confirmed, [pid])


if __name__ == '__main__': unittest.main()
