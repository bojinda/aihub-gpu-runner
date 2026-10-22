"""Disposable Linux files/locks only; no backend or GPU operations."""
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
from aihub_gpu_runner.core import AtomicStore, NativeLock, RecoveryRequired, encode, strict_json
from aihub_gpu_runner.scope_transition import WALLPAPER_ATTESTATIONS, transition_wallpaper_workflow
from tests.test_dual_gpu import fixture


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hold_native_lock(path, ready, release):
    lock = NativeLock(path, create=False)
    try:
        if lock.acquire(0):
            ready.set()
            release.wait(5)
    finally:
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
        transition_wallpaper_workflow(old, new, evidence)


@unittest.skipUnless(sys.platform.startswith('linux'), 'native Linux wallpaper workflow transition')
class WallpaperTransitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wallpaper-transition-')
        self.root = Path(self.tmp.name)
        policy = self.root / 'policy'
        policy.mkdir()
        _, complete, _, _, _ = fixture(policy)
        config = strict_json(complete.read_bytes())
        config['targets']['meeting-dual-ha'] = config['targets'].pop('meeting-dual')
        config['state_dir'] = str(self.root / 'state')
        config['lock_paths'] = {r: str(self.root / (r + '.lock')) for r in ('gpu0', 'gpu1')}
        for path in config['lock_paths'].values():
            Path(path).touch(mode=0o600)
        self.workflow = {
            '1': {'class_type': 'UNETLoader', 'inputs': {'unet_name': 'synthetic', 'weight_dtype': 'fp8_e4m3fn'}},
            '40': {'class_type': 'Resize', 'inputs': {'width': 100}},
            '7': {'class_type': 'QwenImageIntegratedKSampler',
                  'inputs': {'model': ['1', 0], 'image': ['40', 0], 'seed': 1}},
            '94': {'class_type': 'LayerUtility: PurgeVRAM', 'inputs': {'anything': ['7', 0]}},
            '95': {'class_type': 'LayerUtility: PurgeVRAM', 'inputs': {'anything': ['40', 0]}},
            '36': {'class_type': 'SaveImage', 'inputs': {'images': ['7', 0]}}}
        self.full = self.root / 'full.json'
        self.reduced = self.root / 'no95.json'
        self.full.write_bytes(encode(self.workflow))
        self.reduced.write_bytes(encode({k: v for k, v in self.workflow.items() if k != '95'}))
        config['targets']['wallpaper'] = {
            'kind': 'comfy', 'resources': ['gpu0'], 'base_url': 'http://127.0.0.1:1',
            'workflow_template': str(self.full), 'workflow_sha256': digest(self.full),
            'output_node': '36', 'idle_memory_mb': 1200, 'cleanup_samples': 3}
        config['targets']['music'] = {
            'kind': 'comfy', 'resources': ['gpu0'], 'base_url': 'http://127.0.0.1:2',
            'workflow': {'104': {'class_type': 'SaveAudioMP3', 'inputs': {}}},
            'output_node': '104', 'output_key': 'audio', 'media_type': 'audio/mpeg'}
        self.old = self.root / 'old.json'
        self.new = self.root / 'new.json'
        self.old.write_bytes(encode(config))
        candidate = copy.deepcopy(config)
        candidate['targets']['wallpaper'].update(workflow_template=str(self.reduced), workflow_sha256=digest(self.reduced))
        self.new.write_bytes(encode(candidate))
        _, _, self.gate = admission_from_config(self.old)
        self.gate.bootstrap({'approval_ref': 'synthetic-bootstrap', 'bootstrap_id': self.gate.snapshot()['bootstrap_id'],
                             'backend_quiescent': True, 'cleanup_verified': True})
        for number, state in enumerate(('success', 'failed', 'cancelled')):
            self.gate.store.put_job({'request_id': 'old-' + str(number), 'state': state, 'payload': {'synthetic': True}})
        self.gate.store.write('results/old-0.json', {'value': 'preserved'})
        state = self.gate.snapshot()
        state.update(recoveries=[{'approval_ref': 'earlier-recovery'}],
                     physical_lock_migrations=[{'approval_ref': 'earlier-lock-migration'}])
        self.gate.store.write('ownership.json', state)
        self.baseline = self.gate.store.path('ownership.json').read_bytes()
        self.preserved = {str(p.relative_to(self.gate.store.root)): p.read_bytes()
                          for directory in ('jobs', 'results') for p in self.gate.store.path(directory).glob('*')}
        self.evidence = self.make_evidence(self.old, self.new, 'synthetic-forward')

    def tearDown(self):
        self.tmp.cleanup()

    def make_evidence(self, old, new, approval):
        return dict(approval_ref=approval, old_scope=load_config(old)[-1], new_scope=load_config(new)[-1],
            old_config_sha256=digest(old), new_config_sha256=digest(new),
            old_workflow_sha256=load_config(old)[0]['targets']['wallpaper']['workflow_sha256'],
            new_workflow_sha256=load_config(new)[0]['targets']['wallpaper']['workflow_sha256'],
            ownership_sha256=digest(self.gate.store.path('ownership.json')),
            exclusion_evidence_ref='synthetic-exclusion', backend_evidence_ref='synthetic-idle-backends',
            **{k: True for k in WALLPAPER_ATTESTATIONS})

    def transition(self, evidence=None):
        return transition_wallpaper_workflow(self.old, self.new, self.evidence if evidence is None else evidence)

    def assert_preserved(self):
        for name, data in self.preserved.items():
            self.assertEqual(self.gate.store.path(name).read_bytes(), data)
        before = strict_json(self.baseline)
        after = self.gate.store.read('ownership.json')
        for key in before:
            if key not in ('scope', 'scope_transitions'):
                self.assertEqual(before[key], after[key], key)

    def test_transition_preserves_history_lock_identities_and_other_targets_without_activating_config(self):
        config_bytes = self.old.read_bytes(), self.new.read_bytes()
        self.assertTrue(self.transition()['journal_preserved'])
        self.assertEqual(config_bytes, (self.old.read_bytes(), self.new.read_bytes()))
        self.assert_preserved()
        with self.assertRaises(RecoveryRequired):
            admission_from_config(self.old)
        _, targets, gate = admission_from_config(self.new)
        self.assertEqual(gate.snapshot()['owners'], {})
        self.assertIn('meeting-dual-ha', targets)
        self.assertIn('music', targets)
        self.assertEqual(targets['wallpaper'].workflow, {k: v for k, v in self.workflow.items() if k != '95'})
        self.assertEqual(len(gate.snapshot()['scope_transitions']), 1)

    def test_repeat_requires_identical_evidence_and_never_appends_twice(self):
        self.transition()
        self.assertTrue(self.transition()['already_recorded'])
        for key, value in [('approval_ref', 'different'), ('backend_evidence_ref', 'different'),
                           ('ownership_sha256', '0' * 64)]:
            with self.subTest(key=key), self.assertRaises(RecoveryRequired):
                self.transition(dict(self.evidence, **{key: value}))
        self.assertEqual(len(self.gate.store.read('ownership.json')['scope_transitions']), 1)

    def test_guarded_rollback_and_repeat_restore_only_the_previous_scope(self):
        self.transition()
        reverse = self.make_evidence(self.new, self.old, 'synthetic-rollback')
        reverse['rollback_of'] = self.evidence['approval_ref']
        transition_wallpaper_workflow(self.new, self.old, reverse, rollback=True)
        self.assertTrue(transition_wallpaper_workflow(self.new, self.old, reverse, rollback=True)['already_recorded'])
        _, _, gate = admission_from_config(self.old)
        self.assertEqual(len(gate.snapshot()['scope_transitions']), 2)
        self.assert_preserved()
        reused = dict(self.evidence, ownership_sha256=digest(self.gate.store.path('ownership.json')))
        with self.assertRaisesRegex(ValueError, 'fresh_approval'):
            self.transition(reused)

    def test_rollback_requires_exact_immediately_previous_forward_transition(self):
        self.transition()
        reverse = self.make_evidence(self.new, self.old, 'synthetic-rollback')
        with self.assertRaises(ValueError):
            transition_wallpaper_workflow(self.new, self.old, reverse, rollback=True)
        state = self.gate.store.read('ownership.json')
        state['scope_transitions'].append({'kind': 'another-transition', 'approval_ref': 'later'})
        self.gate.store.write('ownership.json', state)
        reverse = self.make_evidence(self.new, self.old, 'synthetic-rollback')
        reverse['rollback_of'] = self.evidence['approval_ref']
        with self.assertRaises(ValueError):
            transition_wallpaper_workflow(self.new, self.old, reverse, rollback=True)

    def test_other_graph_model_seed_type_or_dangling_link_changes_are_rejected(self):
        original_config, original_workflow = self.new.read_bytes(), self.reduced.read_bytes()
        mutations = [lambda w: w.pop('94'),
                     lambda w: w['1']['inputs'].update(unet_name='different-model'),
                     lambda w: w['7']['inputs'].update(seed=True),
                     lambda w: w['7']['inputs'].update(image=['95', 0]),
                     lambda w: w.update(extra={'class_type': 'Other', 'inputs': {}})]
        for change in mutations:
            with self.subTest(change=mutations.index(change)):
                candidate = strict_json(original_workflow)
                change(candidate)
                self.reduced.write_bytes(encode(candidate))
                config = strict_json(original_config)
                config['targets']['wallpaper']['workflow_sha256'] = digest(self.reduced)
                self.new.write_bytes(encode(config))
                with self.assertRaises(ValueError):
                    self.transition(self.make_evidence(self.old, self.new, 'invalid'))
                self.assertEqual(self.gate.store.path('ownership.json').read_bytes(), self.baseline)
        self.new.write_bytes(original_config)
        self.reduced.write_bytes(original_workflow)
        full = copy.deepcopy(self.workflow)
        full['95']['class_type'] = 'OtherCleanup'
        self.full.write_bytes(encode(full))
        config = strict_json(self.old.read_bytes())
        config['targets']['wallpaper']['workflow_sha256'] = digest(self.full)
        self.old.write_bytes(encode(config))
        with self.assertRaisesRegex(ValueError, 'purge95'):
            self.transition(self.make_evidence(self.old, self.new, 'invalid-type'))

    def test_nonwallpaper_settings_storage_and_wallpaper_settings_cannot_change(self):
        original = self.new.read_bytes()
        changes = [lambda c: c['targets']['ollama'].update(models=['different']),
                   lambda c: c['targets']['music'].update(base_url='http://127.0.0.1:3'),
                   lambda c: c['targets']['meeting-dual-ha'].update(execution_timeout=99),
                   lambda c: c['targets']['wallpaper'].update(idle_memory_mb=9999),
                   lambda c: c.update(state_dir=str(self.root / 'other-state')),
                   lambda c: c['lock_paths'].update(gpu1=str(self.root / 'other.lock'))]
        for change in changes:
            config = strict_json(original)
            change(config)
            self.new.write_bytes(encode(config))
            with self.subTest(change=changes.index(change)), self.assertRaises(ValueError):
                self.transition(self.make_evidence(self.old, self.new, 'invalid'))
            self.assertEqual(self.gate.store.path('ownership.json').read_bytes(), self.baseline)

    def test_exact_approval_scopes_hashes_and_all_attestations_are_required(self):
        changes = [(key, False) for key in WALLPAPER_ATTESTATIONS]
        changes += [(key, 'wrong') for key in ['old_scope', 'new_scope', 'old_config_sha256',
                    'new_config_sha256', 'old_workflow_sha256', 'new_workflow_sha256', 'ownership_sha256']]
        changes += [('approval_ref', ' '), ('backend_evidence_ref', ''), ('exclusion_evidence_ref', '')]
        for key, value in changes:
            with self.subTest(key=key), self.assertRaises((ValueError, RecoveryRequired)):
                self.transition(dict(self.evidence, **{key: value}))
            self.assertEqual(self.gate.store.path('ownership.json').read_bytes(), self.baseline)

    def test_any_owner_or_nonfinal_job_blocks_transition(self):
        for resource in ('gpu0', 'gpu1'):
            lease = self.gate.try_acquire('busy', 'synthetic-hash', (resource,), 'synthetic')
            try:
                before = self.gate.store.path('ownership.json').read_bytes()
                with self.assertRaisesRegex(RecoveryRequired, 'resolved_idle'):
                    self.transition()
                self.assertEqual(self.gate.store.path('ownership.json').read_bytes(), before)
            finally:
                lease.release({'completion_verified': True, 'cleanup_verified': True})
                lease.close()
        for status in ('waiting', 'running', 'reconciling', 'cleanup_pending'):
            self.gate.store.put_job({'request_id': 'nonfinal', 'state': status})
            with self.subTest(state=status), self.assertRaisesRegex(RecoveryRequired, 'resolved_idle'):
                self.transition()
        self.gate.store.path('jobs/nonfinal.json').unlink()

    def test_maintenance_supervisor_journal_and_physical_locks_must_all_be_available(self):
        paths = [self.gate.store.path(n) for n in ('maintenance.lock', 'supervisor.lock', 'jobs.lock', 'admission.lock')]
        paths += list(self.gate.lock_paths.values())
        for path in paths:
            lock = NativeLock(path)
            self.assertTrue(lock.acquire(0))
            try:
                with self.subTest(path=path.name), self.assertRaises(RecoveryRequired):
                    self.transition()
                self.assertEqual(self.gate.store.path('ownership.json').read_bytes(), self.baseline)
            finally:
                lock.close()

    def test_external_process_supervisor_and_gpu_lock_contention_is_rejected(self):
        context = multiprocessing.get_context('fork')
        for path in (self.gate.store.path('supervisor.lock'), self.gate.lock_paths['gpu1']):
            path.touch(exist_ok=True)
            ready, release = context.Event(), context.Event()
            child = context.Process(target=hold_native_lock, args=(path, ready, release))
            child.start()
            try:
                self.assertTrue(ready.wait(2))
                with self.subTest(path=path.name), self.assertRaises(RecoveryRequired):
                    self.transition()
                self.assertEqual(self.gate.store.path('ownership.json').read_bytes(), self.baseline)
            finally:
                release.set()
                child.join(3)
            self.assertEqual(child.exitcode, 0)

    def test_missing_or_changed_physical_identity_fails_closed(self):
        path = self.gate.lock_paths['gpu0']
        saved = path.with_suffix('.saved')
        path.rename(saved)
        for replaced in (False, True):
            if replaced:
                path.touch(mode=0o600)
            with self.subTest(replaced=replaced), self.assertRaises(RecoveryRequired):
                self.transition()
            self.assertEqual(self.gate.store.path('ownership.json').read_bytes(), self.baseline)
        path.unlink()
        saved.rename(path)
        path.chmod(0o644)
        with self.assertRaises(RecoveryRequired):
            self.transition()

    def test_replacement_between_precheck_and_open_is_rejected_at_descriptor(self):
        acquire = NativeLock.acquire
        physical = self.gate.lock_paths['gpu0']
        def replace_before(lock, timeout=0):
            if lock.path == physical:
                physical.rename(physical.with_suffix('.saved'))
                physical.touch(mode=0o600)
            return acquire(lock, timeout)
        with patch.object(NativeLock, 'acquire', replace_before), self.assertRaisesRegex(RecoveryRequired, 'during_acquisition'):
            self.transition()
        self.assertEqual(self.gate.store.path('ownership.json').read_bytes(), self.baseline)

    def test_input_or_path_change_after_acquisition_prevents_commit(self):
        acquire = NativeLock.acquire
        for changed in ('config', 'workflow', 'path'):
            original_config, original_workflow = self.new.read_bytes(), self.reduced.read_bytes()
            physical = self.gate.lock_paths['gpu1']
            def change_after(lock, timeout=0):
                result = acquire(lock, timeout)
                if result and lock.path == physical:
                    if changed == 'config':
                        self.new.write_bytes(original_config + b'\n')
                    elif changed == 'workflow':
                        self.reduced.write_bytes(original_workflow + b'\n')
                    else:
                        physical.rename(physical.with_suffix('.saved'))
                        physical.touch(mode=0o600)
                return result
            with self.subTest(changed=changed), patch.object(NativeLock, 'acquire', change_after), self.assertRaises((ValueError, RecoveryRequired)):
                self.transition()
            self.assertEqual(self.gate.store.path('ownership.json').read_bytes(), self.baseline)
            self.new.write_bytes(original_config)
            self.reduced.write_bytes(original_workflow)
            if changed == 'path':
                physical.unlink()
                physical.with_suffix('.saved').rename(physical)

    def test_sigkill_at_atomic_commit_boundaries_is_retryable_without_duplicate_audit(self):
        # Separate fixture per boundary, with real process termination releasing flocks.
        for boundary in ('before_replace', 'after_replace', 'after_fsync'):
            if boundary != 'before_replace':
                self.tearDown()
                self.setUp()
            child = multiprocessing.get_context('fork').Process(target=crash_at_commit,
                args=(self.old, self.new, self.evidence, boundary))
            child.start()
            child.join(5)
            self.assertFalse(child.is_alive())
            self.assertEqual(child.exitcode, -signal.SIGKILL)
            outcome = self.transition()
            self.assertEqual(outcome['already_recorded'], boundary != 'before_replace')
            self.assertEqual(len(self.gate.store.read('ownership.json')['scope_transitions']), 1)
            self.assert_preserved()

    def test_missing_journal_is_not_bootstrapped_and_cli_dispatch_is_supported(self):
        evidence_path = self.root / 'evidence.json'
        evidence_path.write_bytes(encode(self.evidence))
        run = subprocess.run([sys.executable, '-B', '-m', 'aihub_gpu_runner', '--config', str(self.old),
            'transition-wallpaper-workflow', '--new-config', str(self.new), '--evidence-file', str(evidence_path)],
            capture_output=True, text=True, timeout=5)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(strict_json(run.stdout)['journal_preserved'])
        self.gate.store.path('ownership.json').unlink()
        with self.assertRaises(RecoveryRequired):
            self.transition()
        self.assertFalse(self.gate.store.path('ownership.json').exists())


if __name__ == '__main__':
    unittest.main()
