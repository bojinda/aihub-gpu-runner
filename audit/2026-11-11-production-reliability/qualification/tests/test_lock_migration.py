"""Native Linux, disposable files only; never call a backend or GPU utility."""
import copy
import hashlib
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from aihub_gpu_runner.config import admission_from_config, load_config
from aihub_gpu_runner.core import AtomicStore, NativeLock, RecoveryRequired, Runner, encode, lock_identity, strict_json
from aihub_gpu_runner.lock_migration import ATTESTATIONS, migrate_physical_locks
from tests.test_dual_gpu import fixture


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def child_attempt(path, output):
    lock = NativeLock(path, create=False)
    output.put(lock.acquire(0))
    lock.close()


def crash_at_commit(old, new, evidence, boundary):
    replace, write = os.replace, AtomicStore.write
    def interrupted_replace(source, destination):
        if Path(destination).name == 'ownership.json':
            if boundary == 'before_replace':
                os.kill(os.getpid(), signal.SIGKILL)
            replace(source, destination)
            if boundary == 'after_replace':
                os.kill(os.getpid(), signal.SIGKILL)
        else:
            replace(source, destination)
    def interrupted_write(store, name, value):
        write(store, name, value)
        if name == 'ownership.json' and boundary == 'after_fsync':
            os.kill(os.getpid(), signal.SIGKILL)
    with patch('os.replace', interrupted_replace), patch.object(AtomicStore, 'write', interrupted_write):
        migrate_physical_locks(old, new, 'retained', evidence)


@unittest.skipUnless(sys.platform.startswith('linux'), 'native Linux physical-lock migration')
class LockMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='lock-migration-')
        self.root = Path(self.tmp.name)
        policy_root = self.root / 'policy'
        policy_root.mkdir(mode=0o700)
        _, dual_config, _, _, _ = fixture(policy_root)
        config = strict_json(dual_config.read_bytes())
        config['targets']['meeting-dual-ha'] = copy.deepcopy(config['targets']['meeting-dual'])
        comfy = {'kind': 'comfy', 'resources': ['gpu0'], 'base_url': 'http://127.0.0.1:1',
                 'workflow': {'1': {'class_type': 'SaveImage', 'inputs': {}}}, 'output_node': '1'}
        config['targets']['wallpaper'] = comfy
        config['targets']['music'] = copy.deepcopy(comfy)
        self.state_dir = self.root / 'persistent-state'
        config['state_dir'] = str(self.state_dir)
        self.old_paths = {r: self.root / (r + '-temporary.lock') for r in ('gpu0', 'gpu1')}
        config['lock_paths'] = {r: str(p) for r, p in self.old_paths.items()}
        for path in self.old_paths.values():
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        self.old, self.new = self.root / 'frozen-old.json', self.root / 'prepared-new.json'
        self.old.write_bytes(encode(config))
        _, _, self.gate = admission_from_config(self.old)
        self.gate.bootstrap({'approval_ref': 'synthetic-bootstrap',
            'bootstrap_id': self.gate.snapshot()['bootstrap_id'], 'backend_quiescent': True, 'cleanup_verified': True})
        lease = self.gate.try_acquire('retained', 'h' * 64, ('gpu0',), 'wallpaper')
        lease.mark('reconciling', {'prompt_id': 'synthetic-prompt'})
        self.owner = self.gate.snapshot()['owners']['gpu0']
        self.gate.store.put_job(dict(request_id='retained', state='reconciling', phase='running',
            lease_id=self.owner['lease_id'], request_hash=self.owner['request_hash'], target='wallpaper',
            resources=['gpu0'], payload={'synthetic_private_evidence': 'preserve-byte-for-byte'},
            error_code='comfy_history_uncertain', completion_verified=None))
        self.gate.store.put_job({'request_id': 'earlier', 'state': 'success', 'request_hash': 'historical'})
        self.gate.store.write('results/earlier.json', {'synthetic': 'historical-result'})
        lease.close()  # Process termination releases flock, not durable ownership.
        self.baseline = self.gate.store.path('ownership.json').read_bytes()
        for path in self.old_paths.values():
            path.unlink()  # Only synthetic temporary paths; emulate lost /tmp files.
        self.new_paths = {r: self.state_dir / 'physical-locks' / (r + '.lock') for r in ('gpu0', 'gpu1')}
        (self.state_dir / 'physical-locks').mkdir(mode=0o700)
        for path in self.new_paths.values():
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            os.fsync(fd)
            os.close(fd)
        new_config = copy.deepcopy(config)
        new_config['lock_paths'] = {r: str(p) for r, p in self.new_paths.items()}
        self.new.write_bytes(encode(new_config))
        self.job_path = self.gate.store.path('jobs/retained.json')
        self.evidence = dict(approval_ref='synthetic-migration', owner=copy.deepcopy(self.owner),
            old_scope=load_config(self.old)[-1], new_scope=load_config(self.new)[-1],
            old_config_sha256=digest(self.old), new_config_sha256=digest(self.new),
            ownership_sha256=digest(self.gate.store.path('ownership.json')), job_sha256=digest(self.job_path),
            pending_job_sha256={},
            old_lock_identity=strict_json(self.baseline)['lock_identity'],
            new_lock_identity={r: lock_identity(p.stat()) for r, p in self.new_paths.items()},
            exclusion_evidence_ref='synthetic-exclusion', backend_evidence_ref='synthetic-termination',
            gpu_evidence_ref='synthetic-quiescence', **{k: True for k in ATTESTATIONS})
        self.preserved = {str(p.relative_to(self.state_dir)): p.read_bytes()
                          for folder in ('jobs', 'results') for p in (self.state_dir / folder).glob('*')}

    def tearDown(self):
        self.tmp.cleanup()

    def migrate(self, evidence=None):
        return migrate_physical_locks(self.old, self.new, 'retained', evidence or self.evidence)

    def assert_unchanged(self):
        self.assertEqual(self.gate.store.path('ownership.json').read_bytes(), self.baseline)
        for relative, contents in self.preserved.items():
            self.assertEqual((self.state_dir / relative).read_bytes(), contents)

    def test_migration_preserves_owner_jobs_targets_and_separate_config_activation(self):
        configs = self.old.read_bytes(), self.new.read_bytes()
        self.assertTrue(self.migrate()['owners_retained'])
        self.assertEqual(configs, (self.old.read_bytes(), self.new.read_bytes()))
        for relative, contents in self.preserved.items():
            self.assertEqual((self.state_dir / relative).read_bytes(), contents)
        with self.assertRaises(RecoveryRequired):
            admission_from_config(self.old)
        _, targets, gate = admission_from_config(self.new)
        self.assertIn('meeting-dual-ha', targets)
        self.assertEqual(gate.snapshot()['owners'], {'gpu0': self.owner})
        self.assertIsNone(gate.try_acquire('next-wallpaper', 'next', ('gpu0',), 'wallpaper'))
        self.assertIsNone(gate.try_acquire('next-dual', 'next', ('gpu0', 'gpu1'), 'meeting-dual-ha'))
        record = gate.snapshot()['physical_lock_migrations'][-1]
        self.assertEqual(record['old_identity_source'], 'persisted_ownership_only')
        self.assertEqual(record['old_file_observations'], {'gpu0': 'absent', 'gpu1': 'absent'})
        self.assertEqual(record['job_sha256'], digest(self.job_path))
        self.assertTrue(self.migrate()['already_recorded'])
        self.assertEqual(len(gate.snapshot()['physical_lock_migrations']), 1)

    def test_migration_cli_works_with_missing_old_locks_without_bootstrap(self):
        evidence_path = self.root / 'migration-evidence.json'
        evidence_path.write_bytes(encode(self.evidence))
        result = subprocess.run([sys.executable, '-m', 'aihub_gpu_runner', '--config', str(self.old),
            'migrate-physical-locks', '--new-config', str(self.new), '--job-id', 'retained',
            '--evidence-file', str(evidence_path)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(strict_json(result.stdout)['owners_retained'])
        self.assertEqual(self.gate.store.read('ownership.json')['owners'], {'gpu0': self.owner})
        self.assertEqual(self.job_path.read_bytes(), self.preserved['jobs/retained.json'])

    def test_evidence_bound_unadmitted_waiting_job_preserved_and_normal_start_fails_without_execution(self):
        self.gate.store.put_job({'request_id': 'unadmitted', 'state': 'waiting', 'target': 'wallpaper',
            'resources': ['gpu0'], 'request_hash': 'unadmitted-hash', 'created_at': 'synthetic'})
        path = self.gate.store.path('jobs/unadmitted.json')
        before = path.read_bytes()
        with self.assertRaisesRegex(RecoveryRequired, 'migration_pending_job_evidence_changed'):
            self.migrate()
        evidence = dict(self.evidence, pending_job_sha256={'unadmitted': digest(path)})
        self.migrate(evidence)
        self.assertEqual(path.read_bytes(), before)
        _, targets, gate = admission_from_config(self.new)
        class NeverBackend:
            def execute(self, *args, **kwargs):
                raise AssertionError('migration/startup must not submit work')
        runner = Runner(gate, targets, {name: NeverBackend() for name in targets})
        try:
            self.assertEqual(gate.store.job('unadmitted')['state'], 'failed')
            self.assertEqual(gate.store.job('unadmitted')['error_code'], 'restart_before_admission')
            self.assertEqual(gate.snapshot()['owners'], {'gpu0': self.owner})
        finally:
            runner.close()

    def test_missing_startup_and_missing_existing_lock_open_do_not_recreate(self):
        with self.assertRaisesRegex(RecoveryRequired, 'existing_physical_lock_missing'):
            admission_from_config(self.old)
        with self.assertRaises(FileNotFoundError):
            NativeLock(self.old_paths['gpu0'], create=False).acquire(0)
        self.assertFalse(self.old_paths['gpu0'].exists())

    def test_persistent_identities_and_ownership_survive_simulated_process_restart(self):
        self.migrate()
        before = {r: lock_identity(p.stat()) for r, p in self.new_paths.items()}
        for _ in range(2):
            _, _, gate = admission_from_config(self.new)
            self.assertEqual(before, {r: lock_identity(p.stat()) for r, p in self.new_paths.items()})
            self.assertEqual(gate.snapshot()['owners']['gpu0'], self.owner)
            self.assertIsNone(gate.try_acquire('blocked', 'h', ('gpu0',), 'wallpaper'))

    def test_gpu1_native_process_exclusion_and_normal_release(self):
        self.migrate()
        _, _, gate = admission_from_config(self.new)
        lease = gate.try_acquire('synthetic-gpu1', 'hash', ('gpu1',), 'ollama')
        self.assertIsNotNone(lease)
        context = multiprocessing.get_context('spawn')
        output = context.Queue()
        process = context.Process(target=child_attempt, args=(str(self.new_paths['gpu1']), output))
        process.start()
        try:
            self.assertFalse(output.get(timeout=5))
            process.join(5)
            self.assertEqual(process.exitcode, 0)
            self.assertIsNone(gate.try_acquire('another', 'h', ('gpu1',), 'ollama'))
        finally:
            if process.is_alive():
                process.terminate()
                process.join(5)
            output.close()
            output.join_thread()
            lease.release({'completion_verified': True, 'cleanup_verified': True})
            lease.close()
        next_lease = gate.try_acquire('next-gpu1', 'hash', ('gpu1',), 'ollama')
        self.assertIsNotNone(next_lease)
        next_lease.release({'completion_verified': True, 'cleanup_verified': True})
        next_lease.close()

    def test_wrong_approval_scope_digests_owner_and_uncertain_backend_refused(self):
        bad_cases = [{k: False} for k in ATTESTATIONS]
        bad_cases += [{k: ''} for k in ('approval_ref', 'exclusion_evidence_ref', 'backend_evidence_ref', 'gpu_evidence_ref')]
        bad_cases += [{k: 'wrong'} for k in ('old_scope', 'new_scope', 'old_config_sha256',
            'new_config_sha256', 'ownership_sha256', 'job_sha256')]
        for key in ('job_id', 'lease_id', 'request_hash'):
            bad_owner = dict(self.owner, **{key: 'incorrect'})
            bad_cases.append({'owner': bad_owner})
        bad_cases += [{'old_lock_identity': {}}, {'new_lock_identity': {}}]
        for changes in bad_cases:
            with self.subTest(changes=changes):
                with self.assertRaises((ValueError, RecoveryRequired)):
                    self.migrate(dict(self.evidence, **changes))
                self.assert_unchanged()

    def test_wrong_job_argument_or_unknown_execution_metadata_refused(self):
        with self.assertRaises(RecoveryRequired):
            migrate_physical_locks(self.old, self.new, 'different-job', self.evidence)
        self.gate.store.put_job({'request_id': 'unadmitted', 'state': 'waiting', 'target': 'wallpaper',
            'resources': ['gpu0'], 'request_hash': 'hash', 'unknown_backend_handle': 'uncertain'})
        path = self.gate.store.path('jobs/unadmitted.json')
        with self.assertRaises(RecoveryRequired):
            self.migrate(dict(self.evidence, pending_job_sha256={'unadmitted': digest(path)}))
        path.unlink()
        self.assert_unchanged()

    def test_active_job_other_nonfinal_job_or_other_owner_refused(self):
        original_job = self.job_path.read_bytes()
        self.gate.store.put_job(dict(strict_json(original_job), state='running'))
        with self.assertRaises(RecoveryRequired):
            self.migrate(dict(self.evidence, job_sha256=digest(self.job_path)))
        self.job_path.write_bytes(original_job)
        self.gate.store.put_job({'request_id': 'pending', 'state': 'waiting'})
        with self.assertRaises(RecoveryRequired):
            self.migrate()
        self.gate.store.path('jobs/pending.json').unlink()
        state = strict_json(self.baseline)
        state['owners']['gpu1'] = dict(self.owner)
        self.gate.store.write('ownership.json', state)
        with self.assertRaises(RecoveryRequired):
            self.migrate(dict(self.evidence, ownership_sha256=digest(self.gate.store.path('ownership.json'))))
        self.gate.store.path('ownership.json').write_bytes(self.baseline)
        self.assert_unchanged()

    def test_all_maintenance_journal_and_new_physical_locks_must_be_free(self):
        paths = [self.gate.store.path(n) for n in ('maintenance.lock', 'supervisor.lock', 'jobs.lock', 'admission.lock')]
        paths += list(self.new_paths.values())
        for path in paths:
            lock = NativeLock(path)
            self.assertTrue(lock.acquire(0))
            try:
                with self.assertRaises(RecoveryRequired):
                    self.migrate()
                self.assert_unchanged()
            finally:
                lock.close()

    def test_new_files_missing_replaced_linked_nonempty_or_bad_permissions_refused(self):
        path = self.new_paths['gpu1']
        retained = path.with_name('retained-original')
        path.rename(retained)
        for variant in ('missing', 'replacement', 'symlink', 'hardlink', 'nonempty', 'permissions'):
            with self.subTest(variant=variant):
                if variant == 'replacement':
                    path.touch(mode=0o600)
                elif variant == 'symlink':
                    path.symlink_to(retained)
                elif variant == 'hardlink':
                    os.link(retained, path)
                elif variant in ('nonempty', 'permissions'):
                    path.write_bytes(b'x' if variant == 'nonempty' else b'')
                    path.chmod(0o600 if variant == 'nonempty' else 0o644)
                with self.assertRaises((RecoveryRequired, ValueError)):
                    self.migrate()
                self.assert_unchanged()
                if path.exists() or path.is_symlink():
                    path.unlink()
        retained.rename(path)
        directory = path.parent
        directory.chmod(0o755)
        try:
            with self.assertRaises((RecoveryRequired, ValueError)):
                self.migrate()
        finally:
            directory.chmod(0o700)

    def test_old_file_reappearance_including_dangling_link_refused(self):
        path = self.old_paths['gpu0']
        for linked in (False, True):
            path.symlink_to(self.root / 'absent') if linked else path.touch()
            with self.assertRaises((RecoveryRequired, ValueError)):
                self.migrate()
            self.assert_unchanged()
            path.unlink()

    def test_changed_targets_or_arbitrary_destination_refused(self):
        original = self.new.read_bytes()
        for change in ('target', 'path'):
            config = strict_json(original)
            if change == 'target':
                config['targets']['meeting-dual-ha']['execution_timeout'] = 99
            else:
                config['lock_paths']['gpu0'] = str(self.root / 'different.lock')
            self.new.write_bytes(encode(config))
            with self.assertRaises(ValueError):
                self.migrate()
            self.assert_unchanged()
        self.new.write_bytes(original)

    def test_replaced_new_physical_identity_rejected_at_ordinary_startup(self):
        self.migrate()
        path = self.new_paths['gpu1']
        path.rename(path.with_name('old-retained-inode'))
        path.touch(mode=0o600)
        with self.assertRaisesRegex(RecoveryRequired, 'physical_lock_identity_changed'):
            admission_from_config(self.new)
        self.assertEqual(self.gate.store.read('ownership.json')['owners'], {'gpu0': self.owner})

    def test_replacement_during_physical_acquisition_refused(self):
        acquire = NativeLock.acquire
        def replace_after_acquire(lock, timeout=0):
            result = acquire(lock, timeout)
            if lock.path == self.new_paths['gpu1'] and result:
                lock.path.rename(lock.path.with_name('old-inode'))
                lock.path.touch(mode=0o600)
            return result
        with patch.object(NativeLock, 'acquire', replace_after_acquire), self.assertRaises(RecoveryRequired):
            self.migrate()
        self.assert_unchanged()

    def test_sigkill_before_replace_leaves_old_journal_and_resumable_migration(self):
        self.check_crash('before_replace', False)

    def test_sigkill_after_replace_leaves_new_journal_and_idempotent_fsync_resume(self):
        self.check_crash('after_replace', True)

    def test_sigkill_after_directory_fsync_preserves_single_audit_entry(self):
        self.check_crash('after_fsync', True)

    def check_crash(self, boundary, committed):
        process = multiprocessing.get_context('spawn').Process(target=crash_at_commit,
            args=(str(self.old), str(self.new), self.evidence, boundary))
        process.start()
        process.join(10)
        if process.is_alive():
            process.kill()
            process.join(5)
            self.fail('bounded crash fixture did not exit')
        self.assertEqual(process.exitcode, -signal.SIGKILL)
        current = self.gate.store.read('ownership.json')
        self.assertEqual(current['owners'], {'gpu0': self.owner})
        self.assertEqual(current['scope'], self.evidence['new_scope' if committed else 'old_scope'])
        for relative, contents in self.preserved.items():
            self.assertEqual((self.state_dir / relative).read_bytes(), contents)
        self.assertEqual(self.migrate()['already_recorded'], committed)
        self.assertEqual(len(self.gate.store.read('ownership.json')['physical_lock_migrations']), 1)

    def test_config_can_be_activated_before_or_after_journal_but_scope_mismatch_blocks_gap(self):
        active = self.root / 'active.json'
        active.write_bytes(self.new.read_bytes())  # Wrong order still refuses old scope.
        with self.assertRaises(RecoveryRequired):
            admission_from_config(active)
        active.write_bytes(self.old.read_bytes())
        self.migrate()
        with self.assertRaises(RecoveryRequired):
            admission_from_config(active)
        temporary = self.root / '.active.new'
        temporary.write_bytes(self.new.read_bytes())
        os.replace(temporary, active)
        _, _, gate = admission_from_config(active)
        self.assertEqual(gate.snapshot()['owners'], {'gpu0': self.owner})

    def test_existing_recover_cli_remains_separate_and_records_failed_not_success(self):
        self.migrate()
        evidence = dict(self.owner, approval_ref='synthetic-separate-recovery',
            backend_quiescent=True, cleanup_verified=True)
        _, _, gate = admission_from_config(self.new)
        with self.assertRaises(ValueError):
            gate.recover('retained', evidence)
        evidence['operator_approved_backend_recovery'] = True
        evidence_path = self.root / 'recovery.json'
        evidence_path.write_bytes(encode(evidence))
        result = subprocess.run([sys.executable, '-m', 'aihub_gpu_runner', '--config', str(self.new),
            'recover', '--job-id', 'retained', '--evidence-file', str(evidence_path)],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(gate.snapshot()['owners'], {})
        job = gate.store.job('retained')
        self.assertEqual(job['state'], 'failed')
        self.assertEqual(job['error_code'], 'operator_recovered_no_automatic_resubmission')
        self.assertIsNone(job['completion_verified'])
        self.assertEqual(gate.snapshot()['physical_lock_migrations'][-1]['job_sha256'],
                         self.evidence['job_sha256'])


if __name__ == '__main__':
    unittest.main()
