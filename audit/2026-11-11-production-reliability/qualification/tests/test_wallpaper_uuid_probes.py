"""Physical UUID selection and identity failure paths; no real nvidia-smi calls."""
import copy
import subprocess
import time
import unittest
from unittest.mock import patch

from aihub_gpu_runner.backends import NvidiaUUIDMemoryProbe, NvidiaMemoryProbe, ComfyBackend, Uncertain
from aihub_gpu_runner.config import make_runner
from tests import test_wallpaper_conditioning as fixtures
GPU0, GPU1, WORKFLOW, Transport = fixtures.GPU0, fixtures.GPU1, fixtures.WORKFLOW, fixtures.Transport


class UUIDProbeTests(unittest.TestCase):
    def test_queries_full_uuid_and_checks_returned_identity_without_numeric_indices(self):
        with patch('aihub_gpu_runner.backends.subprocess.check_output', return_value=GPU0 + ', 763\n') as command:
            self.assertEqual(NvidiaUUIDMemoryProbe(GPU0)(), 763)
        args = command.call_args.args[0]
        self.assertIn('--id=' + GPU0, args)
        self.assertIn('--query-gpu=uuid,memory.used', args)
        self.assertNotIn('--id=0', args)
        self.assertNotIn('--id=1', args)

    def test_missing_wrong_duplicated_or_invalid_device_observations_fail_closed(self):
        for output in ('', GPU1 + ', 1\n', GPU0 + ', 1\n' + GPU0 + ', 2\n',
                       GPU0 + ', N/A\n', GPU0 + ', nan\n', GPU0 + ', -1\n', '0, 1\n'):
            with self.subTest(output=output), patch('aihub_gpu_runner.backends.subprocess.check_output', return_value=output):
                with self.assertRaises(Uncertain):
                    NvidiaUUIDMemoryProbe(GPU0)()
        with patch('aihub_gpu_runner.backends.subprocess.check_output', side_effect=subprocess.CalledProcessError(1, ['synthetic'])):
            with self.assertRaises(Uncertain):
                NvidiaUUIDMemoryProbe(GPU0)()
        for value in ('0', 'GPU-short', '', None):
            with self.subTest(uuid=value), self.assertRaises(ValueError):
                NvidiaUUIDMemoryProbe(value)

    def test_legacy_single_gpu_probe_command_and_value_are_unchanged(self):
        with patch('aihub_gpu_runner.backends.subprocess.check_output', return_value='763\n') as command:
            self.assertEqual(NvidiaMemoryProbe(0)(), 763)
        self.assertEqual(command.call_args.args[0], ['nvidia-smi', '--id=0',
            '--query-gpu=memory.used', '--format=csv,noheader,nounits'])


class UUIDFailurePaths(unittest.TestCase):
    setUp = fixtures.WallpaperConditioningTests.setUp
    tearDown = fixtures.WallpaperConditioningTests.tearDown
    target = fixtures.WallpaperConditioningTests.target
    runner = fixtures.WallpaperConditioningTests.runner
    submit = fixtures.WallpaperConditioningTests.submit
    # Exercise separate real Runner/Admission instances and disposable flocks,
    # substituting ONLY the nvidia-smi response at its OS-command boundary.
    def uuid_runner(self, transport):
        runner, _ = self.runner(transport)
        backend = runner.backends['wallpaper']
        backend.probe = NvidiaUUIDMemoryProbe(GPU0)
        backend.conditioning_probe = NvidiaUUIDMemoryProbe(GPU1)
        return runner

    def test_wrong_physical_gpu_before_submission_retains_both_without_backend_post(self):
        for index, bad in enumerate((GPU0, GPU1)):
            if index:
                self.tearDown(); self.setUp()
            transport = Transport()
            runner = self.uuid_runner(transport)
            def response(args, **kwargs):
                identity = args[1].split('=', 1)[1]
                return (GPU1 if bad == GPU0 else GPU0) + ', 1\n' if identity == bad else identity + ', 1\n'
            with self.subTest(device=bad), patch('aihub_gpu_runner.backends.subprocess.check_output', side_effect=response):
                job = self.submit(runner)
            self.assertEqual(job['state'], 'reconciling')
            self.assertEqual(job['error_code'], 'physical_gpu_uuid_mismatch')
            self.assertEqual(transport.submissions, 0)
            self.assertEqual(transport.frees, 0)
            self.assertEqual(set(self.gate.snapshot()['owners']), {'gpu0', 'gpu1'})

    def test_gpu1_disappearance_or_uuid_change_at_cleanup_never_releases_either_owner(self):
        failures = [(gpu, failure) for gpu in (GPU0, GPU1) for failure in ('missing', 'swapped')]
        for index, (bad, failure) in enumerate(failures):
            if index:
                self.tearDown(); self.setUp()
            transport = Transport()
            runner = self.uuid_runner(transport)
            def response(args, **kwargs):
                identity = args[1].split('=', 1)[1]
                if transport.phase == 'cleanup' and identity == bad:
                    if failure == 'missing':
                        raise subprocess.CalledProcessError(1, ['synthetic'])
                    return (GPU1 if bad == GPU0 else GPU0) + ', 1\n'
                return identity + ', 1\n'
            with self.subTest(device=bad, failure=failure), patch('aihub_gpu_runner.backends.subprocess.check_output', side_effect=response):
                job = self.submit(runner)
                self.assertEqual(job['state'], 'cleanup_pending')
                self.assertEqual(set(self.gate.snapshot()['owners']), {'gpu0', 'gpu1'})

    def test_late_final_uuid_observation_does_not_authorize_release_after_deadline(self):
        transport = Transport()
        runner = self.uuid_runner(transport)
        count = 0
        def response(args, **kwargs):
            nonlocal count
            identity = args[1].split('=', 1)[1]
            if transport.phase == 'cleanup' and identity == GPU1:
                count += 1
                if count == 3:
                    time.sleep(.08)  # Synthetic delay exceeds this fixture's .06-second cleanup window.
            return identity + ', 1\n'
        with patch('aihub_gpu_runner.backends.subprocess.check_output', side_effect=response):
            job = self.submit(runner)
        self.assertEqual(job['state'], 'cleanup_pending')
        self.assertEqual(set(self.gate.snapshot()['owners']), {'gpu0', 'gpu1'})

    def test_numeric_enumeration_changes_do_not_redirect_uuid_bound_observations(self):
        runner = self.uuid_runner(Transport())
        selected = []
        def response(args, **kwargs):
            # Host GPU indices may now be reversed; this API never selects them.
            identity = args[1].split('=', 1)[1]
            self.assertIn(identity, (GPU0, GPU1))
            selected.append(identity)
            return identity + ', 1\n'
        with patch('aihub_gpu_runner.backends.subprocess.check_output', side_effect=response):
            job = self.submit(runner)
        self.assertEqual(job['state'], 'success')
        self.assertEqual(set(selected), {GPU0, GPU1})
        self.assertEqual(job['cleanup_proof']['physical_gpu_uuids'], {'gpu0': GPU0, 'gpu1': GPU1})
        self.assertEqual(self.gate.snapshot()['owners'], {})

    def test_missing_duplicate_or_mutable_workflow_gpu_identities_are_rejected(self):
        for change in (lambda w: w['3']['inputs'].pop('gpu1_uuid'),
                       lambda w: w['3']['inputs'].update(gpu1_uuid=GPU0),
                       lambda w: w['3']['inputs'].update(gpu0_uuid='0')):
            workflow = copy.deepcopy(WORKFLOW); change(workflow)
            with self.assertRaises(ValueError):
                self.target(workflow=workflow)
        with self.assertRaisesRegex(ValueError, 'fixed_wallpaper_conditioning'):
            self.target(mutations={'3.gpu1_uuid': {'type': 'enum', 'values': [GPU1]}})


if __name__ == '__main__':
    unittest.main()
