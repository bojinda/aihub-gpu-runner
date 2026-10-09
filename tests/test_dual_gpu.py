"""Synthetic CDI switching, whole-session reservations and native Linux recovery."""
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from aihub_gpu_runner.backends import Target, CleanupPending, Uncertain
from aihub_gpu_runner.config import admission_from_config, load_config, make_runner
from aihub_gpu_runner.core import NativeLock, RecoveryRequired, AtomicStore, encode, Conflict
from aihub_gpu_runner.dual_gpu import (ComposeOllamaSwitch, preserved_config, switch_binding,
    restore_held, validate_policy)
from aihub_gpu_runner.host_stage import HostStage, HostOllamaClient, Interrupted
from aihub_gpu_runner.scope_transition import transition_scope


class FakeTransport:
    def __init__(self, error=None):
        self.calls, self.error = [], error
    def json(self, method, route, payload=None, timeout=5):
        self.calls.append((method, route, payload))
        if route == '/api/version':
            return {'version': '0.35.1'}
        if route == '/api/ps':
            return {'models': []}
        if self.error and (payload.get('keep_alive') == 0 or self.error == 'response'):
            raise Uncertain('synthetic_lost_response')
        return {'done': True, 'response': 'synthetic'}


def fixture(root):
    runtime = {'mounts': [{'Type': 'volume', 'Name': 'synthetic-models'}],
               'ports': {'11434/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '11434'}]},
               'restart': {'Name': 'unless-stopped', 'MaximumRetryCount': 0},
               'flash_attention': 'true', 'kv_cache': 'q8_0'}
    configs = {}
    for mode, indices in [('gpu1', ('1',)), ('dual', ('0', '1'))]:
        configs[mode] = {'name': 'synthetic', 'services': {'ollama': {
            'image': 'synthetic/ollama@sha256:' + 'a' * 64, 'container_name': 'synthetic',
            'environment': {'OLLAMA_FLASH_ATTENTION': 'true', 'OLLAMA_KV_CACHE_TYPE': 'q8_0'},
            'volumes': [{'type': 'volume', 'source': 'models', 'target': '/root/.ollama'}],
            'ports': [{'target': 11434, 'published': '11434'}], 'restart': 'unless-stopped',
            'devices': [{'source': 'nvidia.com/gpu=' + i, 'target': 'nvidia.com/gpu=' + i,
                         'permissions': 'rwm'} for i in indices]}}}
        (root / (mode + '.json')).write_bytes(encode(configs[mode]))
    policy = {'docker': '/synthetic/docker', 'nvidia_smi': '/synthetic/nvidia-smi',
        'project': 'synthetic', 'project_directory': str(root), 'service': 'ollama',
        'container': 'synthetic', 'gpu1_compose': str(root / 'gpu1.json'),
        'dual_compose': str(root / 'dual.json'),
        'gpu1_sha256': hashlib.sha256(encode(configs['gpu1'])).hexdigest(),
        'dual_sha256': hashlib.sha256(encode(configs['dual'])).hexdigest(),
        'preserved_config_sha256': preserved_config(configs['gpu1'], 'ollama'),
        'runtime_contract_sha256': hashlib.sha256(encode(runtime)).hexdigest(),
        'image_id': 'sha256:' + 'a' * 64, 'compose_version': '5.0.0', 'docker_version': '29.1.3',
        'ollama_version': '0.35.1', 'gpu_uuids': {'gpu0': 'GPU-zero', 'gpu1': 'GPU-one'},
        'idle_memory_mb': {'gpu0': 2, 'gpu1': 2}, 'cleanup_samples': 2,
        'switch_timeout': .15, 'activation_approval_ref': 'synthetic-window'}
    target = {'kind': 'ollama', 'resources': ['gpu0', 'gpu1'],
        'base_url': 'http://127.0.0.1:1', 'models': ['synthetic-map', 'qwen3.8:27b'],
        'max_context': 196608, 'cleanup_timeout': .1, 'mode_switch': policy}
    old = {'state_dir': str(root / 'state'),
        'lock_paths': {r: str(root / (r + '.lock')) for r in ('gpu0', 'gpu1')},
        'targets': {'ollama': {'kind': 'ollama', 'resources': ['gpu1'],
            'base_url': 'http://127.0.0.1:1', 'models': ['synthetic-map']}}}
    for path in old['lock_paths'].values():
        Path(path).touch()
    old_path, new_path = root / 'old.json', root / 'new.json'
    old_path.write_bytes(encode(old))
    new = copy.deepcopy(old)
    new['targets']['meeting-dual'] = target
    new_path.write_bytes(encode(new))
    _, _, admission = admission_from_config(old_path)
    admission.bootstrap({'approval_ref': 'synthetic bootstrap', 'bootstrap_id': admission.snapshot()['bootstrap_id'],
                         'backend_quiescent': True, 'cleanup_verified': True})
    evidence = {'approval_ref': 'synthetic additive transition', 'old_scope': load_config(old_path)[-1],
                'new_scope': load_config(new_path)[-1]}
    evidence.update({k: True for k in ('callers_excluded', 'backend_quiescent', 'cleanup_verified',
                                     'gpu1_restored', 'backend_ready', 'placement_verified')})
    return old_path, new_path, target, evidence, runtime


class FakeSwitch:
    def __init__(self, target, failure=None):
        self.target, self.failure, self.calls = target, failure, []
    def proof(self, mode):
        return {'mode': mode, 'backend_ready': True, 'placement_verified': True,
                'physical_gpus': ['gpu1'] if mode == 'gpu1' else ['gpu0', 'gpu1'],
                'gpu1_restored': mode == 'gpu1', 'switch_binding': switch_binding(self.target.mode_switch)}
    def activate(self):
        self.calls.append('activate')
        if self.failure == 'activate':
            raise RecoveryRequired('synthetic_switch_interrupted')
        if self.failure == 'signal':
            raise Interrupted(signal.SIGTERM)
        return self.proof('dual')
    def restore(self):
        self.calls.append('restore')
        if self.failure == 'restore':
            raise CleanupPending('synthetic_restoration_failed')
        return self.proof('gpu1')
    def verify(self, mode):
        self.calls.append('verify-' + mode)
        return self.proof(mode)


class FakeDocker:
    def __init__(self, policy, runtime):
        self.policy, self.runtime = policy, runtime
        self.mode, self.calls, self.up_error, self.drift = 'gpu1', [], False, False
    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs.get('input')))
        if 'config' in argv:
            out = kwargs['input']
        elif 'up' in argv:
            config = json.loads(kwargs['input'])
            self.mode = 'dual' if len(config['services']['ollama']['devices']) == 2 else 'gpu1'
            if self.up_error:
                raise subprocess.TimeoutExpired(argv, .1)
            out = ''
        elif argv[1:3] == ['compose', 'version']:
            out = self.policy['compose_version']
        elif argv[1] == 'version':
            out = self.policy['docker_version']
        elif argv[1:3] == ['image', 'inspect']:
            out = self.policy['image_id']
        elif argv[1] == 'inspect':
            indices = ('1',) if self.mode == 'gpu1' else ('0', '1')
            out = json.dumps({'running': True, 'image': self.policy['image_id'], 'runtime': 'runc',
                'privileged': False, 'devices': [], 'rules': [], 'requests': [{'Count': 0,
                'DeviceIDs': ['nvidia.com/gpu=' + i for i in indices]}],
                'preserved': dict(self.runtime, kv_cache='wrong') if self.drift else self.runtime})
        elif argv[1] == 'exec':
            out = '0, GPU-one' if self.mode == 'gpu1' else '0, GPU-zero\n1, GPU-one'
        else:
            out = '0, GPU-zero\n1, GPU-one'
        return subprocess.CompletedProcess(argv, 0, out, '')


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux CDI session fixtures')
class DualTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='synthetic-dual-')
        self.root = Path(self.tmp.name)
        self.old, self.new, self.raw_target, self.evidence, self.runtime = fixture(self.root)
        transition_scope(self.old, self.new, self.evidence)
        _, targets, self.admission = admission_from_config(self.new)
        self.target = targets['meeting-dual']
        self.env = dict(os.environ, AIHUB_GPU_RUNNER_CONFIG=str(self.new),
                        AIHUB_GPU_DUAL_APPROVAL='synthetic-window')
    def tearDown(self):
        self.tmp.cleanup()
    def stage(self, switch=None, transport=None, job_id='dual-stage'):
        return HostStage(self.admission, 'gpu0+gpu1', job_id, 'synthetic-request-hash',
            ollama_target=self.target, switch_controller=switch or FakeSwitch(self.target),
            transport_factory=lambda url: transport or FakeTransport())
    def run_stage(self, stage, callback=lambda: 0):
        class Child:
            pid = 123456789
            def wait(self, *args):
                return callback()
            def poll(self):
                return 0
        with patch('aihub_gpu_runner.host_stage.subprocess.Popen', return_value=Child()), \
             patch('aihub_gpu_runner.host_stage.os.killpg', side_effect=ProcessLookupError):
            return stage.run([sys.executable, '-c', 'pass'], self.env, .2)
    def recovery(self):
        owner = self.admission.snapshot()['owners']['gpu1']
        return dict(owner, approval_ref='synthetic recovery', backend_quiescent=True, cleanup_verified=True,
            operator_approved_backend_recovery=True, gpu1_restored=True, backend_ready=True,
            placement_verified=True, switch_binding=switch_binding(self.target.mode_switch))
    def test_session_holds_both_for_multiple_calls_then_unloads_and_restores_once(self):
        switch, transport = FakeSwitch(self.target), FakeTransport()
        stage = self.stage(switch, transport)
        def work():
            client = HostOllamaClient(self.admission, self.target, stage.job_id, stage.lease.owner['lease_id'], transport)
            for model in ('synthetic-map', 'qwen3.8:27b', 'qwen3.8:27b'):
                self.assertEqual(set(self.admission.snapshot()['owners']), {'gpu0', 'gpu1'})
                self.assertIsNone(self.admission.try_acquire('competing', 'h', ('gpu0',), 'wallpaper'))
                self.assertIsNone(self.admission.try_acquire('competing', 'h', ('gpu1',), 'ollama'))
                result = client.generate(self.target.base_url, {'model': model, 'prompt': 'synthetic',
                    'options': {'num_ctx': 196608 if model == 'qwen3.8:27b' else 4096}})
                self.assertTrue(result['done'])
            self.assertEqual(switch.calls, ['activate'])
            return 0
        self.assertEqual(self.run_stage(stage, work), 0)
        self.assertEqual(switch.calls, ['activate', 'restore'])
        unloads = [c[2]['model'] for c in transport.calls if c[2] and c[2].get('keep_alive') == 0]
        self.assertEqual(sorted(unloads), ['qwen3.8:27b', 'synthetic-map'])
        self.assertEqual(self.admission.snapshot()['owners'], {})
        self.assertEqual(self.run_stage(self.stage(switch, transport)), 0)
        self.assertEqual(switch.calls, ['activate', 'restore'])
    def test_explicit_approval_and_dual_resource_selection_required(self):
        stage = self.stage()
        with self.assertRaises(ValueError):
            stage.run(['unused'], {}, .1)
        with self.assertRaises(ValueError):
            HostStage(self.admission, 'gpu1', 'bad', 'h', ollama_target=self.target)
        self.assertEqual(self.admission.snapshot()['owners'], {})
    def test_interrupted_activation_keeps_both_no_automatic_restore_or_replay(self):
        switch = FakeSwitch(self.target, 'activate')
        with self.assertRaises(RecoveryRequired):
            self.run_stage(self.stage(switch))
        self.assertEqual(set(self.admission.snapshot()['owners']), {'gpu0', 'gpu1'})
        with self.assertRaises(RecoveryRequired):
            self.run_stage(self.stage(switch))
        self.assertEqual(switch.calls, ['activate'])
        with self.assertRaises(ValueError):
            self.admission.recover('dual-stage', self.recovery())
    def test_signal_during_switch_keeps_both_and_does_not_restore(self):
        switch = FakeSwitch(self.target, 'signal')
        self.assertEqual(self.run_stage(self.stage(switch)), 143)
        self.assertEqual(switch.calls, ['activate'])
        self.assertEqual(set(self.admission.snapshot()['owners']), {'gpu0', 'gpu1'})
    def test_model_submission_during_activation_is_rejected(self):
        switch, transport = FakeSwitch(self.target), FakeTransport()
        stage = self.stage(switch, transport)
        def activate():
            client = HostOllamaClient(self.admission, self.target, stage.job_id, stage.lease.owner['lease_id'], transport)
            with self.assertRaises(RecoveryRequired):
                client.generate(self.target.base_url, {'model': 'synthetic-map', 'prompt': 'synthetic'})
            self.assertEqual(transport.calls, [])
            return switch.proof('dual')
        switch.activate = activate
        self.assertEqual(self.run_stage(stage), 0)
    def test_incomplete_restoration_proof_never_releases_both(self):
        switch = FakeSwitch(self.target)
        switch.restore = lambda: dict(switch.proof('gpu1'), physical_gpus=['gpu0', 'gpu1'])
        with self.assertRaises(CleanupPending):
            self.run_stage(self.stage(switch))
        self.assertEqual(set(self.admission.snapshot()['owners']), {'gpu0', 'gpu1'})
    def test_uncertain_model_call_never_unloads_or_restores(self):
        switch, transport = FakeSwitch(self.target), FakeTransport('response')
        stage = self.stage(switch, transport)
        def work():
            client = HostOllamaClient(self.admission, self.target, stage.job_id, stage.lease.owner['lease_id'], transport)
            try:
                client.generate(self.target.base_url, {'model': 'synthetic-map', 'prompt': 'synthetic'})
            except Uncertain:
                pass  # Application swallowing the exception must not permit restoration.
            return 0
        with self.assertRaises(RecoveryRequired):
            self.run_stage(stage, work)
        self.assertEqual(switch.calls, ['activate'])
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(set(self.admission.snapshot()['owners']), {'gpu0', 'gpu1'})
    def test_owned_unload_failure_holds_both_and_does_not_restore(self):
        switch, transport = FakeSwitch(self.target), FakeTransport('unload')
        stage = self.stage(switch, transport)
        def work():
            HostOllamaClient(self.admission, self.target, stage.job_id, stage.lease.owner['lease_id'], transport).generate(
                self.target.base_url, {'model': 'synthetic-map', 'prompt': 'synthetic'})
            return 0
        with self.assertRaises(CleanupPending):
            self.run_stage(stage, work)
        self.assertEqual(switch.calls, ['activate'])
        self.assertEqual(set(self.admission.snapshot()['owners']), {'gpu0', 'gpu1'})
    def test_restoration_failure_requires_separate_verified_manual_restore_and_recovery(self):
        switch = FakeSwitch(self.target, 'restore')
        with self.assertRaises(CleanupPending):
            self.run_stage(self.stage(switch))
        with self.assertRaises(ValueError):
            self.admission.recover('dual-stage', self.recovery())
        evidence = dict(self.recovery(), operator_approved_dual_restoration=True,
                        backend_work_resolved=True, observed_mode='dual')
        healthy = FakeSwitch(self.target)
        proof = restore_held(self.admission, self.target, 'dual-stage', evidence, healthy)
        self.assertTrue(proof['gpu1_restored'])
        self.assertEqual(healthy.calls, ['restore'])
        self.assertEqual(set(self.admission.snapshot()['owners']), {'gpu0', 'gpu1'})
        self.admission.recover('dual-stage', self.recovery())
        self.assertEqual(self.admission.snapshot()['owners'], {})
    def test_manual_restore_of_already_restored_mode_verifies_without_recreation(self):
        switch = FakeSwitch(self.target, 'activate')
        with self.assertRaises(RecoveryRequired):
            self.run_stage(self.stage(switch))
        evidence = dict(self.recovery(), operator_approved_dual_restoration=True,
                        backend_work_resolved=True, observed_mode='gpu1')
        healthy = FakeSwitch(self.target)
        restore_held(self.admission, self.target, 'dual-stage', evidence, healthy)
        self.assertEqual(healthy.calls, ['verify-gpu1'])
        self.assertEqual(set(self.admission.snapshot()['owners']), {'gpu0', 'gpu1'})
    def test_new_failed_manual_restoration_cannot_reuse_an_old_placement_proof(self):
        with self.assertRaises(RecoveryRequired):
            self.run_stage(self.stage(FakeSwitch(self.target, 'activate')))
        evidence = dict(self.recovery(), operator_approved_dual_restoration=True,
                        backend_work_resolved=True, observed_mode='gpu1')
        restore_held(self.admission, self.target, 'dual-stage', evidence, FakeSwitch(self.target))
        evidence['observed_mode'] = 'dual'
        with self.assertRaises(CleanupPending):
            restore_held(self.admission, self.target, 'dual-stage', evidence, FakeSwitch(self.target, 'restore'))
        self.assertNotIn('dual_restoration_verified', self.admission.store.job('dual-stage'))
        with self.assertRaises(ValueError):
            self.admission.recover('dual-stage', self.recovery())
    def test_compose_controller_pins_exact_config_image_cdi_and_no_pull(self):
        run = FakeDocker(self.target.mode_switch, self.runtime)
        controller = ComposeOllamaSwitch(self.target, run=run, transport=FakeTransport(),
            probes={'gpu0': lambda: 0, 'gpu1': lambda: 0}, poll_interval=.001)
        self.assertEqual(controller.activate()['mode'], 'dual')
        self.assertTrue(controller.restore()['gpu1_restored'])
        up = [args for args, data in run.calls if 'up' in args]
        self.assertEqual(len(up), 2)
        for args in up:
            self.assertIn('--no-build', args)
            self.assertIn('--no-deps', args)
            self.assertEqual(args[args.index('--pull') + 1], 'never')
    def test_compose_timeout_after_effect_is_never_retried_or_rolled_back(self):
        run = FakeDocker(self.target.mode_switch, self.runtime)
        run.up_error = True
        controller = ComposeOllamaSwitch(self.target, run=run, transport=FakeTransport(),
            probes={'gpu0': lambda: 0, 'gpu1': lambda: 0}, poll_interval=.001)
        with self.assertRaises(RecoveryRequired):
            controller.activate()
        self.assertEqual(run.mode, 'dual')
        self.assertEqual(len([a for a, _ in run.calls if 'up' in a]), 1)
    def test_configuration_hash_or_running_runtime_drift_prevents_switch(self):
        for drift in ('file', 'runtime'):
            with self.subTest(drift=drift):
                run = FakeDocker(self.target.mode_switch, self.runtime)
                run.drift = drift == 'runtime'
                path = Path(self.target.mode_switch['dual_compose'])
                old = path.read_bytes()
                if drift == 'file':
                    path.write_bytes(old + b' ')
                controller = ComposeOllamaSwitch(self.target, run=run, transport=FakeTransport(),
                    probes={'gpu0': lambda: 0, 'gpu1': lambda: 0}, poll_interval=.001)
                try:
                    with self.assertRaises(RecoveryRequired):
                        controller.activate()
                    self.assertFalse(any('up' in a for a, _ in run.calls))
                finally:
                    path.write_bytes(old)
    def test_memory_cleanup_and_backend_readiness_are_required_for_restoration(self):
        run = FakeDocker(self.target.mode_switch, self.runtime)
        run.mode = 'dual'
        controller = ComposeOllamaSwitch(self.target, run=run, transport=FakeTransport(),
            probes={'gpu0': lambda: 100, 'gpu1': lambda: 0}, poll_interval=.001)
        with self.assertRaises(CleanupPending):
            controller.restore()
        self.assertFalse(any('up' in a for a, _ in run.calls))
    def test_restrictive_device_environment_is_not_silently_preserved_in_dual_mode(self):
        policy = self.target.mode_switch
        for mode in ('gpu1', 'dual'):
            path = Path(policy[mode + '_compose'])
            config = json.loads(path.read_text())
            config['services']['ollama']['environment']['CUDA_VISIBLE_DEVICES'] = '1'
            path.write_bytes(encode(config))
            policy[mode + '_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            policy['preserved_config_sha256'] = preserved_config(config, 'ollama')
        run = FakeDocker(policy, self.runtime)
        controller = ComposeOllamaSwitch(self.target, run=run, transport=FakeTransport(),
            probes={'gpu0': lambda: 0, 'gpu1': lambda: 0}, poll_interval=.001)
        with self.assertRaises(RecoveryRequired):
            controller.activate()
        self.assertFalse(any('up' in a for a, _ in run.calls))
    def test_running_q8_flash_attention_must_hold_even_if_bad_metadata_was_bound(self):
        runtime = dict(self.runtime, flash_attention='false')
        self.target.mode_switch['runtime_contract_sha256'] = hashlib.sha256(encode(runtime)).hexdigest()
        run = FakeDocker(self.target.mode_switch, runtime)
        controller = ComposeOllamaSwitch(self.target, run=run, transport=FakeTransport(),
            probes={'gpu0': lambda: 0, 'gpu1': lambda: 0}, poll_interval=.001)
        with self.assertRaises(RecoveryRequired):
            controller.activate()
        self.assertFalse(any('up' in a for a, _ in run.calls))
    def test_backend_unavailable_prevents_restoration_and_ownership_handoff(self):
        run = FakeDocker(self.target.mode_switch, self.runtime)
        run.mode = 'dual'
        transport = FakeTransport()
        transport.json = lambda *a, **k: (_ for _ in ()).throw(Uncertain('synthetic_backend_unavailable'))
        controller = ComposeOllamaSwitch(self.target, run=run, transport=transport,
            probes={'gpu0': lambda: 0, 'gpu1': lambda: 0}, poll_interval=.001)
        with self.assertRaises(CleanupPending):
            controller.restore()
        self.assertFalse(any('up' in a for a, _ in run.calls))
    def test_dual_target_is_not_exposed_as_api_per_request_switch(self):
        _, runner = make_runner(self.new)
        try:
            with self.assertRaises(ValueError):
                runner.submit('api-dual', 'meeting-dual', 'generate',
                              {'model': 'qwen3.8:27b', 'prompt': 'synthetic'})
            self.assertEqual(self.admission.snapshot()['owners'], {})
        finally:
            runner.close()
    def test_unconfigured_dual_target_and_null_cleanup_fail_closed(self):
        value = json.loads(self.new.read_text())
        del value['targets']['meeting-dual']['mode_switch']
        bad = self.root / 'bad.json'
        bad.write_bytes(encode(value))
        with self.assertRaises(ValueError):
            load_config(bad)
        policy = copy.deepcopy(self.target.mode_switch)
        policy['idle_memory_mb']['gpu0'] = None
        with self.assertRaises(ValueError):
            validate_policy(policy)
    @unittest.skipUnless(os.environ.get('MEETING_PIPELINE_TEST_ROOT'), 'guarded meeting launcher harness')
    def test_historical_dual_launcher_holds_session_and_preserves_normal_mapreduce_without_audio(self):
        transcript = self.root / 'historical'
        (transcript / 'chunks_out').mkdir(parents=True)
        (transcript / 'chunks_out/transcript_chunks.jsonl').write_text(json.dumps({'chunk_id': 1,
            'text': '[SPEAKER_00] Okay guys, I think we will get started here.\n[SPEAKER_00] CURRENT_PUBLIC_SENTINEL needs discussion.',
            'start_time': 0, 'end_time': 30}) + '\n')
        settings = self.root / 'meeting.env'
        settings.write_text('MEETING_MAP_MODEL=synthetic-map\nMEETING_REDUCE_MODEL=qwen3.8:27b\n')
        log, switches = self.root / 'mock-calls.jsonl', self.root / 'mock-switches.jsonl'
        env = dict(self.env, MEETING_CONFIG_FILE=str(settings),
            MEETING_SUMMARIES_ROOT=str(self.root / 'comparison'), OLLAMA_URL=self.target.base_url,
            AIHUB_GPU_RUNNER_PYTHON=sys.executable, MEETING_SUMMARY_PYTHON=sys.executable,
            MEETING_FAKE_OLLAMA_LOG=str(log), DUAL_OFFLINE_SWITCH_LOG=str(switches),
            MEETING_TEST_GPU0=str(self.root / 'gpu0.lock'), MEETING_TEST_GPU1=str(self.root / 'gpu1.lock'))
        command = ['bash', str(Path(os.environ['MEETING_PIPELINE_TEST_ROOT']) / 'bin/summarize-existing-meeting.sh'),
                   str(transcript), '--dual-gpu-target', 'meeting-dual', '--reduce-num-ctx', '196608', '--keep-recap']
        outcome = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(outcome.returncode, 0, outcome.stderr)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertTrue(calls)
        self.assertTrue(all(not c['gpu0_free'] and c['gpu1_held'] for c in calls))
        self.assertEqual([json.loads(row)['action'] for row in switches.read_text().splitlines()], ['activate', 'restore'])
        self.assertTrue((self.root / 'comparison/historical/minutes-draft.md').is_file())
        self.assertEqual(list(transcript.glob('*.wav')), [])
        self.assertEqual(self.admission.snapshot()['owners'], {})
        again = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(len(log.read_text().splitlines()), len(calls))


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux scope-transition fixtures')
class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='scope-transition-')
        self.root = Path(self.tmp.name)
        self.old, self.new, _, self.evidence, _ = fixture(self.root)
        _, _, self.old_gate = admission_from_config(self.old)
    def tearDown(self):
        self.tmp.cleanup()
    def test_idle_transition_and_exact_rollback_preserve_journal_jobs_bootstrap_and_lock_inodes(self):
        self.old_gate.store.put_job({'request_id': 'old-complete', 'state': 'success', 'request_hash': 'old'})
        old_job = self.old_gate.store.path('jobs/old-complete.json').read_bytes()
        before = self.old_gate.snapshot()
        result = transition_scope(self.old, self.new, self.evidence)
        self.assertTrue(result['journal_preserved'])
        self.assertTrue(transition_scope(self.old, self.new, self.evidence)['already_recorded'])
        with self.assertRaises(RecoveryRequired):
            admission_from_config(self.old)
        _, _, new_gate = admission_from_config(self.new)
        after = new_gate.snapshot()
        for key in ('bootstrap_id', 'bootstrap_evidence', 'ready', 'lock_identity', 'owners'):
            self.assertEqual(before[key], after[key])
        self.assertEqual(new_gate.store.path('jobs/old-complete.json').read_bytes(), old_job)
        reverse = dict(self.evidence, approval_ref='synthetic rollback',
                       old_scope=self.evidence['new_scope'], new_scope=self.evidence['old_scope'],
                       rollback_of=self.evidence['approval_ref'])
        transition_scope(self.new, self.old, reverse, rollback=True)
        _, _, restored = admission_from_config(self.old)
        self.assertEqual(len(restored.snapshot()['scope_transitions']), 2)
        self.assertEqual(restored.snapshot()['lock_identity'], before['lock_identity'])
    def test_active_owner_or_pending_job_blocks_scope_transition_without_mutation(self):
        lease = self.old_gate.try_acquire('busy', 'h', ('gpu0',), 'whisperx')
        before = self.old_gate.store.path('ownership.json').read_bytes()
        with self.assertRaises(RecoveryRequired):
            transition_scope(self.old, self.new, self.evidence)
        self.assertEqual(before, self.old_gate.store.path('ownership.json').read_bytes())
        lease.release({'completion_verified': True, 'cleanup_verified': True})
        lease.close()
        self.old_gate.store.put_job({'request_id': 'queued', 'state': 'waiting'})
        before = self.old_gate.store.path('ownership.json').read_bytes()
        with self.assertRaises(RecoveryRequired):
            transition_scope(self.old, self.new, self.evidence)
        self.assertEqual(before, self.old_gate.store.path('ownership.json').read_bytes())
    def test_supervisor_or_physical_lock_busy_blocks_scope_transition(self):
        for path in (self.old_gate.store.path('supervisor.lock'), self.old_gate.lock_paths['gpu1']):
            lock = NativeLock(path)
            self.assertTrue(lock.acquire(0))
            try:
                with self.assertRaises(RecoveryRequired):
                    transition_scope(self.old, self.new, self.evidence)
            finally:
                lock.close()
    def test_transition_write_failure_after_effect_reconnects_without_reset_or_duplicate_history(self):
        original = AtomicStore.write
        def fail_after(store, name, value):
            original(store, name, value)
            if name == 'ownership.json':
                raise OSError('synthetic fsync response lost')
        with patch.object(AtomicStore, 'write', fail_after), self.assertRaises(OSError):
            transition_scope(self.old, self.new, self.evidence)
        self.assertTrue(transition_scope(self.old, self.new, self.evidence)['already_recorded'])
        _, _, gate = admission_from_config(self.new)
        self.assertEqual(len(gate.snapshot()['scope_transitions']), 1)
        self.assertTrue(gate.snapshot()['ready'])
    def test_changed_routine_settings_or_missing_journal_rejected_without_bootstrap(self):
        original = self.new.read_bytes()
        value = json.loads(self.new.read_text())
        value['targets']['ollama']['models'] = ['different']
        self.new.write_bytes(encode(value))
        with self.assertRaises(ValueError):
            transition_scope(self.old, self.new, self.evidence)
        self.new.write_bytes(original)
        self.old_gate.store.path('ownership.json').unlink()  # Temporary synthetic resource only.
        with self.assertRaises(RecoveryRequired):
            transition_scope(self.old, self.new, self.evidence)
        self.assertFalse(self.old_gate.store.path('ownership.json').exists())


@unittest.skipUnless(sys.platform.startswith('linux'), 'native Linux multiprocess flock')
class NativeDualTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='native-dual-')
        self.root = Path(self.tmp.name)
        self.old, self.new, _, evidence, _ = fixture(self.root)
        transition_scope(self.old, self.new, evidence)
        _, _, self.gate = admission_from_config(self.new)
        self.processes = []
    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
        self.tmp.cleanup()
    def launch(self):
        code = '''import sys,time,os
from pathlib import Path
from aihub_gpu_runner.config import admission_from_config
from aihub_gpu_runner.host_stage import HostStage
from aihub_gpu_runner.dual_gpu import switch_binding
root=Path(sys.argv[1]);_,targets,gate=admission_from_config(root/'new.json');target=targets['meeting-dual']
class Control:
 def activate(self):
  (root/'entered').write_text('dual synthetic activation')
  while not (root/'continue').exists():time.sleep(.01)
  return {'mode':'dual','physical_gpus':['gpu0','gpu1'],'backend_ready':True,'placement_verified':True,'switch_binding':switch_binding(target.mode_switch)}
 def restore(self):
  (root/'restored').write_text('gpu1 synthetic restoration')
  return {'mode':'gpu1','physical_gpus':['gpu1'],'gpu1_restored':True,'backend_ready':True,'placement_verified':True,'switch_binding':switch_binding(target.mode_switch)}
stage=HostStage(gate,'gpu0+gpu1','native-session','native-hash',ollama_target=target,switch_controller=Control())
raise SystemExit(stage.run([sys.executable,'-c','pass'],dict(os.environ,AIHUB_GPU_DUAL_APPROVAL='synthetic-window'),2))
'''
        process = subprocess.Popen([sys.executable, '-B', '-c', code, str(self.root)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.processes.append(process)
        return process
    def wait_entered(self, process):
        deadline = time.monotonic() + 3
        while not (self.root / 'entered').exists():
            if process.poll() is not None:
                self.fail(process.communicate())
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)
    def test_waiting_for_gpu1_never_partially_reserves_gpu0_then_whole_session_excludes_both(self):
        single = self.gate.try_acquire('normal', 'h', ('gpu1',), 'ollama')
        process = self.launch()
        time.sleep(.1)
        self.assertFalse((self.root / 'entered').exists())
        self.assertEqual(set(self.gate.snapshot()['owners']), {'gpu1'})
        other = self.gate.try_acquire('whisper', 'h', ('gpu0',), 'host-whisperx')
        self.assertIsNotNone(other)
        other.release({'completion_verified': True, 'cleanup_verified': True}); other.close()
        single.release({'completion_verified': True, 'cleanup_verified': True}); single.close()
        self.wait_entered(process)
        for resources in (('gpu0',), ('gpu1',), ('gpu0', 'gpu1')):
            self.assertIsNone(self.gate.try_acquire('other', 'h', resources, 'synthetic'))
        for path in self.gate.lock_paths.values():
            outcome = subprocess.run(['flock', '-n', str(path), 'true'])
            self.assertNotEqual(outcome.returncode, 0)
        (self.root / 'continue').touch()
        self.assertEqual(process.communicate(timeout=5)[1].count('ERROR'), 0)
        self.assertEqual(process.returncode, 0)
        self.assertTrue((self.root / 'restored').exists())
        self.assertEqual(self.gate.snapshot()['owners'], {})
    def test_sigkill_frees_os_locks_but_both_durable_owners_block_restart_and_rollback(self):
        process = self.launch()
        self.wait_entered(process)
        process.kill(); process.communicate(timeout=5)
        for path in self.gate.lock_paths.values():
            self.assertEqual(subprocess.run(['flock', '-n', str(path), 'true']).returncode, 0)
        self.assertIsNone(self.gate.try_acquire('other', 'h', ('gpu0',), 'wallpaper'))
        self.assertIsNone(self.gate.try_acquire('other', 'h', ('gpu1',), 'ollama'))
        again = self.launch()
        again.communicate(timeout=5)
        self.assertNotEqual(again.returncode, 0)
        self.assertFalse((self.root / 'restored').exists())
        self.assertEqual(set(self.gate.snapshot()['owners']), {'gpu0', 'gpu1'})


if __name__ == '__main__':
    unittest.main()
