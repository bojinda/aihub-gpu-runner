import concurrent.futures
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from aihub_gpu_runner.core import Admission, AtomicStore, NativeLock, Runner, Conflict, RecoveryRequired
from aihub_gpu_runner.backends import Target, Execution, Uncertain, CleanupPending, OllamaBackend, ComfyBackend
from aihub_gpu_runner.api import Application


class FakeBackend:
    def __init__(self, entered=None, finish=None, error=None, cleanup_error=None):
        self.entered, self.finish = entered, finish
        self.error, self.cleanup_error = error, cleanup_error
        self.calls, self.cleanups = 0, 0
    def validate(self, operation, payload):
        if operation != "generate" or payload.get("model") != "synthetic":
            raise ValueError("invalid_operation")
    def execute(self, operation, payload, deadline, progress):
        self.calls += 1
        if self.entered:
            self.entered.set()
        if self.finish:
            if not self.finish.wait(2):
                raise AssertionError("offline fixture did not finish")
        if self.error:
            raise self.error
        return Execution({"done": True, "response": "synthetic"}, {"model": "synthetic"})
    def cleanup(self, execution, deadline):
        self.cleanups += 1
        if self.cleanup_error:
            raise self.cleanup_error
        return {"completion_verified": True, "cleanup_verified": True, "policy": "synthetic"}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = AtomicStore(self.root / "state")
        self.paths = {r: self.root / (r + ".lock") for r in ("gpu0", "gpu1")}
        self.gate = Admission(self.store, self.paths, "fixture-scope")
        self.gate.bootstrap({"approval_ref": "offline-only", "bootstrap_id": self.gate.snapshot()["bootstrap_id"],
                             "backend_quiescent": True, "cleanup_verified": True})
        self.runners = []
        self.release_events = []
        self.blocks = [
            patch("urllib.request.urlopen", side_effect=AssertionError("live HTTP forbidden")),
            patch("subprocess.run", side_effect=AssertionError("live process/GPU probe forbidden")),
            patch("subprocess.check_output", side_effect=AssertionError("live process/GPU probe forbidden")),
        ]
        for block in self.blocks:
            block.start()
    def tearDown(self):
        for event in self.release_events:
            event.set()
        for runner in self.runners:
            runner.close()
        for block in self.blocks:
            block.stop()
        self.tmp.cleanup()
    def runner(self, backends=None, timeout=.5):
        backends = backends or {"ollama": FakeBackend(), "wallpaper": FakeBackend()}
        targets = {
            "ollama": Target("ollama", "ollama", ("gpu1",), "http://127.0.0.1:11434",
                             models=("synthetic",), acquisition_timeout=timeout),
            "wallpaper": Target("wallpaper", "ollama", ("gpu0",), "http://127.0.0.1:8190",
                               models=("synthetic",), acquisition_timeout=timeout),
        }
        value = Runner(self.gate, targets, backends, workers=4, poll_interval=.005)
        self.runners.append(value)
        return value
    def submit(self, runner, name, target="ollama"):
        return runner.submit(name, target, "generate", {"model": "synthetic", "prompt": "neutral"})
    def test_fresh_gate_requires_explicit_local_bootstrap(self):
        store = AtomicStore(self.root / "fresh")
        gate = Admission(store, self.paths, "new-scope")
        self.assertIsNone(gate.try_acquire("fresh", "hash", ("gpu1",), "ollama"))
        with self.assertRaises(ValueError):
            gate.bootstrap({"backend_quiescent": True, "cleanup_verified": True})
    def test_same_resource_exclusion_and_different_resource_overlap(self):
        entered, finish, other = threading.Event(), threading.Event(), threading.Event()
        self.release_events.append(finish)
        first, second, independent = FakeBackend(entered, finish), FakeBackend(), FakeBackend(other)
        runner = self.runner({"ollama": first, "wallpaper": independent})
        self.submit(runner, "first")
        self.assertTrue(entered.wait(1))
        self.submit(runner, "second")
        self.submit(runner, "other", "wallpaper")
        self.assertTrue(other.wait(1))
        self.assertEqual(runner.wait("other", 1)["state"], "success")
        self.assertEqual(first.calls, 1)
        self.assertEqual(runner.status("second")["state"], "waiting")
        finish.set()
        self.assertEqual(runner.wait("first", 1)["state"], "success")
        self.assertEqual(runner.wait("second", 1)["state"], "success")
        self.assertEqual(first.calls, 2)
    def test_duplicate_same_payload_attaches_without_resubmission(self):
        runner = self.runner()
        self.submit(runner, "duplicate")
        runner.wait("duplicate", 1)
        self.submit(runner, "duplicate")
        self.assertEqual(runner.backends["ollama"].calls, 1)
        with self.assertRaises(Conflict):
            runner.submit("duplicate", "ollama", "generate", {"model": "synthetic", "prompt": "different"})
    def test_concurrent_duplicate_requests_submit_once(self):
        runner = self.runner()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: self.submit(runner, "concurrent"), range(16)))
        self.assertEqual(runner.wait("concurrent", 1)["state"], "success")
        self.assertEqual(runner.backends["ollama"].calls, 1)
    def test_client_wait_timeout_does_not_cancel_or_release_running_work(self):
        entered, finish = threading.Event(), threading.Event()
        self.release_events.append(finish)
        runner = self.runner({"ollama": FakeBackend(entered, finish), "wallpaper": FakeBackend()})
        self.submit(runner, "waiting-client")
        self.assertTrue(entered.wait(1))
        with self.assertRaises(TimeoutError):
            runner.wait("waiting-client", .01)
        self.assertEqual(runner.status("waiting-client")["state"], "running")
        self.assertIn("gpu1", self.gate.snapshot()["owners"])
        finish.set()
        self.assertEqual(runner.wait("waiting-client", 1)["state"], "success")
    def test_lost_backend_response_quarantines_free_os_lock(self):
        runner = self.runner({"ollama": FakeBackend(error=Uncertain("lost_response")),
                              "wallpaper": FakeBackend()})
        self.submit(runner, "lost")
        self.assertEqual(runner.wait("lost", 1)["state"], "reconciling")
        physical = NativeLock(self.paths["gpu1"])
        self.assertTrue(physical.acquire(0))
        physical.close()
        # Synthetic meeting/host launcher uses the same mandatory shared gate.
        self.assertIsNone(self.gate.try_acquire("host-stage", "hash", ("gpu1",), "meeting"))
        self.assertEqual(runner.backends["ollama"].calls, 1)
    def test_cleanup_failure_does_not_release_durable_owner(self):
        runner = self.runner({"ollama": FakeBackend(cleanup_error=CleanupPending("not_verified")),
                              "wallpaper": FakeBackend()})
        self.submit(runner, "cleanup-fail")
        self.assertEqual(runner.wait("cleanup-fail", 1)["state"], "cleanup_pending")
        self.assertIn("gpu1", self.gate.snapshot()["owners"])
        self.assertIsNone(self.gate.try_acquire("next", "hash", ("gpu1",), "ollama"))
    def test_supervisor_crash_restart_cannot_reuse_unresolved_gpu(self):
        runner = self.runner({"ollama": FakeBackend(error=SystemExit("synthetic crash")),
                              "wallpaper": FakeBackend()})
        self.submit(runner, "crashed")
        with self.assertRaises(SystemExit):
            runner.future("crashed").result(1)
        runner.close()
        self.runners.remove(runner)
        restarted = self.runner(timeout=.03)
        self.assertEqual(restarted.status("crashed")["state"], "reconciling")
        self.submit(restarted, "after-crash")
        self.assertEqual(restarted.wait("after-crash", 1)["state"], "failed")
        self.assertEqual(restarted.backends["ollama"].calls, 0)
    def test_recovery_requires_exact_owner_and_local_operator_evidence(self):
        runner = self.runner({"ollama": FakeBackend(error=Uncertain("lost_response")),
                              "wallpaper": FakeBackend()})
        self.submit(runner, "recover")
        runner.wait("recover", 1)
        owner = self.gate.snapshot()["owners"]["gpu1"]
        evidence = dict(owner, approval_ref="offline-only", backend_quiescent=True,
                        cleanup_verified=True, operator_approved_backend_recovery=True)
        with self.assertRaises(ValueError):
            self.gate.recover("recover", dict(evidence, lease_id="stale"))
        self.gate.recover("recover", evidence)
        lease = self.gate.try_acquire("next", "hash", ("gpu1",), "ollama")
        self.assertIsNotNone(lease)
        lease.release({"completion_verified": True, "cleanup_verified": True})
        lease.close()
    def test_active_owner_cannot_be_recovered(self):
        lease = self.gate.try_acquire("active", "hash", ("gpu1",), "ollama")
        try:
            owner = self.gate.snapshot()["owners"]["gpu1"]
            evidence = dict(owner, approval_ref="offline-only", backend_quiescent=True,
                            cleanup_verified=True, operator_approved_backend_recovery=True)
            with self.assertRaises(RecoveryRequired):
                self.gate.recover("active", evidence)
        finally:
            lease.release({"completion_verified": True, "cleanup_verified": True})
            lease.close()
    def test_queue_cancellation_does_not_interrupt_running_backend(self):
        entered, finish = threading.Event(), threading.Event()
        self.release_events.append(finish)
        runner = self.runner({"ollama": FakeBackend(entered, finish), "wallpaper": FakeBackend()})
        self.submit(runner, "running")
        self.assertTrue(entered.wait(1))
        self.submit(runner, "queued")
        self.assertTrue(runner.cancel("queued"))
        self.assertFalse(runner.cancel("running"))
        self.assertEqual(runner.wait("queued", 1)["state"], "cancelled")
        finish.set()
        runner.wait("running", 1)
        self.assertEqual(runner.backends["ollama"].calls, 1)
    def test_lock_inode_persists_and_unsafe_release_is_refused(self):
        lease = self.gate.try_acquire("identity", "hash", ("gpu1",), "ollama")
        inode = self.paths["gpu1"].stat().st_ino
        try:
            with self.assertRaises(ValueError):
                lease.release({"completion_verified": True, "cleanup_verified": False})
        finally:
            lease.release({"completion_verified": True, "cleanup_verified": True})
            lease.close()
        self.assertEqual(self.paths["gpu1"].stat().st_ino, inode)
    def test_api_authentication_and_no_admin_or_arbitrary_targets(self):
        runner = self.runner()
        app = Application(runner, "offline-token-only-not-production")
        body = json.dumps({"request_id": "api-job", "target": "ollama", "operation": "generate",
                           "payload": {"model": "synthetic", "prompt": "neutral"}}).encode()
        self.assertEqual(app.handle("POST", "/jobs", {}, body)[0], 401)
        headers = {"Authorization": "Bearer offline-token-only-not-production"}
        self.assertEqual(app.handle("POST", "/jobs", headers, body)[0], 202)
        runner.wait("api-job", 1)
        self.assertEqual(app.handle("GET", "/jobs/api-job/result", headers)[0], 200)
        self.assertEqual(app.handle("POST", "/recover", headers, b"{}")[0], 404)
        with self.assertRaises(ValueError):
            runner.submit("../escape", "ollama", "generate", {"model": "synthetic"})
        with self.assertRaises(ValueError):
            runner.submit("url", "http://evil.example", "generate", {"model": "synthetic"})


class ScriptedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def json(self, method, route, payload=None, timeout=1):
        self.calls.append((method, route, payload))
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value
    def ack(self, method, route, payload=None, timeout=1):
        self.json(method, route, payload, timeout)
    def binary(self, route, params, timeout=1):
        self.calls.append(("GET-BINARY", route, params))
        return self.responses.pop(0)


class BackendTests(unittest.TestCase):
    def test_ollama_known_completion_then_unload_and_residency_verification(self):
        target = Target("ollama", "ollama", ("gpu1",), "http://127.0.0.1:11434", models=("synthetic",))
        transport = ScriptedTransport([{"done": True, "model": "synthetic", "response": "ok"},
                                       {"done": True}, {"models": []}])
        backend = OllamaBackend(target, transport, poll_interval=.001)
        payload = {"model": "synthetic", "prompt": "neutral", "options": {"num_ctx": 16384}, "keep_alive": "30m"}
        backend.validate("generate", payload)
        result = backend.execute("generate", payload, time.monotonic()+1, lambda *args: None)
        proof = backend.cleanup(result, time.monotonic()+1)
        self.assertTrue(proof["cleanup_verified"])
        self.assertEqual(transport.calls[0][2]["options"]["num_ctx"], 16384)
        self.assertEqual(transport.calls[0][2]["keep_alive"], "30m")
        self.assertEqual(transport.calls[1][2]["keep_alive"], 0)
    def test_ollama_incomplete_or_lost_response_is_uncertain(self):
        target = Target("ollama", "ollama", ("gpu1",), "http://127.0.0.1:11434", models=("synthetic",))
        for response in ({"done": False}, TimeoutError("synthetic")):
            with self.subTest(response=type(response).__name__):
                backend = OllamaBackend(target, ScriptedTransport([response]))
                with self.assertRaises(Uncertain):
                    backend.execute("generate", {"model": "synthetic", "prompt": "neutral"},
                                    time.monotonic()+1, lambda *args: None)
    def workflow_target(self, media="image"):
        template = {"1": {"class_type": "SyntheticGenerate", "inputs": {"seed": 1, "prompt": "neutral"}},
                    "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "synthetic"}}}
        return Target("wallpaper", "comfy", ("gpu0",), "http://127.0.0.1:8190",
                      workflow=template, mutations={"1.seed": {"type": "integer", "minimum": 0},
                                                     "1.prompt": {"type": "string", "max_length": 100}},
                      output_node="2", output_key="images", media_type="image/png",
                      idle_memory_mb=1000, cleanup_samples=2)
    def test_comfy_waits_for_its_terminal_prompt_and_fetches_intended_output(self):
        target = self.workflow_target()
        pending = {"prompt-1": {"status": {"completed": False}, "outputs": {"2": {"images": [{"filename": "partial.png"}]}}}}
        finished = {"prompt-1": {"status": {"completed": True, "status_str": "success"},
                                "outputs": {"2": {"images": [{"filename": "synthetic.png", "subfolder": "", "type": "output"}]}}}}
        transport = ScriptedTransport([{"queue_running": [], "queue_pending": []},
                                       {"prompt_id": "prompt-1"}, pending, finished,
                                       (b"\x89PNG\r\n\x1a\nsynthetic", "image/png")])
        backend = ComfyBackend(target, transport, probe=lambda: 900, poll_interval=.001)
        backend.validate("workflow", target.workflow)
        result = backend.execute("workflow", target.workflow, time.monotonic()+1, lambda *args: None)
        self.assertEqual(result.handle["prompt_id"], "prompt-1")
        self.assertTrue(result.artifact.startswith(b"\x89PNG"))
        self.assertEqual([c[1] for c in transport.calls].count("/prompt"), 1)
    def test_comfy_cleanup_http_ack_alone_never_releases(self):
        target = self.workflow_target()
        transport = ScriptedTransport([{"queue_running": [], "queue_pending": []}, {}])
        backend = ComfyBackend(target, transport, probe=lambda: 2000, poll_interval=.001)
        with self.assertRaises(CleanupPending):
            backend.cleanup(Execution({}, {"prompt_id": "p"}), time.monotonic()+.01)
    def test_comfy_unknown_existing_work_is_quarantined_without_submission(self):
        target = self.workflow_target()
        transport = ScriptedTransport([{"queue_running": [[1, "other"]], "queue_pending": []}])
        backend = ComfyBackend(target, transport, probe=lambda: 0)
        with self.assertRaises(Uncertain):
            backend.execute("workflow", target.workflow, time.monotonic()+1, lambda *args: None)
        self.assertNotIn("/prompt", [c[1] for c in transport.calls])
    def test_workflow_contract_rejects_new_nodes_paths_and_link_changes(self):
        target = self.workflow_target()
        backend = ComfyBackend(target, ScriptedTransport([]), probe=lambda: 0)
        for changed in (
            dict(target.workflow, evil={"class_type": "ExecuteCommand", "inputs": {"command": "bad"}}),
            {"1": target.workflow["1"], "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "../escape"}}},
            {"1": target.workflow["1"], "2": {"class_type": "SaveImage", "inputs": {"images": ["evil", 0], "filename_prefix": "synthetic"}}},
        ):
            with self.subTest():
                with self.assertRaises(ValueError):
                    backend.validate("workflow", changed)


if __name__ == "__main__":
    unittest.main()
