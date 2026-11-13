"""Actual worker/guard/lease/store; synthetic telemetry and explicit phase latency.

No GPU or backend process is used. Local journal, persistence and locks are real.
The seven-second observations model captured cadence, not historical command proof.
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from aihub_gpu_runner.backends import Target
from aihub_gpu_runner.core import Admission, AtomicStore, NativeLock, Runner, encode
from aihub_gpu_runner.estop import activate_generations
from aihub_gpu_runner.ollama_cleanup import OllamaCleanupGuard, _ProcessPending
from aihub_gpu_runner.recovery import digest
from tests import test_ollama_physical_cleanup as physical

BOOT, GPU1 = physical.BOOT, physical.GPU1

REAL_MONOTONIC = time.monotonic
REAL_SLEEP = time.sleep
ORIGINAL_SAMPLE = OllamaCleanupGuard.sample


class WorkerBudgetTests(unittest.TestCase):
    def fixture(self, *, seconds=7, history=1799, authority_bytes=17_124_005):
        f = physical.PhysicalCleanupTests('runTest'); f.setUp(); self.addCleanup(f.doCleanups)
        tmp = tempfile.TemporaryDirectory(prefix='cleanup-budget-'); self.addCleanup(tmp.cleanup)
        root = Path(tmp.name); store = AtomicStore(root)
        paths = {r: root / (r + '.lock') for r in ('gpu0', 'gpu1')}
        for path in paths.values(): path.touch()
        a = Admission(store, paths, 'offline-scope', True, boot_reader=lambda: BOOT)
        a.bootstrap(dict(approval_ref='synthetic', bootstrap_id=a.snapshot()['bootstrap_id'],
                         backend_quiescent=True, cleanup_verified=True))
        activate_generations(a, confirmed_coordinated_shutdown=True)
        f.target = Target(name='ollama', kind='ollama', resources=('gpu1',),
                          base_url='http://synthetic', models=('synthetic',), cleanup_timeout=60)
        f.backend.target = f.target
        f.memory = lambda: 1
        # The original fixture's worker injects only process births. Replace its
        # in-memory journal/lease/mutex with the real native implementations.
        runner, _, payload = f.worker()
        job = f.store.job('owned-job')
        for n in range(history - 1):
            store.put_job(dict(request_id=f'history-{n}', request_hash='synthetic',
                               target='ollama', resources=['gpu1'], state='success'))
        store.put_job(job)
        lease = a.try_acquire('owned-job', job['request_hash'], ('gpu1',), 'ollama')
        self.assertIsNotNone(lease); self.addCleanup(lease.close)
        job.update(lease_id=lease.owner['lease_id'], boot_id=BOOT); store.put_job(job)
        state = a.snapshot()
        state['verified_boot'] = BOOT
        state['recovery_policy'] = {'policy': copy.deepcopy(f.p), 'policy_id': digest(f.p)}
        # Nested synthetic records model parse/copy cost, rather than one giant string.
        row = {'observations': [{'birth': str(i), 'identity': 'synthetic-' + 'x'*80} for i in range(8)]}
        state['qualification_history'] = [row for _ in range(max(0, authority_bytes // len(encode(row))))]
        store.write('ownership.json', state)
        runner.admission = a; runner.store = store
        runner.job_mutex = NativeLock(root / 'jobs.lock')
        f.observer.targets = {'ollama': f.target}
        runner.targets = {'ollama': f.target}
        f.backend.target = f.target
        f.samples = 0; f.boundaries = []; f.publications = 0; f.persistence_seconds = 0
        f.phase_costs = {}; f.root = root; f.a = a; f.store = store; f.lease = lease; f.runner = runner
        f.authority_bytes = (root/'ownership.json').stat().st_size
        original_command = f.command
        def command(argv, deadline):
            f.clock.t += .01
            f.phase_costs['command'] = f.phase_costs.get('command', 0) + .01
            return original_command(argv, deadline)
        f.observer.command.side_effect = command
        def driver_health(deadline):
            f.clock.t += .15; f.phase_costs['driver'] = f.phase_costs.get('driver', 0) + .15
            return {'reported_gpu_faults': False, 'kernel_records_checked': 3505}
        f.observer.driver_health.side_effect = driver_health
        transport = f.transport.json
        def request(*args, **kwargs):
            f.clock.t += .025
            return transport(*args, **kwargs)
        f.transport.json = request
        raw_read = store._read_raw
        def read(name, default=None):
            start = REAL_MONOTONIC()
            try: return raw_read(name, default)
            finally: f.clock.t += REAL_MONOTONIC() - start
        store._read_raw = read
        raw_write = store._write_bytes
        def write(name, data):
            start = REAL_MONOTONIC()
            try: return raw_write(name, data)
            finally:
                elapsed = REAL_MONOTONIC() - start
                f.persistence_seconds += elapsed
                f.clock.t += max(.02, elapsed)
                if str(name) == 'results/owned-job.json': f.publications += 1
        store._write_bytes = write
        sample = ORIGINAL_SAMPLE
        def measured_sample(guard, deadline):
            self.assertFalse(NativeLock.held_by_current_thread(a.mutex.path), 'external sample under authority mutex')
            start = f.clock.t; f.samples += 1
            f.boundaries.append(getattr(guard, 'boundary', 'baseline'))
            try: return sample(guard, deadline)
            finally:
                # Nonzero complete phase cost, including observer/storage/contention.
                delay = max(0, seconds - (f.clock.t - start))
                f.clock.t += min(delay, max(0, deadline - f.clock.t))
                from aihub_gpu_runner.backends import remaining, CleanupPending
                remaining(deadline, CleanupPending)
        self.enterContext(patch.object(OllamaCleanupGuard, 'sample', measured_sample))
        return f

    def run_worker(self, f):
        start = f.clock.t; wall = REAL_MONOTONIC()
        f.runner._work('owned-job', f.lease)
        f.elapsed = f.clock.t - start; f.wall = REAL_MONOTONIC() - wall
        job = f.store.job('owned-job')
        self.assertEqual(len([c for c in f.calls if c[0] == 'POST']), 2)
        self.assertLessEqual(f.publications, 1)
        print(json.dumps({'worker_outcome': job['state'], 'error': job.get('error_code'),
                          'samples': f.samples, 'virtual_seconds': f.elapsed, 'wall_seconds': f.wall,
                          'publications': f.publications, 'authority_bytes': f.authority_bytes,
                          'boundaries': f.boundaries, 'persistence_seconds': f.persistence_seconds}))
        return job

    def test_healthy_whole_worker_six_samples_inside_original_budget_with_real_history(self):
        f = self.fixture()
        job = self.run_worker(f)
        self.assertEqual(job['state'], 'success', job.get('error_code'))
        self.assertEqual(f.samples, 6)
        self.assertEqual(f.publications, 1)
        self.assertLess(f.elapsed, 60)
        self.assertEqual(f.a.snapshot()['owners'], {})
        self.assertEqual(f.runner.result('owned-job')['value']['response'], 'synthetic')
        self.assertGreater(f.persistence_seconds, 0)
        self.assertGreater(f.phase_costs['driver'], 0)
        self.assertGreater(f.authority_bytes, 17_000_000)
        print(json.dumps({'scenario': 'whole_worker_7s', 'samples': f.samples,
                          'virtual_seconds': f.elapsed, 'wall_seconds': f.wall,
                          'persistence_seconds': f.persistence_seconds,
                          'authority_bytes': f.authority_bytes, 'history_jobs': 1799,
                          'boundaries': f.boundaries, 'telemetry': 'synthetic'}))

    def test_pending_at_each_final_boundary_restabilizes_without_repeated_effects(self):
        for boundary in ('prepublication', 'postpublication', 'prerelease'):
            with self.subTest(boundary=boundary):
                # Small history here; full size covered above. 5.5 seconds leaves
                # room for one invalidated sample followed by three new samples.
                f = self.fixture(seconds=5.5, history=20, authority_bytes=30_000)
                original = OllamaCleanupGuard.sample; injected = []
                def turnover(guard, deadline):
                    value = original(guard, deadline)
                    if getattr(guard, 'boundary', None) == boundary and not injected:
                        injected.append(True); raise _ProcessPending()
                    return value
                with patch.object(OllamaCleanupGuard, 'sample', turnover):
                    job = self.run_worker(f)
                self.assertEqual(injected, [True]); self.assertEqual(job['state'], 'success')
                self.assertEqual(f.samples, 9); self.assertEqual(f.publications, 1)
                self.assertLess(f.elapsed, 60); self.assertEqual(f.a.snapshot()['owners'], {})

    def test_unsafe_final_compute_retains_owner_and_result_fence(self):
        f = self.fixture(seconds=1, history=20, authority_bytes=30_000)
        original = f.apps
        def apps():
            return GPU1+',999,1\n' if f.samples >= 5 else original()
        f.apps = apps
        job = self.run_worker(f)
        self.assertEqual(job['error_code'], 'ollama_cleanup_process_settling_pending')
        self.assertIn('gpu1', f.a.snapshot()['owners'])
        self.assertEqual(job['ollama_cleanup_failure']['boundary'], 'postpublication')
        with self.assertRaises(Exception): f.runner.result('owned-job')

    def test_final_turnover_without_enough_remaining_budget_keeps_lease(self):
        f = self.fixture(seconds=7, history=20, authority_bytes=30_000)
        original = OllamaCleanupGuard.sample; injected = []
        def turnover(guard, deadline):
            value = original(guard, deadline)
            if getattr(guard, 'boundary', None) == 'prerelease' and not injected:
                injected.append(True); raise _ProcessPending()
            return value
        with patch.object(OllamaCleanupGuard, 'sample', turnover):
            job = self.run_worker(f)
        self.assertEqual(injected, [True])
        self.assertEqual(job['error_code'], 'backend_deadline')
        self.assertIn('gpu1', f.a.snapshot()['owners'])
        self.assertEqual(job['ollama_cleanup_failure']['boundary'], 'prerelease')
        with self.assertRaises(Exception): f.runner.result('owned-job')

    def test_real_job_and_admission_contention_is_bounded_without_observation_under_lock(self):
        # Use real locks with a short holder; do not replace them with nullcontext.
        f = self.fixture(seconds=1, history=20, authority_bytes=30_000)
        for name in ('jobs.lock', 'admission.lock'):
            ready = threading.Event()
            def hold():
                with NativeLock(f.root/name).held():
                    ready.set(); REAL_SLEEP(.04)
            t = threading.Thread(target=hold); t.start(); self.assertTrue(ready.wait(2))
            start = REAL_MONOTONIC()
            with NativeLock(f.root/name).held(.5): pass
            t.join(2); self.assertFalse(t.is_alive())
            self.assertGreater(REAL_MONOTONIC()-start, .02)
        job = self.run_worker(f)
        self.assertEqual(job['state'], 'success'); self.assertEqual(f.a.snapshot()['owners'], {})
