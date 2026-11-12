"""Bounded command diagnostics; real disposable CPU processes, never GPU commands."""
import errno
import json
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock

from aihub_gpu_runner.backends import Uncertain
from aihub_gpu_runner.ollama_cleanup import cleanup_failure, OllamaCleanupGuard
from aihub_gpu_runner.recovery import RecoveryObserver


class CommandDiagnosticsTests(unittest.TestCase):
    def observer(self, run=subprocess.run):
        return RecoveryObserver({'docker': '/fixture/docker', 'journalctl': '/fixture/journalctl',
                                 'nvidia_smi': '/fixture/nvidia-smi', 'services': {
                                     'ollama': {'id': 'synthetic-id', 'name': 'ollama'}}}, {}, {}, run=run)

    def caught(self, observer, argv, deadline, code, category):
        with self.assertRaises(Uncertain) as caught: observer.command(argv, deadline)
        exc = caught.exception; self.assertEqual(exc.code, code)
        self.assertEqual(exc.observation_failure['category'], category)
        self.assertGreaterEqual(exc.observation_failure['elapsed_seconds'], 0)
        self.assertGreaterEqual(exc.observation_failure['remaining_seconds'], 0)
        return exc

    def test_expired_shared_budget_does_not_launch_a_command(self):
        run = Mock(); observer = self.observer(run)
        exc = self.caught(observer, ['/fixture/docker', 'inspect', 'ollama'], time.monotonic()-1,
                          'backend_deadline', 'budget_exhausted_before_launch')
        run.assert_not_called()
        self.assertEqual(exc.observation_failure['kind'], 'docker_inspect')
        self.assertEqual(exc.observation_failure['service'], 'ollama')

    def test_real_process_timeout_is_distinct(self):
        self.caught(self.observer(), [sys.executable, '-c', 'import time;time.sleep(2)'], time.monotonic()+.1,
                    'recovery_command_timeout', 'command_timeout')

    def test_real_nonzero_exit_exposes_only_bounded_returncode(self):
        exc = self.caught(self.observer(), [sys.executable, '-c', 'import sys;sys.stderr.write("private");sys.exit(7)'],
                          time.monotonic()+3, 'recovery_command_failed', 'nonzero_exit')
        self.assertEqual(exc.observation_failure['returncode'], 7)
        self.assertNotIn('private', json.dumps(exc.observation_failure))

    def test_missing_executable_and_permission_have_specific_errno_without_paths(self):
        for number in (errno.ENOENT, errno.EACCES):
            exc = self.caught(self.observer(Mock(side_effect=OSError(number, 'private/path'))),
                              ['/private/not-logged'], time.monotonic()+3, 'recovery_command_os_error', 'command_os_error')
            self.assertEqual(exc.observation_failure['errno'], number)
            self.assertNotIn('private', json.dumps(exc.observation_failure))

    def test_real_success_preserves_stdout(self):
        self.assertEqual(self.observer().command([sys.executable, '-c', 'print("okay")'], time.monotonic()+3), 'okay\n')

    def test_real_missing_executable_is_not_observation_uncertainty(self):
        with tempfile.TemporaryDirectory(prefix='command-missing-') as root:
            exc = self.caught(self.observer(), [root+'/absent'], time.monotonic()+3,
                              'recovery_command_os_error', 'command_os_error')
            self.assertEqual(exc.observation_failure['errno'], errno.ENOENT)

    def test_command_details_and_verification_boundary_reach_existing_failure_record(self):
        exc = self.caught(self.observer(Mock(side_effect=subprocess.TimeoutExpired('private', 1))),
                          ['/fixture/nvidia-smi', 'private'], time.monotonic()+2,
                          'recovery_command_timeout', 'command_timeout')
        guard = object.__new__(OllamaCleanupGuard)
        guard.phase = 'container_identity'; guard.service = guard.pid = None; guard.boundary = 'postpublication'
        detail = guard.failure(exc)
        self.assertEqual(detail['reason'], 'recovery_command_timeout')
        self.assertEqual(detail['boundary'], 'postpublication')
        self.assertEqual(detail['command']['category'], 'command_timeout')
        self.assertLess(len(json.dumps(detail)), 2048)
        self.assertNotIn('private', json.dumps(detail))
