import copy
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from aihub_gpu_runner.core import Admission, AtomicStore, NativeLock, Runner, RecoveryRequired, strict_json
from aihub_gpu_runner.backends import Target, Execution, Uncertain, CleanupPending, ComfyBackend, OllamaBackend
from aihub_gpu_runner.config import load_config
from aihub_gpu_runner.api import Application
from tests import test_r1 as fixture
FakeBackend, ScriptedTransport = fixture.FakeBackend, fixture.ScriptedTransport

class AdditionalCoreTests(fixture.CoreTests):
    # Only run the added cases; imported baseline is discovered separately.
    def test_owner_record_write_failure_after_effect_never_submits(self):
        runner = self.runner()
        original = self.store.write
        fired = False
        def fail_after_effect(name, value):
            nonlocal fired
            original(name, value)
            if name == "ownership.json" and value.get("owners") and not fired:
                fired = True
                raise OSError("synthetic write error after effect")
        with patch.object(self.store, "write", side_effect=fail_after_effect):
            self.submit(runner, "journal-fault")
            self.assertEqual(runner.wait("journal-fault", 1)["state"], "reconciling")
        self.assertEqual(runner.backends["ollama"].calls, 0)
        self.assertIn("gpu1", self.gate.snapshot()["owners"])
        physical = NativeLock(self.paths["gpu1"])
        self.assertTrue(physical.acquire(0))
        physical.close()
        self.assertIsNone(self.gate.try_acquire("next", "hash", ("gpu1",), "ollama"))
    def test_owner_clear_write_failure_before_or_after_effect_is_not_a_blind_retry(self):
        for after_effect in (False,True):
            with self.subTest(after_effect=after_effect):
                # Each iteration needs an independent controller/state.
                store=AtomicStore(self.root/("clear-"+str(after_effect)))
                paths={r:self.root/("clear-"+str(after_effect)+r+".lock") for r in ("gpu0","gpu1")}
                gate=Admission(store,paths,"clear-scope")
                gate.bootstrap({"approval_ref":"offline","bootstrap_id":gate.snapshot()["bootstrap_id"],
                                "backend_quiescent":True,"cleanup_verified":True})
                target=Target("ollama","ollama",("gpu1",),"http://127.0.0.1:11434",models=("synthetic",))
                backend=FakeBackend()
                runner=Runner(gate,{"ollama":target},{"ollama":backend},poll_interval=.005)
                self.runners.append(runner)
                original=store.write
                fired=False
                def fail_clear(name,value):
                    nonlocal fired
                    if name=="ownership.json" and not value.get("owners") and not fired:
                        fired=True
                        if after_effect:
                            original(name,value)
                        raise OSError("synthetic clear failure")
                    original(name,value)
                with patch.object(store,"write",side_effect=fail_clear):
                    self.submit(runner,"clear")
                    job=runner.wait("clear",1)
                    runner.future("clear").result(1)
                    job=runner.status("clear")
                self.assertEqual(backend.calls,1)
                if after_effect:
                    self.assertEqual(job["state"],"success")
                    self.assertEqual(runner.result("clear")["value"]["response"],"synthetic")
                    self.assertEqual(gate.snapshot()["owners"],{})
                else:
                    self.assertEqual(job["state"],"cleanup_pending")
                    self.assertIn("gpu1",gate.snapshot()["owners"])
                self.submit(runner,"clear")
                self.assertEqual(backend.calls,1)
    def test_definitive_backend_failure_still_requires_cleanup_before_handoff(self):
        class Failed(FakeBackend):
            def execute(self, *args):
                self.calls += 1
                return Execution({}, {"model": "synthetic"}, error_code="known_backend_failure")
        backend = Failed()
        runner = self.runner({"ollama": backend, "wallpaper": FakeBackend()})
        self.submit(runner, "failed")
        self.assertEqual(runner.wait("failed", 1)["state"], "failed")
        runner.future("failed").result(1)
        self.assertEqual(backend.cleanups, 1)
        self.assertNotIn("gpu1", self.gate.snapshot()["owners"])
        self.assertEqual(runner.result("failed")["state"], "failed")
    def test_backend_exception_does_not_echo_secret_message(self):
        backend = FakeBackend(error=RuntimeError("DO_NOT_ECHO_SECRET"))
        runner = self.runner({"ollama": backend, "wallpaper": FakeBackend()})
        self.submit(runner, "redact")
        self.assertEqual(runner.wait("redact", 1)["state"], "reconciling")
        self.assertNotIn("DO_NOT_ECHO_SECRET", json.dumps(runner.status("redact")))
    def test_mixed_resource_attempt_is_all_or_none(self):
        external = NativeLock(self.paths["gpu1"])
        self.assertTrue(external.acquire(0))
        try:
            self.assertIsNone(self.gate.try_acquire("dual-future", "hash", ("gpu0", "gpu1"), "synthetic"))
            self.assertEqual(self.gate.snapshot()["owners"], {})
            available = NativeLock(self.paths["gpu0"])
            self.assertTrue(available.acquire(0))
            available.close()
        finally:
            external.close()
    def test_corrupted_or_missing_ownership_never_becomes_free(self):
        self.store.write("ownership.json", {"schema": 999})
        with self.assertRaises(RecoveryRequired):
            self.gate.try_acquire("bad-state", "hash", ("gpu1",), "ollama")
    def test_supervisor_is_single_instance(self):
        first = self.runner()
        with self.assertRaises(Exception):
            self.runner()
        self.assertFalse(first.closed)
    def test_api_disconnect_does_not_cancel_supervision(self):
        entered, finish = threading.Event(), threading.Event()
        self.release_events.append(finish)
        backend = FakeBackend(entered, finish)
        runner = self.runner({"ollama": backend, "wallpaper": FakeBackend()})
        app = Application(runner, "offline-token-only-not-production")
        headers = {"Authorization": "Bearer offline-token-only-not-production"}
        body = json.dumps({"request_id": "disconnect", "target": "ollama",
            "operation": "generate", "payload": {"model": "synthetic", "prompt": "neutral"}}).encode()
        response = app.handle("POST", "/jobs", headers, body)
        self.assertEqual(response[0], 202)
        # The client discards the accepted response; the independent worker remains.
        self.assertTrue(entered.wait(1))
        finish.set()
        self.assertEqual(runner.wait("disconnect", 1)["state"], "success")
        self.assertEqual(backend.calls, 1)
    def test_api_duplicate_keys_and_invalid_target_types_rejected(self):
        runner = self.runner()
        app = Application(runner, "offline-token-only-not-production")
        headers = {"Authorization": "Bearer offline-token-only-not-production"}
        duplicate = b'{"request_id":"one","request_id":"two","target":"ollama","operation":"generate","payload":{}}'
        self.assertEqual(app.handle("POST", "/jobs", headers, duplicate)[0], 400)
        invalid = json.dumps({"request_id":"bad-target","target":[],"operation":"generate","payload":{}}).encode()
        self.assertEqual(app.handle("POST", "/jobs", headers, invalid)[0], 400)
        self.assertEqual(runner.backends["ollama"].calls, 0)
    def test_api_result_unavailable_until_verified_cleanup(self):
        class DelayedCleanup(FakeBackend):
            def cleanup(self, execution, deadline):
                cleanup_entered.set()
                if not finish.wait(1):
                    raise AssertionError("fixture timeout")
                return super().cleanup(execution, deadline)
        cleanup_entered, finish = threading.Event(), threading.Event()
        self.release_events.append(finish)
        runner = self.runner({"ollama": DelayedCleanup(), "wallpaper": FakeBackend()})
        self.submit(runner, "cleanup-wait")
        self.assertTrue(cleanup_entered.wait(1))
        app = Application(runner, "offline-token-only-not-production")
        headers={"Authorization":"Bearer offline-token-only-not-production"}
        self.assertEqual(app.handle("GET","/jobs/cleanup-wait/result",headers)[0],409)
        self.assertIn("gpu1", self.gate.snapshot()["owners"])
        finish.set()
        self.assertEqual(runner.wait("cleanup-wait",1)["state"],"success")
    def test_new_admission_instance_observes_old_uncertain_owner(self):
        runner = self.runner({"ollama": FakeBackend(error=Uncertain("uncertain")), "wallpaper": FakeBackend()})
        self.submit(runner, "persistent")
        runner.wait("persistent",1)
        other = Admission(AtomicStore(self.store.root),self.paths,"fixture-scope")
        self.assertIsNone(other.try_acquire("independent-host-stage","hash",("gpu1",),"meeting"))
    def test_future_scope_change_requires_review(self):
        with self.assertRaises(RecoveryRequired):
            Admission(self.store,self.paths,"changed-backends-or-lock-configuration")
    def test_replaced_physical_lock_cannot_be_accepted_as_same_resource(self):
        path=self.paths["gpu1"]
        original_inode=path.stat().st_ino
        old=path.with_name("retained-old-lock")
        path.rename(old)
        path.write_bytes(b"")
        self.assertNotEqual(path.stat().st_ino,original_inode)
        with self.assertRaises(RecoveryRequired):
            self.gate.try_acquire("wrong-inode","hash",("gpu1",),"ollama")
        self.assertEqual(self.gate.snapshot()["owners"],{})
    def test_production_admission_does_not_create_missing_physical_locks(self):
        missing={"gpu0":self.root/"missing0.lock","gpu1":self.root/"missing1.lock"}
        with self.assertRaises(RecoveryRequired):
            Admission(AtomicStore(self.root/"production-check"),missing,"scope",require_existing_locks=True)
        self.assertFalse(missing["gpu0"].exists())
    def test_gpu1_waiters_do_not_consume_gpu0_worker_capacity(self):
        entered,finish,other=threading.Event(),threading.Event(),threading.Event()
        self.release_events.append(finish)
        targets={"ollama":Target("ollama","ollama",("gpu1",),"http://127.0.0.1:11434",models=("synthetic",),acquisition_timeout=1),
                 "wallpaper":Target("wallpaper","ollama",("gpu0",),"http://127.0.0.1:8190",models=("synthetic",),acquisition_timeout=1)}
        runner=Runner(self.gate,targets,{"ollama":FakeBackend(entered,finish),"wallpaper":FakeBackend(other)},workers=2,poll_interval=.005)
        self.runners.append(runner)
        self.submit(runner,"occupied")
        self.assertTrue(entered.wait(1))
        self.submit(runner,"gpu1-waiter")
        self.submit(runner,"free-gpu0","wallpaper")
        self.assertTrue(other.wait(1))
        self.assertEqual(runner.wait("free-gpu0",1)["state"],"success")
        self.assertEqual(runner.status("gpu1-waiter")["state"],"waiting")
        finish.set()
        runner.wait("occupied",1)
        runner.wait("gpu1-waiter",1)
    def test_acquisition_deadline_does_not_submit_after_expiry(self):
        physical=NativeLock(self.paths["gpu1"])
        self.assertTrue(physical.acquire(0))
        runner=self.runner(timeout=.03)
        try:
            self.submit(runner,"expires")
            result=runner.wait("expires",1)
            self.assertEqual(result["state"],"failed")
            self.assertEqual(result["error_code"],"resource_acquisition_timeout")
            self.assertEqual(runner.backends["ollama"].calls,0)
        finally:
            physical.close()

# Prevent inherited baseline tests from being repeated in this added class.
for _name in list(fixture.CoreTests.__dict__):
    if _name.startswith("test_"):
        setattr(AdditionalCoreTests, _name, None)

class AdditionalBackendTests(unittest.TestCase):
    def target(self):
        return fixture.BackendTests.workflow_target(self)
    def test_comfy_cleanup_requires_stable_calibrated_observations(self):
        target=self.target()
        queue={"queue_running":[],"queue_pending":[]}
        transport=ScriptedTransport([queue,{},queue,queue,queue])
        observations=iter([1500,900,900])
        backend=ComfyBackend(target,transport,probe=lambda:next(observations),poll_interval=.001)
        proof=backend.cleanup(Execution({},{"prompt_id":"owned"}),time.monotonic()+1)
        self.assertEqual(proof["stable_samples"],2)
        self.assertEqual(proof["prompt_id"],"owned")
        self.assertEqual(len([c for c in transport.calls if c[0]=="POST"]),1)
    def test_comfy_refuses_cleanup_with_foreign_queue_work(self):
        target=self.target()
        transport=ScriptedTransport([{"queue_running":[[0,"foreign"]],"queue_pending":[]}])
        backend=ComfyBackend(target,transport,probe=lambda:0)
        with self.assertRaises(CleanupPending):
            backend.cleanup(Execution({},{"prompt_id":"owned"}),time.monotonic()+1)
        self.assertFalse(any(c[0]=="POST" for c in transport.calls))
    def test_comfy_terminal_error_is_known_failure_not_immediate_release(self):
        target=self.target()
        transport=ScriptedTransport([{"queue_running":[],"queue_pending":[]},
            {"prompt_id":"p"},{"p":{"status":{"completed":True,"status_str":"error"}}}])
        backend=ComfyBackend(target,transport,probe=lambda:0)
        result=backend.execute("workflow",target.workflow,time.monotonic()+1,lambda *a:None)
        self.assertEqual(result.error_code,"comfy_execution_failed")
        self.assertFalse(any(c[1]=="/free" for c in transport.calls))
    def test_comfy_wrong_artifact_or_path_never_becomes_success(self):
        target=self.target()
        for descriptor in (
            {"filename":"../secret.png","subfolder":"","type":"output"},
            {"filename":"wrong.wav","subfolder":"","type":"output"},
            {"filename":"synthetic.png","subfolder":"","type":"input"},
        ):
            with self.subTest(descriptor=descriptor["type"]):
                transport=ScriptedTransport([{"queue_running":[],"queue_pending":[]},
                    {"prompt_id":"p"},{"p":{"status":{"completed":True,"status_str":"success"},
                     "outputs":{"2":{"images":[descriptor]}}}},(b"bad","image/png")])
                result=ComfyBackend(target,transport,probe=lambda:0).execute(
                    "workflow",target.workflow,time.monotonic()+1,lambda *a:None)
                self.assertEqual(result.error_code,"artifact_retrieval_or_validation_failed")
    def test_comfy_music_mp3_selection_uses_named_output(self):
        target=Target("music","comfy",("gpu0",),"http://127.0.0.1:8188",
            workflow={"104":{"class_type":"SaveAudioMP3","inputs":{"filename_prefix":"synthetic"}}},
            output_node="104",output_key="audio",media_type="audio/mpeg",
            idle_memory_mb=1000,cleanup_samples=2)
        descriptor={"filename":"synthetic.mp3","subfolder":"audio","type":"output"}
        transport=ScriptedTransport([{"queue_running":[],"queue_pending":[]},{"prompt_id":"p"},
          {"p":{"status":{"completed":True,"status_str":"success"},
           "outputs":{"wrong":{"audio":[{"filename":"wrong.wav"}]},"104":{"audio":[descriptor]}}}},
          (b"ID3synthetic","audio/mpeg")])
        result=ComfyBackend(target,transport,probe=lambda:0).execute(
            "workflow",target.workflow,time.monotonic()+1,lambda *a:None)
        self.assertEqual(result.media_type,"audio/mpeg")
        self.assertEqual(result.value["filename"],"synthetic.mp3")
    def test_ollama_failed_unload_never_proves_release(self):
        target=Target("ollama","ollama",("gpu1",),"http://127.0.0.1:11434",models=("synthetic",))
        backend=OllamaBackend(target,ScriptedTransport([{"done":False}]))
        with self.assertRaises(CleanupPending):
            backend.cleanup(Execution({},{"model":"synthetic"}),time.monotonic()+1)

    def test_ollama_client_cannot_choose_devices_paths_or_commands(self):
        target=Target("ollama","ollama",("gpu1",),"http://127.0.0.1:11434",models=("synthetic",))
        backend=OllamaBackend(target,ScriptedTransport([]))
        for options in ({"num_gpu":2},{"main_gpu":0},{"path":"/private"},{"command":"bad"}):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    backend.validate("generate",{"model":"synthetic","prompt":"neutral","options":options})
    def test_unexpected_gpu_memory_prevents_comfy_submission(self):
        target=self.target()
        transport=ScriptedTransport([{"queue_running":[],"queue_pending":[]}])
        backend=ComfyBackend(target,transport,probe=lambda:2000)
        with self.assertRaises(Uncertain):
            backend.execute("workflow",target.workflow,time.monotonic()+1,lambda *a:None)
        self.assertFalse(any(c[0]=="POST" for c in transport.calls))

class ConfigTests(unittest.TestCase):
    def test_config_rejects_dual_gpu_url_credentials_and_changed_template(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            template={"2":{"class_type":"SaveImage","inputs":{"filename_prefix":"synthetic"}}}
            raw=json.dumps(template).encode()
            (root/"workflow.json").write_bytes(raw)
            good={"state_dir":"state","lock_paths":{"gpu0":"g0.lock","gpu1":"g1.lock"},
              "targets":{"wallpaper":{"kind":"comfy","resources":["gpu0"],
                "base_url":"http://127.0.0.1:8190","workflow_template":"workflow.json",
                "workflow_sha256":hashlib.sha256(raw).hexdigest(),"output_node":"2",
                "idle_memory_mb":None}}}
            config=root/"config.json"
            config.write_text(json.dumps(good))
            value,targets,*_=load_config(config)
            self.assertIn("wallpaper",targets)
            for field,bad in (("resources",["gpu0","gpu1"]),("base_url","http://user:secret@localhost:8190"),
                              ("workflow_sha256","wrong")):
                with self.subTest(field=field):
                    changed=copy.deepcopy(good)
                    changed["targets"]["wallpaper"][field]=bad
                    config.write_text(json.dumps(changed))
                    with self.assertRaises(ValueError):
                        load_config(config)

if __name__=="__main__":
    unittest.main()
