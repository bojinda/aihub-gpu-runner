"""Synthetic wallpaper execution, real native admission locks, no GPU operations."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from aihub_gpu_runner.backends import Target, ComfyBackend, OllamaBackend, Uncertain
from aihub_gpu_runner.config import load_config, make_runner
from aihub_gpu_runner.core import Admission, AtomicStore, NativeLock, RecoveryRequired, Runner


GPU0 = 'GPU-2466a5de-d7d9-8799-a5f0-e2fc9fd5d559'
GPU1 = 'GPU-d786e691-b17c-9ebc-5a60-101433ca2adf'
WORKFLOW = {'2': {'class_type': 'SyntheticOutput', 'inputs': {}},
            '3': {'class_type': 'AIHubQwenGPU1CLIPLoader', 'inputs': {
                'clip_name': 'qwen_2.5_vl_7b_fp8_scaled.safetensors',
                'gpu0_uuid': GPU0, 'gpu1_uuid': GPU1}}}


class Transport:
    def __init__(self, history='success', invalid_artifact=False):
        self.history, self.invalid_artifact = history, invalid_artifact
        self.submissions, self.frees = 0, 0
        self.phase = 'before'
        self.free_payload = None
        self.events = []
    def json(self, method, route, payload=None, timeout=5):
        if route == '/queue':
            return {'queue_running': [], 'queue_pending': []}
        if route == '/prompt':
            self.submissions += 1
            self.events.append('wallpaper-submit')
            return {'prompt_id': 'synthetic-owned-prompt'}
        if route == '/history/synthetic-owned-prompt':
            if self.history == 'uncertain':
                raise Uncertain('synthetic_history_uncertain')
            if self.history == 'missing':
                return {}
            success = self.history == 'success'
            return {'synthetic-owned-prompt': {
                'status': {'status_str': 'success' if success else 'error', 'completed': success},
                'outputs': {'2': {'images': [{'filename': 'synthetic.png', 'type': 'output'}]}}}}
        raise AssertionError('unexpected synthetic route')
    def ack(self, method, route, payload=None, timeout=5):
        assert method == 'POST' and route == '/free'
        self.frees += 1
        self.free_payload, self.phase = payload, 'cleanup'
    def binary(self, *args, **kwargs):
        return (b'bad' if self.invalid_artifact else b'\x89PNG\r\n\x1a\nsynthetic'), 'image/png'


class WallpaperConditioningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='offline-dual-wallpaper-')
        self.root = Path(self.tmp.name)
        self.store = AtomicStore(self.root / 'state')
        self.paths = {r: self.root / (r + '.lock') for r in ('gpu0', 'gpu1')}
        self.gate = Admission(self.store, self.paths, 'synthetic')
        self.gate.bootstrap({'approval_ref': 'OFFLINE ONLY',
            'bootstrap_id': self.gate.snapshot()['bootstrap_id'],
            'backend_quiescent': True, 'cleanup_verified': True})
        self.runners, self.releases = [], []
        self.addCleanup(patch.stopall)
        patch('subprocess.check_output', side_effect=AssertionError('real GPU probe forbidden')).start()
        patch('subprocess.run', side_effect=AssertionError('real process/GPU work forbidden')).start()
    def tearDown(self):
        for event in self.releases:
            event.set()
        for runner in self.runners:
            runner.close()
        self.tmp.cleanup()
    def target(self, **kwargs):
        params = dict(name='wallpaper', kind='comfy', resources=('gpu0', 'gpu1'),
            base_url='http://offline.invalid', workflow=WORKFLOW, output_node='2',
            idle_memory_mb=10, conditioning_idle_memory_mb=5, cleanup_samples=3,
            acquisition_timeout=.2, execution_timeout=.1, cleanup_timeout=.06)
        return Target(**dict(params, **kwargs))
    def runner(self, transport=None, probe0=None, probe1=None):
        target, transport = self.target(), transport or Transport()
        backend = ComfyBackend(target, transport, probe=probe0 or (lambda: 1),
            conditioning_probe=probe1 or (lambda: 1), poll_interval=.001)
        runner = Runner(self.gate, {'wallpaper': target}, {'wallpaper': backend}, poll_interval=.001)
        self.runners.append(runner)
        return runner, transport
    def submit(self, runner, job_id='synthetic-wallpaper'):
        runner.submit(job_id, 'wallpaper', 'workflow', WORKFLOW)
        runner.future(job_id).result(3)
        return runner.status(job_id)

    def test_both_resources_share_one_lease_and_release_only_after_three_joint_samples(self):
        transport = Transport()
        # Both directions of instability reset the JOINT stable counter.
        samples = iter([(1, 1), (1, 8), (12, 1), (1, 1), (1, 1), (1, 1)])
        observed, pair = [], [1, 1]
        def probe0():
            if transport.phase == 'cleanup':
                pair[:] = next(samples)
                owners = self.gate.snapshot()['owners']
                self.assertEqual(set(owners), {'gpu0', 'gpu1'})
                self.assertEqual(owners['gpu0']['lease_id'], owners['gpu1']['lease_id'])
                observed.append(tuple(pair))
            return pair[0]
        runner, _ = self.runner(transport, probe0, lambda: pair[1])
        job = self.submit(runner)
        self.assertEqual(job['state'], 'success')
        self.assertEqual(len(observed), 6)
        self.assertEqual(self.gate.snapshot()['owners'], {})
        proof = job['cleanup_proof']
        self.assertEqual(proof['stable_samples'], 3)
        self.assertEqual(proof['memory_mb_by_resource'], {'gpu0': 1, 'gpu1': 1})
        self.assertEqual(proof['idle_ceiling_mb_by_resource'], {'gpu0': 10, 'gpu1': 5})
        self.assertEqual(transport.free_payload, {'unload_models': True, 'free_memory': False})

    def test_busy_gpu1_native_lock_does_not_partially_claim_gpu0(self):
        busy = NativeLock(self.paths['gpu1'], create=False)
        self.assertTrue(busy.acquire(0))
        try:
            self.assertIsNone(self.gate.try_acquire('blocked', 'hash', ('gpu0', 'gpu1'), 'wallpaper'))
            self.assertEqual(self.gate.snapshot()['owners'], {})
            independent = NativeLock(self.paths['gpu0'], create=False)
            self.assertTrue(independent.acquire(0))
            independent.close()
        finally:
            busy.close()

    def test_gpu1_residency_or_bad_observation_prevents_any_submission_and_retains_both(self):
        for value in (6, float('nan'), -1, True):
            with self.subTest(value=value):
                transport = Transport()
                backend = ComfyBackend(self.target(), transport, lambda: 1,
                    conditioning_probe=lambda: value)
                with self.assertRaises(Uncertain):
                    backend.execute('workflow', WORKFLOW, time.monotonic() + 1, lambda *a: None)
                self.assertEqual(transport.submissions, 0)
        runner, transport = self.runner(probe1=lambda: 6)
        self.assertEqual(self.submit(runner)['state'], 'reconciling')
        self.assertEqual(transport.submissions, 0)
        self.assertEqual(set(self.gate.snapshot()['owners']), {'gpu0', 'gpu1'})

    def test_gpu1_dirty_cleanup_retains_both_and_blocks_music_ollama_and_meeting(self):
        transport = Transport()
        runner, _ = self.runner(transport, probe1=lambda: 9 if transport.phase == 'cleanup' else 1)
        job = self.submit(runner)
        self.assertEqual(job['state'], 'cleanup_pending')
        for resources, target in ((('gpu0',), 'music'), (('gpu1',), 'ollama'),
                                  (('gpu0', 'gpu1'), 'meeting-dual-ha')):
            self.assertIsNone(self.gate.try_acquire('next-' + target, 'hash', resources, target))
        self.assertEqual(set(self.gate.snapshot()['owners']), {'gpu0', 'gpu1'})

    def test_uncertain_history_never_frees_or_replays_and_recovery_stays_operator_gated(self):
        runner, transport = self.runner(Transport(history='uncertain'))
        self.assertEqual(self.submit(runner)['state'], 'reconciling')
        owner = self.gate.snapshot()['owners']['gpu0']
        self.assertEqual(transport.frees, 0)
        runner.submit('synthetic-wallpaper', 'wallpaper', 'workflow', WORKFLOW)
        self.assertEqual(transport.submissions, 1)
        runner.close()
        self.runners.remove(runner)
        restarted, _ = self.runner(transport)
        self.assertEqual(restarted.status('synthetic-wallpaper')['state'], 'reconciling')
        self.assertEqual(transport.submissions, 1)
        with self.assertRaises(ValueError):
            self.gate.recover('synthetic-wallpaper', dict(owner, cleanup_verified=True))
        proof = dict(owner, approval_ref='SYNTHETIC RECOVERY ONLY', backend_quiescent=True,
                     cleanup_verified=True, operator_approved_backend_recovery=True)
        with self.assertRaises(ValueError):
            self.gate.recover('synthetic-wallpaper', dict(proof, lease_id='wrong'))
        self.gate.recover('synthetic-wallpaper', proof)
        self.assertEqual(self.gate.snapshot()['owners'], {})

    def test_missing_terminal_history_expires_without_freeing_either_resource(self):
        runner, transport = self.runner(Transport(history='missing'))
        self.assertEqual(self.submit(runner)['state'], 'reconciling')
        self.assertEqual(transport.frees, 0)
        self.assertEqual(set(self.gate.snapshot()['owners']), {'gpu0', 'gpu1'})

    def test_gpu1_cleanup_observation_failure_retains_both_owners(self):
        transport = Transport()
        def probe1():
            if transport.phase == 'cleanup':
                raise OSError('synthetic observer failure')
            return 1
        runner, _ = self.runner(transport, probe1=probe1)
        job = self.submit(runner)
        self.assertEqual(job['state'], 'cleanup_pending')
        self.assertEqual(job['error_code'], 'comfy_cleanup_uncertain')
        self.assertEqual(set(self.gate.snapshot()['owners']), {'gpu0', 'gpu1'})

    def test_terminal_error_or_bad_artifact_still_requires_both_cleanup_samples(self):
        for i, transport in enumerate((Transport(history='error'), Transport(invalid_artifact=True))):
            with self.subTest(history=transport.history, artifact=transport.invalid_artifact):
                runner, _ = self.runner(transport)
                job = self.submit(runner, 'synthetic-terminal-' + str(i))
                self.assertEqual(job['state'], 'failed')
                self.assertTrue(job['cleanup_verified'])
                self.assertEqual(set(job['cleanup_proof']['memory_mb_by_resource']), {'gpu0', 'gpu1'})
                self.assertEqual(self.gate.snapshot()['owners'], {})
                runner.close()
                self.runners.remove(runner)

    def test_ollama_completion_and_unload_precede_wallpaper_admission(self):
        release, unloading = threading.Event(), threading.Event()
        self.releases.append(release)
        events = []
        class OllamaTransport:
            def json(inner, method, route, payload=None, timeout=5):
                if route == '/api/ps':
                    events.append('ollama-empty')
                    return {'models': []}
                if payload.get('keep_alive') == 0:
                    unloading.set()
                    if not release.wait(2):
                        raise AssertionError('synthetic unload not released')
                    events.append('ollama-unload')
                return {'done': True, 'response': 'synthetic'}
        wall = Transport()
        wall.events = events
        target = self.target()
        ollama = Target('ollama', 'ollama', ('gpu1',), 'http://offline.invalid', models=('synthetic',))
        runner = Runner(self.gate, {'wallpaper': target, 'ollama': ollama},
            {'wallpaper': ComfyBackend(target, wall, lambda: 1, conditioning_probe=lambda: 1, poll_interval=.001),
             'ollama': OllamaBackend(ollama, OllamaTransport(), poll_interval=.001)}, poll_interval=.001)
        self.runners.append(runner)
        runner.submit('synthetic-ollama', 'ollama', 'generate', {'model': 'synthetic', 'prompt': 'neutral'})
        self.assertTrue(unloading.wait(1))
        runner.submit('synthetic-wallpaper', 'wallpaper', 'workflow', WORKFLOW)
        self.assertEqual(wall.submissions, 0)
        self.assertEqual(set(self.gate.snapshot()['owners']), {'gpu1'})
        release.set()
        runner.future('synthetic-wallpaper').result(3)
        self.assertEqual(events, ['ollama-unload', 'ollama-empty', 'wallpaper-submit'])
        self.assertEqual(self.gate.snapshot()['owners'], {})

    def test_dual_target_requires_both_probes_and_cannot_enable_music(self):
        with self.assertRaises(ValueError):
            self.target(name='music')
        with self.assertRaises(ValueError):
            self.target(conditioning_idle_memory_mb=None)
        with self.assertRaises(ValueError):
            self.target(idle_memory_mb=None)
        with self.assertRaises(ValueError):
            self.target(conditioning_idle_memory_mb=True)
        with self.assertRaises(ValueError):
            self.target(resources=('gpu0',))
        with self.assertRaises(ValueError):
            ComfyBackend(self.target(), Transport(), lambda: 1).validate('workflow', WORKFLOW)

    def test_changed_scope_cannot_discard_a_retained_cpu_wallpaper_owner(self):
        lease = self.gate.try_acquire('original-cpu-job', 'original-hash', ('gpu0',), 'wallpaper')
        lease.mark('reconciling')
        lease.close()
        before = self.store.path('ownership.json').read_bytes()
        with self.assertRaisesRegex(RecoveryRequired, 'ownership_invalid_or_scope_changed'):
            Admission(self.store, self.paths, 'candidate-dual-wallpaper-scope', require_existing_locks=True)
        self.assertEqual(self.store.path('ownership.json').read_bytes(), before)
        self.assertIsNone(self.gate.try_acquire('candidate-job', 'new-hash', ('gpu0', 'gpu1'), 'wallpaper'))
        self.assertEqual(self.gate.snapshot()['owners']['gpu0']['job_id'], 'original-cpu-job')

    def test_config_requires_explicit_wallpaper_opt_in_and_wires_both_probes(self):
        config = {'state_dir': str(self.root / 'new-state'),
            'lock_paths': {r: str(p) for r, p in self.paths.items()},
            'targets': {'wallpaper': {'kind': 'comfy', 'resources': ['gpu0', 'gpu1'],
                'base_url': 'http://offline.invalid', 'workflow': WORKFLOW, 'output_node': '2',
                'idle_memory_mb': 10, 'conditioning_idle_memory_mb': 5}}}
        p = self.root / 'candidate.json'
        p.write_text(json.dumps(config))
        self.assertEqual(load_config(p)[1]['wallpaper'].resources, ('gpu0', 'gpu1'))
        with patch('aihub_gpu_runner.config.NvidiaUUIDMemoryProbe') as probe, \
             patch('aihub_gpu_runner.config.NvidiaMemoryProbe') as numeric, \
             patch('aihub_gpu_runner.config.Runner'):
            make_runner(p)
            self.assertEqual([c.args for c in probe.call_args_list], [(GPU0,), (GPU1,)])
            numeric.assert_not_called()
        bad = copy.deepcopy(config)
        del bad['targets']['wallpaper']['conditioning_idle_memory_mb']
        p.write_text(json.dumps(bad))
        with self.assertRaisesRegex(ValueError, 'routine_resource_policy'):
            load_config(p)
        bad['targets']['music'] = bad['targets'].pop('wallpaper')
        bad['targets']['music']['conditioning_idle_memory_mb'] = 5
        p.write_text(json.dumps(bad))
        with self.assertRaisesRegex(ValueError, 'routine_resource_policy'):
            load_config(p)


if __name__ == '__main__':
    unittest.main()
