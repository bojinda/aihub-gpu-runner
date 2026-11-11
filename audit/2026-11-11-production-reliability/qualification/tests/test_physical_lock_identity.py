"""Native inode-replacement tests; all state, holders and backends are synthetic."""
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from aihub_gpu_runner.config import admission_from_config
from aihub_gpu_runner.core import Admission, AtomicStore, NativeLock, RecoveryRequired, Runner, lock_identity
from aihub_gpu_runner.dual_gpu import restore_held, switch_binding
from aihub_gpu_runner.host_stage import HostOllamaClient
from aihub_gpu_runner.scope_transition import transition_scope
from tests.test_dual_gpu import fixture, FakeSwitch, FakeTransport
from tests.test_r1 import FakeBackend


def hold_physical_inode(path, ready, release):
    import fcntl
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ready.put(lock_identity(os.fstat(fd)))
        release.wait(10)
    finally:
        os.close(fd)


@unittest.skipUnless(sys.platform.startswith('linux'), 'native Linux point-of-use flock identity')
class PhysicalIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='physical-identity-')
        self.root = Path(self.tmp.name)
        self.old, self.dual, _, self.transition_evidence, _ = fixture(self.root)
        _, self.targets, self.gate = admission_from_config(self.old)
        self.holders = []

    def tearDown(self):
        for process, ready, release in self.holders:
            release.set()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join(5)
            ready.close()
            ready.join_thread()
        self.tmp.cleanup()

    def holder(self, path):
        context = multiprocessing.get_context('spawn')
        ready, release = context.Queue(), context.Event()
        process = context.Process(target=hold_physical_inode, args=(str(path), ready, release))
        process.start()
        self.holders.append((process, ready, release))
        return ready.get(timeout=5)

    def replace(self, path):
        path.rename(path.with_name(path.name + '.retained-inode'))
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)

    def acquire_after_replacement(self, victim):
        acquire = NativeLock.acquire
        def raced(lock, timeout=0):
            if lock.path == victim:
                self.replace(victim)
            return acquire(lock, timeout)
        return patch.object(NativeLock, 'acquire', raced)

    def test_admission_checks_acquired_fd_before_either_single_or_paired_ownership(self):
        for resources in (('gpu0',), ('gpu0', 'gpu1')):
            victim = self.gate.lock_paths[resources[-1]]
            before = self.gate.store.path('ownership.json').read_bytes()
            with self.acquire_after_replacement(victim), self.assertRaisesRegex(
                    RecoveryRequired, 'physical_lock_identity_changed_during_acquisition'):
                self.gate.try_acquire('race', 'synthetic', resources, 'synthetic')
            self.assertEqual(self.gate.store.path('ownership.json').read_bytes(), before)
            # Failure released every acquired descriptor, including the first of a pair.
            for resource in resources:
                lock = NativeLock(self.gate.lock_paths[resource], create=False)
                self.assertTrue(lock.acquire(0))
                lock.close()
            victim.unlink()
            victim.with_name(victim.name + '.retained-inode').rename(victim)

    def test_runner_replacement_between_stat_and_acquire_never_calls_backend(self):
        backend = FakeBackend()
        runner = Runner(self.gate, self.targets, {'ollama': backend}, poll_interval=.005)
        victim = self.gate.lock_paths['gpu1']
        try:
            with self.acquire_after_replacement(victim):
                runner.submit('runner-race', 'ollama', 'generate', {'model': 'synthetic', 'prompt': 'synthetic'})
                job = runner.wait('runner-race', 2)
            self.assertEqual(job['state'], 'reconciling')
            self.assertEqual(job['error_code'], 'admission_or_storage_failure')
            self.assertEqual(self.gate.snapshot()['owners'], {})
            self.assertEqual((backend.calls, backend.cleanups), (0, 0))
        finally:
            runner.close()

    def test_dual_restore_checks_acquired_fd_before_backend_or_journal_change(self):
        transition_scope(self.old, self.dual, self.transition_evidence)
        _, targets, gate = admission_from_config(self.dual)
        target = targets['meeting-dual']
        lease = gate.try_acquire('dual-race', 'hash', target.resources, target.name)
        lease.mark('cleanup_pending')
        owner = gate.snapshot()['owners']['gpu1']
        gate.store.put_job(dict(request_id='dual-race', state='cleanup_pending', dual_gpu_session=True,
            switch_binding=switch_binding(target.mode_switch)))
        lease.close()
        evidence = dict(owner, approval_ref='synthetic', operator_approved_dual_restoration=True,
            backend_work_resolved=True, observed_mode='dual', switch_binding=switch_binding(target.mode_switch))
        journal = gate.store.path('ownership.json').read_bytes()
        job = gate.store.path('jobs/dual-race.json').read_bytes()
        controller = FakeSwitch(target)
        with self.acquire_after_replacement(gate.lock_paths['gpu1']), self.assertRaisesRegex(
                RecoveryRequired, 'physical_lock_identity_changed_during_acquisition'):
            restore_held(gate, target, 'dual-race', evidence, controller)
        self.assertEqual(controller.calls, [])
        self.assertEqual(gate.store.path('ownership.json').read_bytes(), journal)
        self.assertEqual(gate.store.path('jobs/dual-race.json').read_bytes(), job)

    def host_client(self, dual=False):
        if dual:
            transition_scope(self.old, self.dual, self.transition_evidence)
            _, self.targets, self.gate = admission_from_config(self.dual)
        target = self.targets['meeting-dual' if dual else 'ollama']
        lease = self.gate.try_acquire('host-race', 'hash', target.resources, target.name)
        lease.mark('running')
        owner = self.gate.snapshot()['owners']['gpu1']
        self.gate.store.put_job(dict(request_id='host-race', state='running', host_stage=True,
            host_requests=[], lease_id=owner['lease_id'], dual_gpu_session=dual,
            mode_phase='dual_ready' if dual else None))
        lease.close()
        transport = FakeTransport()
        client = HostOllamaClient(self.gate, target, 'host-race', owner['lease_id'], transport)
        return client, transport

    def test_host_accepts_actual_supervisor_contention_on_recorded_descriptor(self):
        client, transport = self.host_client()
        victim = self.gate.lock_paths['gpu1']
        expected = self.gate.snapshot()['lock_identity']['gpu1']
        self.assertEqual(self.holder(victim), expected)
        client.generate(client.target.base_url, {'model': 'synthetic-map', 'prompt': 'synthetic'})
        self.assertEqual(len(transport.calls), 1)

    def test_host_free_recorded_inode_refused_and_probe_descriptor_released(self):
        client, transport = self.host_client()
        before = self.gate.store.path('ownership.json').read_bytes()
        job = self.gate.store.path('jobs/host-race.json').read_bytes()
        with self.assertRaisesRegex(RecoveryRequired, 'host_supervisor_lock_not_held'):
            client.generate(client.target.base_url, {'model': 'synthetic-map', 'prompt': 'synthetic'})
        self.assertEqual(transport.calls, [])
        self.assertEqual(self.gate.store.path('ownership.json').read_bytes(), before)
        self.assertEqual(self.gate.store.path('jobs/host-race.json').read_bytes(), job)
        lock = NativeLock(self.gate.lock_paths['gpu1'], create=False)
        self.assertTrue(lock.acquire(0))
        lock.close()

    def test_host_replacement_with_busy_wrong_inode_never_authorizes_backend(self):
        self.assert_host_replacement_refused('gpu1')

    def test_dual_host_gpu0_busy_wrong_inode_never_authorizes_backend(self):
        self.assert_host_replacement_refused('gpu0', dual=True)

    def test_dual_host_gpu1_busy_wrong_inode_never_authorizes_backend(self):
        self.assert_host_replacement_refused('gpu1', dual=True)

    def assert_host_replacement_refused(self, resource, dual=False):
        client, transport = self.host_client(dual=dual)
        victim = self.gate.lock_paths[resource]
        expected = self.gate.snapshot()['lock_identity'][resource]
        for name in client.target.resources:
            self.assertEqual(self.holder(self.gate.lock_paths[name]),
                             self.gate.snapshot()['lock_identity'][name])
        journal = self.gate.store.path('ownership.json').read_bytes()
        job = self.gate.store.path('jobs/host-race.json').read_bytes()
        open_file = os.open
        replaced = False
        def raced_open(path, flags, *args, **kwargs):
            nonlocal replaced
            if Path(path) == victim and not replaced:
                replaced = True
                self.replace(victim)
                self.assertNotEqual(self.holder(victim)['inode'], expected['inode'])
            return open_file(path, flags, *args, **kwargs)
        with patch('os.open', raced_open), self.assertRaises(RecoveryRequired):
            client.generate(client.target.base_url, {'model': 'synthetic-map', 'prompt': 'synthetic'})
        self.assertTrue(replaced)
        self.assertEqual(transport.calls, [])
        self.assertEqual(self.gate.store.path('ownership.json').read_bytes(), journal)
        self.assertEqual(self.gate.store.path('jobs/host-race.json').read_bytes(), job)

    def test_bootstrap_records_held_descriptor_and_rejects_path_replacement(self):
        store = AtomicStore(self.root / 'fresh-state')
        gate = Admission(store, self.gate.lock_paths, 'fresh-synthetic-scope', require_existing_locks=True)
        before = store.path('ownership.json').read_bytes()
        acquire = NativeLock.acquire
        victim = gate.lock_paths['gpu1']
        def replace_after_acquire(lock, timeout=0):
            result = acquire(lock, timeout)
            if lock.path == victim and result:
                self.replace(victim)
            return result
        with patch.object(NativeLock, 'acquire', replace_after_acquire), self.assertRaises(RecoveryRequired):
            gate.bootstrap(dict(approval_ref='synthetic', bootstrap_id=gate.snapshot()['bootstrap_id'],
                backend_quiescent=True, cleanup_verified=True))
        self.assertEqual(store.path('ownership.json').read_bytes(), before)
        self.assertFalse(gate.snapshot()['ready'])


if __name__ == '__main__':
    unittest.main()
