"""Exact conditioning transition; independent synthetic files and native Linux locks."""
import copy
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from aihub_gpu_runner.config import admission_from_config, load_config
from aihub_gpu_runner.core import AtomicStore, RecoveryRequired, encode, strict_json
from aihub_gpu_runner.scope_transition import CONDITIONING_ATTESTATIONS, transition_wallpaper_conditioning
from tests import test_wallpaper_transition as prior
from tests.test_dual_gpu import fixture
from tests.test_wallpaper_conditioning import GPU0, GPU1


def crash_conditioning(old, new, evidence, boundary, rollback):
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
        transition_wallpaper_conditioning(old, new, evidence, rollback=rollback)


@unittest.skipUnless(sys.platform.startswith('linux'), 'native Linux conditioning scope transition')
class ConditioningTransitionTests(prior.WallpaperTransitionTests):
    # Inherit the existing adversarial lock, retained-owner, journal, fd-race
    # and frozen-input tests, exercising THIS command via the overridden fixture.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='conditioning-transition-')
        self.root = Path(self.tmp.name)
        policy = self.root / 'policy'
        policy.mkdir()
        _, complete, _, _, _ = fixture(policy)
        config = strict_json(complete.read_bytes())
        config['targets']['meeting-dual-ha'] = config['targets'].pop('meeting-dual')
        config['targets']['meeting-dual-ha']['mode_switch']['gpu_uuids'] = {'gpu0': GPU0, 'gpu1': GPU1}
        config['state_dir'] = str(self.root / 'state')
        config['lock_paths'] = {r: str(self.root / (r + '.lock')) for r in ('gpu0', 'gpu1')}
        for path in config['lock_paths'].values():
            Path(path).touch(mode=0o600)
        self.workflow = {
            '1': {'class_type': 'UNETLoader', 'inputs': {'unet_name': 'synthetic', 'weight_dtype': 'fp8_e4m3fn'}},
            '3': {'class_type': 'CLIPLoader', 'inputs': {
                'clip_name': 'qwen_2.5_vl_7b_fp8_scaled.safetensors', 'type': 'qwen_image', 'device': 'cpu'}},
            '40': {'class_type': 'Resize', 'inputs': {'width': 100}},
            '7': {'class_type': 'QwenImageIntegratedKSampler',
                  'inputs': {'model': ['1', 0], 'clip': ['3', 0], 'seed': 1, 'steps': 4}},
            '94': {'class_type': 'LayerUtility: PurgeVRAM', 'inputs': {'anything': ['7', 0]}},
            '36': {'class_type': 'SaveImage', 'inputs': {'images': ['7', 0]}}}
        dual = copy.deepcopy(self.workflow)
        dual['3'].update(class_type='AIHubQwenGPU1CLIPLoader', inputs={
            'clip_name': self.workflow['3']['inputs']['clip_name'], 'gpu0_uuid': GPU0, 'gpu1_uuid': GPU1})
        self.full, self.reduced = self.root / 'cpu-workflow.json', self.root / 'dual-workflow.json'
        self.full.write_bytes(encode(self.workflow))
        self.reduced.write_bytes(encode(dual))
        config['targets']['wallpaper'] = {
            'kind': 'comfy', 'resources': ['gpu0'], 'base_url': 'http://127.0.0.1:1',
            'workflow_template': str(self.full), 'workflow_sha256': prior.digest(self.full),
            'output_node': '36', 'idle_memory_mb': 1200, 'cleanup_samples': 3,
            'execution_timeout': 900, 'cleanup_timeout': 60}
        config['targets']['music'] = {
            'kind': 'comfy', 'resources': ['gpu0'], 'base_url': 'http://127.0.0.1:2',
            'workflow': {'104': {'class_type': 'SaveAudioMP3', 'inputs': {}}},
            'output_node': '104', 'output_key': 'audio', 'media_type': 'audio/mpeg'}
        self.old, self.new = self.root / 'cpu-config.json', self.root / 'dual-config.json'
        self.old.write_bytes(encode(config))
        candidate = copy.deepcopy(config)
        candidate['targets']['wallpaper'].update(resources=['gpu0', 'gpu1'], conditioning_idle_memory_mb=512,
            workflow_template=str(self.reduced), workflow_sha256=prior.digest(self.reduced))
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
        self.evidence = self.make_evidence(self.old, self.new, 'synthetic-conditioning-forward')

    def make_evidence(self, old, new, approval):
        ov, ot, _, _, oscope = load_config(old)
        nv, nt, _, _, nscope = load_config(new)
        dual = (ot if ot['wallpaper'].resources == ('gpu0', 'gpu1') else nt)['wallpaper']
        return dict(approval_ref=approval, old_scope=oscope, new_scope=nscope,
            old_config_sha256=prior.digest(old), new_config_sha256=prior.digest(new),
            old_workflow_sha256=ov['targets']['wallpaper']['workflow_sha256'],
            new_workflow_sha256=nv['targets']['wallpaper']['workflow_sha256'],
            ownership_sha256=prior.digest(self.gate.store.path('ownership.json')),
            exclusion_evidence_ref='synthetic-exclusion', backend_evidence_ref='synthetic-idle-backends',
            workflow_staging_evidence_ref='synthetic-media-and-aihub-byte-check',
            calibration_evidence_ref='synthetic-only-not-a-live-calibration',
            physical_gpu_uuids=dual.conditioning_gpu_uuids(),
            calibrated_memory_mb={'gpu0': dual.idle_memory_mb, 'gpu1': dual.conditioning_idle_memory_mb},
            media_workflow_sha256=nv['targets']['wallpaper']['workflow_sha256'],
            **{k: True for k in CONDITIONING_ATTESTATIONS})

    def transition(self, evidence=None):
        return transition_wallpaper_conditioning(self.old, self.new, self.evidence if evidence is None else evidence)

    def test_transition_preserves_history_lock_identities_and_other_targets_without_activating_config(self):
        configs = self.old.read_bytes(), self.new.read_bytes()
        with patch('aihub_gpu_runner.backends.HTTPTransport._request', side_effect=AssertionError('no backend action')), \
             patch('aihub_gpu_runner.backends.NvidiaUUIDMemoryProbe.__call__', side_effect=AssertionError('no GPU probe')):
            self.assertTrue(self.transition()['journal_preserved'])
        self.assertEqual(configs, (self.old.read_bytes(), self.new.read_bytes()))
        self.assert_preserved()
        with self.assertRaises(RecoveryRequired):
            admission_from_config(self.old)
        _, targets, gate = admission_from_config(self.new)
        self.assertEqual(gate.snapshot()['owners'], {})
        self.assertIn('meeting-dual-ha', targets)
        self.assertIn('music', targets)
        self.assertEqual(targets['wallpaper'].resources, ('gpu0', 'gpu1'))
        self.assertEqual(gate.snapshot()['scope_transitions'][-1]['kind'], 'wallpaper_gpu1_conditioning')

    def test_guarded_rollback_and_repeat_restore_only_the_previous_scope(self):
        self.transition()
        reverse = self.make_evidence(self.new, self.old, 'synthetic-conditioning-rollback')
        reverse['rollback_of'] = self.evidence['approval_ref']
        transition_wallpaper_conditioning(self.new, self.old, reverse, rollback=True)
        self.assertTrue(transition_wallpaper_conditioning(self.new, self.old, reverse, rollback=True)['already_recorded'])
        self.assertEqual(len(admission_from_config(self.old)[2].snapshot()['scope_transitions']), 2)
        self.assert_preserved()
        reused = dict(self.evidence, ownership_sha256=prior.digest(self.gate.store.path('ownership.json')))
        with self.assertRaisesRegex(ValueError, 'fresh_approval'):
            self.transition(reused)

    def test_rollback_requires_exact_immediately_previous_forward_transition(self):
        self.transition()
        reverse = self.make_evidence(self.new, self.old, 'synthetic-rollback')
        with self.assertRaisesRegex(ValueError, 'exact_previous'):
            transition_wallpaper_conditioning(self.new, self.old, reverse, rollback=True)
        reverse['rollback_of'] = self.evidence['approval_ref']
        state = self.gate.store.read('ownership.json')
        state['scope_transitions'].append({'kind': 'another-transition', 'approval_ref': 'later'})
        self.gate.store.write('ownership.json', state)
        reverse['ownership_sha256'] = prior.digest(self.gate.store.path('ownership.json'))
        with self.assertRaisesRegex(ValueError, 'exact_previous'):
            transition_wallpaper_conditioning(self.new, self.old, reverse, rollback=True)

    def test_other_graph_model_seed_type_or_dangling_link_changes_are_rejected(self):
        original_config, original_workflow = self.new.read_bytes(), self.reduced.read_bytes()
        changes = [lambda w: w.pop('94'), lambda w: w['1']['inputs'].update(unet_name='different'),
            lambda w: w['7']['inputs'].update(seed=True), lambda w: w['7']['inputs'].update(steps=6),
            lambda w: w.update(extra={'class_type': 'Other', 'inputs': {}}),
            lambda w: w['3'].update(_meta={'title': 'unexpected'}),
            lambda w: w['3']['inputs'].update(gpu0_uuid=GPU1),
            lambda w: w['3']['inputs'].update(gpu1_uuid='1')]
        for index, change in enumerate(changes):
            candidate = strict_json(original_workflow); change(candidate)
            self.reduced.write_bytes(encode(candidate))
            config = strict_json(original_config)
            config['targets']['wallpaper']['workflow_sha256'] = prior.digest(self.reduced)
            self.new.write_bytes(encode(config))
            with self.subTest(change=index), self.assertRaises(ValueError):
                self.transition(self.make_evidence(self.old, self.new, 'invalid'))
            self.assertEqual(self.gate.store.path('ownership.json').read_bytes(), self.baseline)
        self.new.write_bytes(original_config); self.reduced.write_bytes(original_workflow)
        cpu = copy.deepcopy(self.workflow); cpu['3']['inputs']['device'] = 'default'
        self.full.write_bytes(encode(cpu))
        config = strict_json(self.old.read_bytes())
        config['targets']['wallpaper']['workflow_sha256'] = prior.digest(self.full)
        self.old.write_bytes(encode(config))
        with self.assertRaisesRegex(ValueError, 'approved_cpu_wallpaper'):
            self.transition(self.make_evidence(self.old, self.new, 'invalid-cpu-device'))

    def test_exact_approval_scopes_hashes_and_all_attestations_are_required(self):
        changes = [(k, False) for k in CONDITIONING_ATTESTATIONS]
        changes += [(k, 'wrong') for k in ('old_scope', 'new_scope', 'old_config_sha256',
            'new_config_sha256', 'old_workflow_sha256', 'new_workflow_sha256', 'ownership_sha256',
            'media_workflow_sha256')]
        changes += [(k, '') for k in ('approval_ref', 'backend_evidence_ref', 'exclusion_evidence_ref',
            'workflow_staging_evidence_ref', 'calibration_evidence_ref')]
        changes += [('physical_gpu_uuids', {'gpu0': GPU1, 'gpu1': GPU0}),
                    ('calibrated_memory_mb', {'gpu0': 1200, 'gpu1': 99999})]
        for key, value in changes:
            with self.subTest(key=key), self.assertRaises((ValueError, RecoveryRequired)):
                self.transition(dict(self.evidence, **{key: value}))
            self.assertEqual(self.gate.store.path('ownership.json').read_bytes(), self.baseline)

    def test_sigkill_at_atomic_commit_boundaries_is_retryable_without_duplicate_audit(self):
        for rollback in (False, True):
            for boundary in ('before_replace', 'after_replace', 'after_fsync'):
                self.tearDown(); self.setUp()
                old, new, evidence = self.old, self.new, self.evidence
                if rollback:
                    self.transition()
                    old, new = self.new, self.old
                    evidence = self.make_evidence(old, new, 'synthetic-rollback')
                    evidence['rollback_of'] = self.evidence['approval_ref']
                configs = old.read_bytes(), new.read_bytes()
                child = multiprocessing.get_context('fork').Process(target=crash_conditioning,
                    args=(old, new, evidence, boundary, rollback))
                child.start(); child.join(5)
                self.assertFalse(child.is_alive())
                self.assertEqual(child.exitcode, -signal.SIGKILL)
                if boundary != 'before_replace':
                    with self.assertRaises(RecoveryRequired):
                        admission_from_config(old)
                outcome = transition_wallpaper_conditioning(old, new, evidence, rollback=rollback)
                self.assertEqual(outcome['already_recorded'], boundary != 'before_replace')
                self.assertEqual(len(self.gate.store.read('ownership.json')['scope_transitions']), 2 if rollback else 1)
                self.assertEqual(configs, (old.read_bytes(), new.read_bytes()))
                self.assert_preserved()

    def test_journal_commit_does_not_make_separate_config_activation_atomic(self):
        active = self.root / 'config.json'; active.write_bytes(self.old.read_bytes())
        self.transition()
        with self.assertRaises(RecoveryRequired):
            admission_from_config(active)
        self.assertTrue(self.transition()['already_recorded'])
        replacement = self.root / 'active.tmp'; replacement.write_bytes(self.new.read_bytes())
        os.replace(replacement, active)
        self.assertEqual(admission_from_config(active)[2].snapshot()['scope'], self.evidence['new_scope'])

    def test_lf_and_crlf_staged_workflows_require_their_own_exact_hashes(self):
        workflow = strict_json(self.reduced.read_bytes())
        lf = (json.dumps(workflow, indent=2) + '\n').encode()
        crlf = lf.replace(b'\n', b'\r\n')
        self.assertNotEqual(hashlib.sha256(lf).hexdigest(), hashlib.sha256(crlf).hexdigest())
        self.reduced.write_bytes(lf)
        config = strict_json(self.new.read_bytes())
        config['targets']['wallpaper']['workflow_sha256'] = prior.digest(self.reduced)
        self.new.write_bytes(encode(config))
        evidence_lf = self.make_evidence(self.old, self.new, 'lf-staging')
        self.reduced.write_bytes(crlf)
        with self.assertRaisesRegex(ValueError, 'workflow_template_hash_mismatch'):
            self.transition(evidence_lf)
        config['targets']['wallpaper']['workflow_sha256'] = prior.digest(self.reduced)
        self.new.write_bytes(encode(config))
        evidence_crlf = self.make_evidence(self.old, self.new, 'crlf-staging')
        with self.assertRaisesRegex(ValueError, 'exact_operator_evidence'):
            self.transition(dict(evidence_crlf, media_workflow_sha256=evidence_lf['media_workflow_sha256']))
        self.assertTrue(self.transition(evidence_crlf)['journal_preserved'])

    def test_rollback_with_retained_dual_owner_is_refused_even_when_native_locks_are_free(self):
        self.transition()
        gate = admission_from_config(self.new)[2]
        lease = gate.try_acquire('unresolved-trial', 'synthetic-request', ('gpu0', 'gpu1'), 'wallpaper')
        lease.mark('reconciling'); lease.close()
        before = gate.store.path('ownership.json').read_bytes()
        evidence = self.make_evidence(self.new, self.old, 'synthetic-rollback')
        evidence['rollback_of'] = self.evidence['approval_ref']
        with self.assertRaisesRegex(RecoveryRequired, 'resolved_idle'):
            transition_wallpaper_conditioning(self.new, self.old, evidence, rollback=True)
        self.assertEqual(gate.store.path('ownership.json').read_bytes(), before)

    def test_new_workflow_cannot_remap_the_existing_physical_resource_contract(self):
        workflow = strict_json(self.reduced.read_bytes())
        workflow['3']['inputs'].update(gpu0_uuid=GPU1, gpu1_uuid=GPU0)
        self.reduced.write_bytes(encode(workflow))
        config = strict_json(self.new.read_bytes())
        config['targets']['wallpaper']['workflow_sha256'] = prior.digest(self.reduced)
        self.new.write_bytes(encode(config))
        with self.assertRaisesRegex(ValueError, 'existing_resource_contract'):
            load_config(self.new)
        self.assertEqual(self.gate.store.path('ownership.json').read_bytes(), self.baseline)

    def test_directory_fsync_failure_after_commit_requires_exact_repeat_to_complete(self):
        for rollback in (False, True):
            self.tearDown(); self.setUp()
            old, new, evidence = self.old, self.new, self.evidence
            if rollback:
                self.transition()
                old, new = self.new, self.old
                evidence = self.make_evidence(old, new, 'synthetic-rollback')
                evidence['rollback_of'] = self.evidence['approval_ref']
            fsync = os.fsync
            def fail_directory(fd):
                if stat.S_ISDIR(os.fstat(fd).st_mode):
                    raise OSError('synthetic directory fsync failure')
                return fsync(fd)
            with patch('os.fsync', fail_directory), self.assertRaises(OSError):
                transition_wallpaper_conditioning(old, new, evidence, rollback=rollback)
            with self.assertRaises(RecoveryRequired):
                admission_from_config(old)
            self.assertTrue(transition_wallpaper_conditioning(old, new, evidence, rollback=rollback)['already_recorded'])
            self.assertEqual(len(self.gate.store.read('ownership.json')['scope_transitions']), 2 if rollback else 1)
            self.assert_preserved()

    def test_missing_journal_is_not_bootstrapped_and_cli_dispatch_is_supported(self):
        evidence_path = self.root / 'evidence.json'; evidence_path.write_bytes(encode(self.evidence))
        result = subprocess.run([sys.executable, '-B', '-m', 'aihub_gpu_runner', '--config', str(self.old),
            'transition-wallpaper-conditioning', '--new-config', str(self.new), '--evidence-file', str(evidence_path)],
            capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(strict_json(result.stdout)['journal_preserved'])
        self.gate.store.path('ownership.json').unlink()
        with self.assertRaises(RecoveryRequired):
            self.transition()
        self.assertFalse(self.gate.store.path('ownership.json').exists())


if __name__ == '__main__':
    unittest.main()
