"""Named backend adapters; transports and probes are injected for offline tests."""
from dataclasses import dataclass, field
import copy
import math
import re
import subprocess
import time
from urllib.parse import urlencode, urlsplit
import urllib.request
from .core import encode, strict_json

class Uncertain(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)

class CleanupPending(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)

@dataclass
class Execution:
    value: dict
    handle: dict
    artifact: bytes | None = None
    media_type: str | None = None
    error_code: str | None = None

PATH_INPUTS = {"image", "filename", "filename_prefix", "output_filename_prefix", "path",
               "ckpt_name", "unet_name", "vae_name", "clip_name", "lora_name"}
COMMAND_INPUTS = {"command", "shell", "script", "executable", "arguments"}

@dataclass
class Target:
    name: str
    kind: str
    resources: tuple
    base_url: str
    models: tuple = ()
    workflow: dict = field(default_factory=dict)
    mutations: dict = field(default_factory=dict)
    output_node: str = ""
    output_key: str = "images"
    media_type: str = "image/png"
    idle_memory_mb: float | None = None
    cleanup_samples: int = 3
    max_context: int = 98304
    acquisition_timeout: float = 3600
    execution_timeout: float = 1800
    cleanup_timeout: float = 60
    def __post_init__(self):
        if (not isinstance(self.name, str) or not self.name or
                self.kind not in ("ollama", "comfy") or
                any(not isinstance(model, str) or not model for model in self.models) or
                not isinstance(self.workflow, dict) or not isinstance(self.mutations, dict)):
            raise ValueError("invalid_target_contract")
        url = urlsplit(self.base_url)
        try:
            port = url.port
        except ValueError:
            raise ValueError("invalid_backend_port") from None
        if (url.scheme not in ("http", "https") or not url.hostname or url.username or
                url.password or url.query or url.fragment or url.path not in ("", "/") or
                (port is not None and not 1 <= port <= 65535)):
            raise ValueError("invalid_backend_base")
        if not self.resources or set(self.resources) - {"gpu0", "gpu1"}:
            raise ValueError("invalid_target_resources")
        for value in (self.acquisition_timeout, self.execution_timeout, self.cleanup_timeout):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("invalid_deadline")
        if self.idle_memory_mb is not None and (type(self.idle_memory_mb) not in (int, float)
                or not math.isfinite(self.idle_memory_mb) or self.idle_memory_mb < 0):
            raise ValueError("invalid_calibrated_memory")
        if not 2 <= self.cleanup_samples <= 20 or not 1 <= self.max_context <= 1048576:
            raise ValueError("invalid_target_limits")
        for key, spec in self.mutations.items():
            node, input_key = key.split(".", 1)
            if input_key in COMMAND_INPUTS:
                raise ValueError("mutable_commands_forbidden")
            if node not in self.workflow or input_key not in self.workflow[node].get("inputs", {}):
                raise ValueError("mutation_not_in_template")
            if input_key in PATH_INPUTS and spec.get("type") != "enum":
                raise ValueError("mutable_paths_require_exact_allowlist")

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Uncertain("backend_redirect_forbidden")

class HTTPTransport:
    """No environment proxies, redirects, caller URL or caller filesystem access."""
    def __init__(self, base_url, max_bytes=32 * 1024 * 1024):
        self.base, self.max_bytes = base_url.rstrip("/"), max_bytes
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    def _request(self, method, route, payload=None, params=None, timeout=5):
        if not route.startswith("/") or "://" in route:
            raise ValueError("invalid_backend_route")
        url = self.base + route + (("?" + urlencode(params)) if params else "")
        request = urllib.request.Request(url, data=encode(payload) if payload is not None else None,
                                         method=method, headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=timeout) as response:
                data = response.read(self.max_bytes + 1)
                if len(data) > self.max_bytes:
                    raise Uncertain("backend_response_too_large")
                return data, response.headers.get_content_type()
        except Uncertain:
            raise
        except Exception:
            raise Uncertain("backend_transport_or_response_failure") from None
    def json(self, method, route, payload=None, timeout=5):
        data, _ = self._request(method, route, payload, timeout=timeout)
        try:
            value = strict_json(data)
            if not isinstance(value, dict):
                raise ValueError("not_object")
            return value
        except (ValueError, UnicodeError):
            raise Uncertain("invalid_backend_json") from None
    def binary(self, route, params, timeout=5):
        return self._request("GET", route, params=params, timeout=timeout)

def remaining(deadline, error=Uncertain):
    value = deadline - time.monotonic()
    if value <= 0:
        raise error("backend_deadline")
    return value

OLLAMA_FIELDS = {
    "generate": {"model", "prompt", "system", "suffix", "images", "format", "raw", "think",
                 "stream", "keep_alive", "options", "logprobs", "top_logprobs"},
    "chat": {"model", "messages", "tools", "format", "think", "stream", "keep_alive", "options"},
}
OLLAMA_OPTIONS = {"num_ctx", "num_predict", "temperature", "seed", "top_k", "top_p",
                  "min_p", "tfs_z", "typical_p", "repeat_last_n", "repeat_penalty",
                  "presence_penalty", "frequency_penalty", "stop", "mirostat",
                  "mirostat_tau", "mirostat_eta"}

class OllamaBackend:
    def __init__(self, target, transport, poll_interval=.1):
        self.target, self.transport, self.poll_interval = target, transport, poll_interval
    def validate(self, operation, payload):
        if (operation not in OLLAMA_FIELDS or not isinstance(payload, dict) or
                set(payload) - OLLAMA_FIELDS[operation] or payload.get("model") not in self.target.models or
                payload.get("stream", False) is not False):
            raise ValueError("invalid_ollama_payload")
        if operation == "generate" and not isinstance(payload.get("prompt"), str):
            raise ValueError("prompt_required")
        if operation == "chat" and not isinstance(payload.get("messages"), list):
            raise ValueError("messages_required")
        options = payload.get("options", {})
        if not isinstance(options, dict) or set(options) - OLLAMA_OPTIONS:
            raise ValueError("invalid_options")
        if "num_ctx" in options and (type(options["num_ctx"]) is not int or
                                     not 1 <= options["num_ctx"] <= self.target.max_context):
            raise ValueError("invalid_context")
        encode(payload)
    def execute(self, operation, payload, deadline, progress):
        request = copy.deepcopy(payload)
        request["stream"] = False
        try:
            result = self.transport.json("POST", "/api/" + operation, request,
                                         timeout=remaining(deadline))
        except Exception:
            raise Uncertain("ollama_submission_or_response_uncertain") from None
        if result.get("done") is not True:
            raise Uncertain("ollama_completion_not_confirmed")
        progress("execution_complete", {"model": payload["model"]})
        return Execution(result, {"model": payload["model"]})
    def cleanup(self, execution, deadline):
        return self.cleanup_models((execution.handle["model"],), deadline)
    def cleanup_models(self, models, deadline):
        # A host stage may use distinct map/reduce models. Unload all owned
        # models before checking globally empty residency under that lease.
        try:
            for model in sorted(set(models)):
                response = self.transport.json("POST", "/api/generate",
                    {"model": model, "stream": False, "keep_alive": 0},
                    timeout=remaining(deadline, CleanupPending))
                if response.get("done") is not True:
                    raise CleanupPending("ollama_unload_not_confirmed")
            while True:
                result = self.transport.json("GET", "/api/ps",
                                              timeout=remaining(deadline, CleanupPending))
                if isinstance(result.get("models"), list) and not result["models"]:
                    return {"completion_verified": True, "cleanup_verified": True,
                            "policy": "known_request_completion_then_owned_unload_and_empty_residency"}
                time.sleep(min(self.poll_interval, remaining(deadline, CleanupPending)))
        except CleanupPending:
            raise
        except Exception:
            raise CleanupPending("ollama_cleanup_uncertain") from None

def relative(value, basename=False):
    if not isinstance(value, str) or "\\" in value or "\x00" in value or ":" in value:
        raise ValueError("unsafe_output_path")
    if value.startswith("/") or any(p in (".", "..") for p in value.split("/")):
        raise ValueError("unsafe_output_path")
    if basename and (not value or "/" in value):
        raise ValueError("unsafe_output_filename")
    return value

def validate_mutation(value, spec):
    kind = spec.get("type")
    if kind == "enum":
        if not any(encode(value) == encode(item) for item in spec.get("values", [])):
            raise ValueError("mutation_not_allowlisted")
    elif kind == "string":
        if not isinstance(value, str) or len(value) > spec.get("max_length", 32768):
            raise ValueError("invalid_string_mutation")
    elif kind in ("integer", "number"):
        types = (int,) if kind == "integer" else (int, float)
        if (type(value) not in types or not math.isfinite(value) or
                value < spec.get("minimum", -1e20) or value > spec.get("maximum", 1e20)):
            raise ValueError("invalid_numeric_mutation")
    elif kind == "boolean":
        if type(value) is not bool:
            raise ValueError("invalid_boolean_mutation")
    else:
        raise ValueError("unsupported_mutation_contract")

class ComfyBackend:
    def __init__(self, target, transport, probe=None, poll_interval=.1):
        self.target, self.transport, self.probe = target, transport, probe
        self.poll_interval = poll_interval
    def validate(self, operation, payload):
        if (operation != "workflow" or not isinstance(payload, dict) or
                set(payload) != set(self.target.workflow) or
                self.target.idle_memory_mb is None or self.probe is None):
            raise ValueError("workflow_contract_or_cleanup_calibration_required")
        for node_id, expected in self.target.workflow.items():
            node = payload[node_id]
            if not isinstance(node, dict) or set(node) != set(expected):
                raise ValueError("workflow_node_shape_changed")
            for key in expected:
                if key != "inputs" and encode(node[key]) != encode(expected[key]):
                    raise ValueError("workflow_node_type_changed")
            inputs = node.get("inputs")
            if not isinstance(inputs, dict) or set(inputs) != set(expected.get("inputs", {})):
                raise ValueError("workflow_inputs_changed")
            for key, value in inputs.items():
                contract = self.target.mutations.get(node_id + "." + key)
                if contract:
                    validate_mutation(value, contract)
                elif encode(value) != encode(expected["inputs"][key]):
                    raise ValueError("fixed_workflow_input_changed")
        encode(payload)
    def _queue_empty(self, deadline):
        queue = self.transport.json("GET", "/queue", timeout=remaining(deadline))
        if not isinstance(queue.get("queue_running"), list) or not isinstance(queue.get("queue_pending"), list):
            raise Uncertain("invalid_queue_state")
        return not queue["queue_running"] and not queue["queue_pending"]
    def execute(self, operation, payload, deadline, progress):
        if not self._queue_empty(deadline):
            raise Uncertain("unexpected_existing_comfy_work")
        if self.probe is None or self.target.idle_memory_mb is None:
            raise Uncertain("cleanup_calibration_missing_before_submission")
        observed = self.probe()
        if (type(observed) not in (int, float) or not math.isfinite(observed) or observed < 0
                or observed > self.target.idle_memory_mb):
            raise Uncertain("unexpected_gpu_residency_before_comfy_submission")
        try:
            response = self.transport.json("POST", "/prompt",
                {"prompt": payload}, timeout=remaining(deadline))
        except Exception:
            raise Uncertain("comfy_submission_uncertain") from None
        prompt_id = response.get("prompt_id")
        if not isinstance(prompt_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", prompt_id):
            raise Uncertain("comfy_prompt_id_missing_or_invalid")
        progress("running", {"prompt_id": prompt_id, "target": self.target.name})
        while True:
            try:
                history = self.transport.json("GET", "/history/" + prompt_id,
                                                timeout=remaining(deadline))
            except Exception:
                raise Uncertain("comfy_history_uncertain") from None
            item = history.get(prompt_id, {})
            status = item.get("status", {})
            # Terminal history errors can have completed=False (success flag).
            # Queue/memory cleanup still gates release; outputs alone never do.
            if status.get("status_str") == "error" and type(status.get("completed")) is bool:
                return Execution({}, {"prompt_id": prompt_id}, error_code="comfy_execution_failed")
            if status.get("completed") is True:
                if status.get("status_str") != "success":
                    raise Uncertain("comfy_terminal_status_unknown")
                break
            time.sleep(min(self.poll_interval, remaining(deadline)))
        try:
            outputs = item.get("outputs", {}).get(self.target.output_node, {}).get(self.target.output_key)
            if not isinstance(outputs, list) or not outputs:
                raise ValueError("intended_output_missing")
            descriptor = outputs[0]
            filename = relative(descriptor["filename"], basename=True)
            subfolder = relative(descriptor.get("subfolder", ""))
            if descriptor.get("type") != "output":
                raise ValueError("non_output_artifact")
            data, media_type = self.transport.binary("/view",
                {"filename": filename, "subfolder": subfolder, "type": "output"},
                timeout=remaining(deadline))
            if self.target.media_type == "image/png":
                valid = filename.lower().endswith(".png") and data.startswith(b"\x89PNG\r\n\x1a\n")
            elif self.target.media_type == "audio/mpeg":
                valid = filename.lower().endswith(".mp3") and (data.startswith(b"ID3") or
                    (len(data) >= 2 and data[0] == 0xff and data[1] & 0xe0 == 0xe0))
            else:
                valid = False
            if not valid or media_type != self.target.media_type:
                raise ValueError("unexpected_artifact_format")
            return Execution({"filename": filename, "subfolder": subfolder, "prompt_id": prompt_id},
                             {"prompt_id": prompt_id}, artifact=data, media_type=media_type)
        except Exception:
            return Execution({}, {"prompt_id": prompt_id}, error_code="artifact_retrieval_or_validation_failed")
    def cleanup(self, execution, deadline):
        if self.target.idle_memory_mb is None or self.probe is None:
            raise CleanupPending("cleanup_observer_not_calibrated")
        try:
            if not self._queue_empty(deadline):
                raise CleanupPending("unexpected_work_before_cleanup")
            self.transport.json("POST", "/free", {"unload_models": True, "free_memory": True},
                                timeout=remaining(deadline, CleanupPending))
            stable = 0
            while True:
                remaining(deadline, CleanupPending)
                idle, used = self._queue_empty(deadline), self.probe()
                if type(used) not in (int, float) or not math.isfinite(used) or used < 0:
                    raise CleanupPending("invalid_gpu_cleanup_observation")
                stable = stable + 1 if idle and used <= self.target.idle_memory_mb else 0
                if stable >= self.target.cleanup_samples:
                    return {"completion_verified": True, "cleanup_verified": True,
                            "policy": "terminal_prompt_owned_free_empty_queue_calibrated_gpu_memory",
                            "prompt_id": execution.handle["prompt_id"], "stable_samples": stable,
                            "memory_mb": used, "idle_ceiling_mb": self.target.idle_memory_mb}
                time.sleep(min(self.poll_interval, remaining(deadline, CleanupPending)))
        except CleanupPending:
            raise
        except Exception:
            raise CleanupPending("comfy_cleanup_uncertain") from None

class NvidiaMemoryProbe:
    def __init__(self, gpu_index):
        if type(gpu_index) is not int or gpu_index not in (0, 1):
            raise ValueError("invalid_physical_gpu_index")
        self.gpu_index = gpu_index
    def __call__(self):
        output = subprocess.check_output(
            ["nvidia-smi", "--id=" + str(self.gpu_index), "--query-gpu=memory.used",
             "--format=csv,noheader,nounits"], text=True, timeout=5)
        return float(output.strip())
