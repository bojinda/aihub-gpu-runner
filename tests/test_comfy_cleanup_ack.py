"""Real loopback HTTP acknowledgements; synthetic ComfyUI work and native locks."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest

from aihub_gpu_runner.backends import ComfyBackend, HTTPTransport, Target, Uncertain
from aihub_gpu_runner.core import Admission, AtomicStore, Runner


@contextmanager
def comfy_server(*, media="image", status=200, drop=False, free_body=b"",
                 busy_before_free=False, busy_sample=None):
    state = {"calls": [], "free_calls": 0, "history_calls": 0, "queue_calls": 0,
             "cleanup_queue_calls": 0, "free_payload": None}
    node, key = ("2", "images") if media == "image" else ("104", "audio")
    filename = "synthetic.png" if media == "image" else "synthetic.mp3"
    mime = "image/png" if media == "image" else "audio/mpeg"
    artifact = b"\x89PNG\r\n\x1a\nsynthetic" if media == "image" else b"ID3synthetic"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def reply(self, code, body, content_type="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if code == 302:
                self.send_header("Location", "/redirected")
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            state["calls"].append(("POST", self.path))
            if self.path == "/prompt":
                self.reply(200, b'{"prompt_id":"owned-prompt"}')
            elif self.path == "/free":
                state["free_calls"] += 1
                state["free_payload"] = payload
                if drop:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    self.close_connection = True
                else:
                    self.reply(status, free_body)
            else:
                self.reply(404, b"")
        def do_GET(self):
            route = self.path.split("?", 1)[0]
            state["calls"].append(("GET", route))
            if route == "/queue":
                state["queue_calls"] += 1
                if state["free_calls"]:
                    state["cleanup_queue_calls"] += 1
                busy = (busy_before_free and state["queue_calls"] == 2 or
                        busy_sample is not None and state["free_calls"] and
                        state["cleanup_queue_calls"] == busy_sample)
                self.reply(200, json.dumps({"queue_running": [[0, "foreign"]] if busy else [],
                                           "queue_pending": []}).encode())
            elif route == "/history/owned-prompt":
                state["history_calls"] += 1
                terminal = state["history_calls"] > 1
                self.reply(200, json.dumps({"owned-prompt": {
                    "status": {"completed": terminal, "status_str": "success" if terminal else "running"},
                    "outputs": {node: {key: [{"filename": filename, "subfolder": "", "type": "output"}]}}
                }}).encode())
            elif route == "/view":
                self.reply(200, artifact, mime)
            else:
                self.reply(404, b"")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port), state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@contextmanager
def runner_for(url, media, probe, *, cleanup_timeout=.5):
    with tempfile.TemporaryDirectory(prefix="comfy-ack-") as directory:
        root = Path(directory)
        paths = {r: root / (r + ".lock") for r in ("gpu0", "gpu1")}
        for path in paths.values():
            path.touch()
        gate = Admission(AtomicStore(root / "state"), paths, "synthetic-ack-scope", require_existing_locks=True)
        gate.bootstrap({"approval_ref": "synthetic-only", "bootstrap_id": gate.snapshot()["bootstrap_id"],
                        "backend_quiescent": True, "cleanup_verified": True})
        name, node, key, mime = (("wallpaper", "2", "images", "image/png") if media == "image" else
                                 ("music", "104", "audio", "audio/mpeg"))
        workflow = {node: {"class_type": "SaveImage" if media == "image" else "SaveAudioMP3",
                           "inputs": {"filename_prefix": "synthetic"}}}
        target = Target(name, "comfy", ("gpu0",), url, workflow=workflow, output_node=node,
                        output_key=key, media_type=mime, idle_memory_mb=1200, cleanup_samples=3,
                        acquisition_timeout=1, execution_timeout=1, cleanup_timeout=cleanup_timeout)
        backend = ComfyBackend(target, HTTPTransport(url), probe=probe, poll_interval=.001)
        runner = Runner(gate, {name: target}, {name: backend}, poll_interval=.001)
        try:
            yield runner, gate, target
        finally:
            runner.close()


class CleanupAcknowledgementTests(unittest.TestCase):
    def test_real_http_200_empty_ack_is_accepted_and_json_stays_strict(self):
        with comfy_server() as (url, state):
            transport = HTTPTransport(url)
            payload = {"unload_models": True, "free_memory": True}
            self.assertIsNone(transport.ack("POST", "/free", payload))
            with self.assertRaises(Uncertain) as caught:
                transport.json("POST", "/free", payload)
            self.assertEqual(caught.exception.code, "invalid_backend_json")
            self.assertEqual(state["free_calls"], 2)
            self.assertEqual(state["free_payload"], payload)

    def test_ack_retains_http_error_redirect_and_network_failure_validation(self):
        for status, drop in ((403, False), (500, False), (302, False), (200, True)):
            with self.subTest(status=status, drop=drop), comfy_server(status=status, drop=drop) as (url, state):
                with self.assertRaises(Uncertain):
                    HTTPTransport(url).ack("POST", "/free", {})
                self.assertEqual(state["free_calls"], 1)
                self.assertNotIn(("GET", "/redirected"), state["calls"])

    def test_ack_keeps_the_existing_response_size_limit(self):
        with comfy_server(free_body=b"x" * 20) as (url, state):
            with self.assertRaises(Uncertain) as caught:
                HTTPTransport(url, max_bytes=8).ack("POST", "/free", {})
            self.assertEqual(caught.exception.code, "backend_response_too_large")

    def test_empty_free_ack_releases_both_targets_only_after_three_consecutive_idle_samples(self):
        for media in ("image", "audio"):
            with self.subTest(media=media), comfy_server(media=media) as (url, state):
                samples = iter([879, 1300, 879, 879, 879])
                observed = []
                def probe():
                    if not state["free_calls"]:
                        return 879  # Existing pre-submission residency check.
                    value = next(samples)
                    observed.append(value)
                    self.assertEqual(set(gate.snapshot()["owners"]), {"gpu0"})
                    self.assertEqual(gate.snapshot()["owners"]["gpu0"]["phase"], "cleanup_pending")
                    self.assertIsNone(gate.try_acquire("other", "hash", ("gpu0",), "other"))
                    return value
                with runner_for(url, media, probe) as (runner, gate, target):
                    runner.submit("owned-job", target.name, "workflow", target.workflow)
                    runner.future("owned-job").result(timeout=3)
                    job = runner.status("owned-job")
                    self.assertEqual(job["state"], "success", job)
                    self.assertEqual(observed, [879, 1300, 879, 879, 879])
                    self.assertEqual(job["cleanup_proof"]["stable_samples"], 3)
                    self.assertEqual(job["cleanup_proof"]["memory_mb"], 879)
                    self.assertEqual(gate.snapshot()["owners"], {})
                    self.assertEqual(state["free_calls"], 1)
                    self.assertEqual(state["free_payload"], {"unload_models": True, "free_memory": True})
                    self.assertEqual(state["history_calls"], 2)
                    self.assertLess(state["calls"].index(("GET", "/view")), state["calls"].index(("POST", "/free")))
                    self.assertTrue(runner.artifact("owned-job"))

    def test_failed_free_http_ack_keeps_cleanup_pending_and_durable_owner(self):
        for status, drop in ((403, False), (500, False), (302, False), (200, True)):
            with self.subTest(status=status, drop=drop), comfy_server(status=status, drop=drop) as (url, state):
                with runner_for(url, "image", lambda: 879) as (runner, gate, target):
                    runner.submit("owned-job", target.name, "workflow", target.workflow)
                    runner.future("owned-job").result(timeout=3)
                    job = runner.status("owned-job")
                    self.assertEqual(job["state"], "cleanup_pending")
                    self.assertEqual(job["error_code"], "comfy_cleanup_uncertain")
                    self.assertEqual(set(gate.snapshot()["owners"]), {"gpu0"})
                    self.assertIsNone(gate.try_acquire("other", "hash", ("gpu0",), "other"))
                    self.assertEqual(state["cleanup_queue_calls"], 0)

    def test_invalid_memory_observations_still_retain_cleanup_pending(self):
        for invalid in (None, -1, math.nan, True, "879"):
            with self.subTest(invalid=repr(invalid)), comfy_server() as (url, state):
                probe = lambda: invalid if state["free_calls"] else 879
                with runner_for(url, "image", probe) as (runner, gate, target):
                    runner.submit("owned-job", target.name, "workflow", target.workflow)
                    runner.future("owned-job").result(timeout=3)
                    job = runner.status("owned-job")
                    self.assertEqual(job["state"], "cleanup_pending")
                    self.assertEqual(job["error_code"], "invalid_gpu_cleanup_observation")
                    self.assertEqual(set(gate.snapshot()["owners"]), {"gpu0"})

    def test_nonidle_memory_expires_without_releasing_ownership(self):
        with comfy_server() as (url, state):
            probe = lambda: 1300 if state["free_calls"] else 879
            with runner_for(url, "image", probe, cleanup_timeout=.05) as (runner, gate, target):
                runner.submit("owned-job", target.name, "workflow", target.workflow)
                runner.future("owned-job").result(timeout=3)
                self.assertEqual(runner.status("owned-job")["state"], "cleanup_pending")
                self.assertEqual(set(gate.snapshot()["owners"]), {"gpu0"})
                self.assertEqual(state["free_calls"], 1)

    def test_busy_queue_resets_idle_sample_count(self):
        with comfy_server(busy_sample=2) as (url, state):
            observed = []
            def probe():
                if state["free_calls"]:
                    observed.append(879)
                    self.assertIn("gpu0", gate.snapshot()["owners"])
                return 879
            with runner_for(url, "image", probe) as (runner, gate, target):
                runner.submit("owned-job", target.name, "workflow", target.workflow)
                runner.future("owned-job").result(timeout=3)
                self.assertEqual(runner.status("owned-job")["state"], "success")
                self.assertEqual(len(observed), 5)
                self.assertEqual(gate.snapshot()["owners"], {})

    def test_busy_queue_before_cleanup_prevents_free_and_retains_owner(self):
        with comfy_server(busy_before_free=True) as (url, state):
            with runner_for(url, "image", lambda: 879) as (runner, gate, target):
                runner.submit("owned-job", target.name, "workflow", target.workflow)
                runner.future("owned-job").result(timeout=3)
                job = runner.status("owned-job")
                self.assertEqual(job["state"], "cleanup_pending")
                self.assertEqual(job["error_code"], "unexpected_work_before_cleanup")
                self.assertEqual(state["free_calls"], 0)
                self.assertIn("gpu0", gate.snapshot()["owners"])


if __name__ == "__main__":
    unittest.main()
