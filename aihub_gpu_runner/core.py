"""Durable jobs and one shared admission/recovery boundary; no GPU business logic."""
import concurrent.futures
import contextlib
from datetime import datetime, timezone
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid

JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
FINAL = {"success", "failed", "cancelled"}
QUIESCENT = FINAL | {"reconciling", "cleanup_pending"}
_THREAD_LOCKS = {}
_THREAD_GUARD = threading.Lock()


class Conflict(Exception):
    pass


class RecoveryRequired(Exception):
    pass


def check_id(value):
    if not isinstance(value, str) or not JOB_ID.fullmatch(value):
        raise ValueError("invalid_request_id")
    return value


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("nonfinite_json")
    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


def encode(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class NativeLock:
    """Persistent file identity; Linux flock, Windows byte lock for offline tests."""
    def __init__(self, path):
        self.path = Path(path)
        self.fd = None
        with _THREAD_GUARD:
            self.local = _THREAD_LOCKS.setdefault(str(self.path.resolve()), threading.Lock())
    def acquire(self, timeout=0):
        if self.fd is not None:
            raise RuntimeError("lock_already_owned")
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("invalid_lock_timeout")
        deadline = time.monotonic() + timeout
        if not self.local.acquire(timeout=timeout):
            return False
        fd = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o600)
            os.set_inheritable(fd, False)
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.fd = fd
                    return True
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    if time.monotonic() >= deadline:
                        os.close(fd)
                        self.local.release()
                        return False
                    time.sleep(min(.01, max(0, deadline - time.monotonic())))
        except BaseException:
            if fd is not None:
                os.close(fd)
            self.local.release()
            raise
    def close(self):
        if self.fd is None:
            return
        fd, self.fd = self.fd, None
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
            self.local.release()
    @contextlib.contextmanager
    def held(self, timeout=5):
        # A context object is shared by worker threads; descriptors are not.
        lease = NativeLock(self.path)
        if not lease.acquire(timeout):
            raise TimeoutError("local_lock_timeout")
        try:
            yield lease
        finally:
            lease.close()


class AtomicStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.root / "jobs").mkdir(exist_ok=True, mode=0o700)
        (self.root / "results").mkdir(exist_ok=True, mode=0o700)
    def path(self, name):
        result = (self.root / name).resolve()
        if not result.is_relative_to(self.root):
            raise ValueError("state_path_escape")
        return result
    def write_bytes(self, name, data):
        destination = self.path(name)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = destination.with_name("." + destination.name + "." + uuid.uuid4().hex + ".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        if os.name != "nt":
            fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    def write(self, name, value):
        self.write_bytes(name, encode(value))
    def read(self, name, default=None):
        try:
            return strict_json(self.path(name).read_bytes())
        except FileNotFoundError:
            return default
        except (ValueError, UnicodeError):
            raise RecoveryRequired("stored_record_invalid") from None
    def job_name(self, job_id):
        return "jobs/" + check_id(job_id) + ".json"
    def job(self, job_id):
        value = self.read(self.job_name(job_id))
        if value is None:
            raise KeyError(job_id)
        return value
    def put_job(self, value):
        self.write(self.job_name(value["request_id"]), value)
    def jobs(self):
        return [strict_json(path.read_bytes()) for path in sorted(self.path("jobs").glob("*.json"))]


def lock_identity(stat_value):
    import stat
    return {"device": stat_value.st_dev, "inode": stat_value.st_ino,
            "mode": stat.S_IMODE(stat_value.st_mode),
            "uid": stat_value.st_uid, "gid": stat_value.st_gid}


class Admission:
    """Every participating launcher must use this gate, never the OS lock alone."""
    def __init__(self, store, lock_paths, scope, require_existing_locks=False):
        self.store = store
        if not lock_paths or set(lock_paths) - {"gpu0", "gpu1"}:
            raise ValueError("invalid_resources")
        self.lock_paths = {key: Path(value).resolve() for key, value in lock_paths.items()}
        if len(set(self.lock_paths.values())) != len(self.lock_paths):
            raise ValueError("resource_paths_must_be_distinct")
        self.scope = scope
        self.require_existing_locks = require_existing_locks
        if require_existing_locks and any(not p.is_file() for p in self.lock_paths.values()):
            raise RecoveryRequired("existing_physical_lock_missing")
        self.mutex = NativeLock(store.path("admission.lock"))
        with self.mutex.held():
            current = store.read("ownership.json")
            if current is None:
                if store.jobs():
                    raise RecoveryRequired("ownership_missing_with_existing_jobs")
                store.write("ownership.json", {"schema": 1, "scope": scope, "ready": False,
                    "bootstrap_id": uuid.uuid4().hex, "owners": {}})
            else:
                self._validate(current)
    def _validate(self, state):
        if (not isinstance(state, dict) or state.get("schema") != 1 or
                state.get("scope") != self.scope or type(state.get("ready")) is not bool or
                not isinstance(state.get("owners"), dict) or
                set(state["owners"]) - set(self.lock_paths)):
            raise RecoveryRequired("ownership_invalid_or_scope_changed")
        if state["ready"] and (not isinstance(state.get("lock_identity"), dict) or
                               set(state["lock_identity"]) != set(self.lock_paths)):
            raise RecoveryRequired("physical_lock_identity_missing")
        for resource, owner in state["owners"].items():
            if (not isinstance(owner, dict) or
                    not all(isinstance(owner.get(k), str) and owner[k] for k in
                            ("job_id", "request_hash", "lease_id", "target", "phase"))):
                raise RecoveryRequired("ownership_record_invalid")
        return state
    def snapshot(self):
        return self._validate(self.store.read("ownership.json"))
    def bootstrap(self, evidence):
        with self.mutex.held():
            state = self.snapshot()
            if (not evidence.get("approval_ref") or
                    evidence.get("bootstrap_id") != state["bootstrap_id"] or
                    evidence.get("backend_quiescent") is not True or
                    evidence.get("cleanup_verified") is not True or state["owners"]):
                raise ValueError("invalid_local_bootstrap_evidence")
            if state["ready"]:
                raise Conflict("already_bootstrapped")
            locks = []
            try:
                for resource, path in sorted(self.lock_paths.items()):
                    if self.require_existing_locks and not path.is_file():
                        raise RecoveryRequired("existing_physical_lock_missing")
                    lock = NativeLock(path)
                    if not lock.acquire(0):
                        raise RecoveryRequired("bootstrap_resource_busy")
                    locks.append(lock)
                state["lock_identity"] = {r: lock_identity(p.stat()) for r, p in self.lock_paths.items()}
                state["ready"] = True
                state["bootstrap_evidence"] = dict(evidence, provenance="local_operator_attestation")
                self.store.write("ownership.json", state)
            finally:
                for lock in reversed(locks):
                    lock.close()
    def try_acquire(self, job_id, request_hash, resources, target):
        check_id(job_id)
        resources = tuple(sorted(set(resources)))
        if not resources or set(resources) - set(self.lock_paths):
            raise ValueError("unknown_resource")
        locks = []
        with self.mutex.held():
            state = self.snapshot()
            if not state["ready"] or any(r in state["owners"] for r in resources):
                return None
            try:
                for resource in resources:
                    path = self.lock_paths[resource]
                    if (not path.is_file() or lock_identity(path.stat()) !=
                            state["lock_identity"][resource]):
                        raise RecoveryRequired("physical_lock_identity_changed")
                    lock = NativeLock(self.lock_paths[resource])
                    if not lock.acquire(0):
                        for previous in reversed(locks):
                            previous.close()
                        return None
                    locks.append(lock)
                    if lock_identity(os.fstat(lock.fd)) != state["lock_identity"][resource]:
                        raise RecoveryRequired("physical_lock_identity_changed_during_acquisition")
                owner = {"job_id": job_id, "request_hash": request_hash,
                         "lease_id": uuid.uuid4().hex, "target": target, "phase": "claimed",
                         "claimed_at": timestamp()}
                for resource in resources:
                    state["owners"][resource] = dict(owner)
                # Durable ownership precedes submission, even when the response is lost.
                self.store.write("ownership.json", state)
                return Lease(self, owner, resources, locks)
            except BaseException:
                for lock in reversed(locks):
                    lock.close()
                raise
    def _modify(self, owner, resources, phase=None, clear=False, detail=None):
        with self.mutex.held():
            state = self.snapshot()
            if any(state["owners"].get(r, {}).get("lease_id") != owner["lease_id"]
                   for r in resources):
                raise RecoveryRequired("stale_lease")
            for resource in resources:
                if clear:
                    del state["owners"][resource]
                else:
                    state["owners"][resource]["phase"] = phase
                    if detail:
                        state["owners"][resource]["detail"] = detail
            self.store.write("ownership.json", state)
    def recover(self, job_id, evidence):
        # Local administrative path only, never exposed by the request API.
        with self.mutex.held():
            state = self.snapshot()
            matches = {r: o for r, o in state["owners"].items() if o["job_id"] == job_id}
            if not matches:
                raise ValueError("no_matching_owner")
            owner = next(iter(matches.values()))
            if (not evidence.get("approval_ref") or
                    evidence.get("operator_approved_backend_recovery") is not True or
                    evidence.get("backend_quiescent") is not True or
                    evidence.get("cleanup_verified") is not True or
                    any(evidence.get(k) != owner[k] for k in
                        ("job_id", "request_hash", "lease_id", "target")) or
                    any(o["lease_id"] != owner["lease_id"] for o in matches.values())):
                raise ValueError("invalid_or_stale_recovery_evidence")
            locks = []
            try:
                for resource in sorted(matches):
                    path = self.lock_paths[resource]
                    if (not path.is_file() or lock_identity(path.stat()) !=
                            state["lock_identity"][resource]):
                        raise RecoveryRequired("physical_lock_identity_changed")
                    lock = NativeLock(self.lock_paths[resource])
                    if not lock.acquire(0):
                        raise RecoveryRequired("owner_still_active")
                    locks.append(lock)
                state.setdefault("recoveries", []).append(dict(evidence, recovered_at=timestamp()))
                for resource in matches:
                    del state["owners"][resource]
                self.store.write("ownership.json", state)
            finally:
                for lock in reversed(locks):
                    lock.close()


class Lease:
    def __init__(self, admission, owner, resources, locks):
        self.admission, self.owner = admission, owner
        self.resources, self.locks = resources, locks
    def mark(self, phase, detail=None):
        self.admission._modify(self.owner, self.resources, phase=phase, detail=detail)
    def release(self, proof):
        if (proof.get("completion_verified") is not True or
                proof.get("cleanup_verified") is not True):
            raise ValueError("unverified_release")
        self.admission._modify(self.owner, self.resources, clear=True)
    def close(self):
        for lock in reversed(self.locks):
            lock.close()
        self.locks = []


class Runner:
    def __init__(self, admission, targets, backends, workers=4, poll_interval=.05,
                 max_pending=64):
        self.admission, self.store = admission, admission.store
        self.targets, self.backends = targets, backends
        if set(targets) != set(backends):
            raise ValueError("backend_configuration_mismatch")
        self.poll_interval, self.max_pending = poll_interval, max_pending
        self.job_mutex = NativeLock(self.store.path("jobs.lock"))
        self.service_lock = NativeLock(self.store.path("supervisor.lock"))
        if not self.service_lock.acquire(0):
            raise Conflict("supervisor_already_running")
        self.closed, self.futures, self.pending = False, {}, {}
        self.wake = threading.Event()
        try:
            with self.job_mutex.held():
                owners = self.admission.snapshot()["owners"]
                owner_ids = {o["job_id"] for o in owners.values()}
                for job in self.store.jobs():
                    # Local host stages supervise themselves with the same
                    # Admission journal; API restart cannot rewrite an active stage.
                    if job.get("host_stage") is True:
                        continue
                    if job["state"] not in FINAL:
                        job["state"] = "reconciling" if job["request_id"] in owner_ids else "failed"
                        job["error_code"] = ("supervisor_restart_unresolved_owner" if
                                             job["state"] == "reconciling" else "restart_before_admission")
                        job["updated_at"] = timestamp()
                        self.store.put_job(job)
            self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers,
                                                              thread_name_prefix="gpu-runner")
            self.scheduler = threading.Thread(target=self._schedule, name="gpu-admission", daemon=True)
            self.scheduler.start()
        except BaseException:
            self.service_lock.close()
            raise
    def _update(self, job_id, **fields):
        with self.job_mutex.held():
            job = self.store.job(job_id)
            job.update(fields, updated_at=timestamp())
            self.store.put_job(job)
            return job
    def submit(self, request_id, target, operation, payload):
        check_id(request_id)
        if (self.closed or not isinstance(target, str) or not isinstance(operation, str)
                or target not in self.targets or not isinstance(payload, dict)):
            raise ValueError("invalid_target_or_payload")
        self.backends[target].validate(operation, payload)
        request_hash = hashlib.sha256(encode({"target": target, "operation": operation,
                                              "payload": payload})).hexdigest()
        with self.job_mutex.held():
            existing = self.store.read(self.store.job_name(request_id))
            if existing:
                if existing["request_hash"] != request_hash:
                    raise Conflict("request_id_payload_conflict")
                return existing
            if sum(not f.done() for f in self.futures.values()) >= self.max_pending:
                raise Conflict("pending_limit")
            job = {"request_id": request_id, "request_hash": request_hash, "target": target,
                   "operation": operation, "payload": json.loads(encode(payload)), "state": "waiting",
                   "created_at": timestamp(), "updated_at": timestamp(), "error_code": None,
                   "resources": list(self.targets[target].resources)}
            self.store.put_job(job)
            self.futures[request_id] = concurrent.futures.Future()
            self.pending[request_id] = time.monotonic() + self.targets[target].acquisition_timeout
            self.wake.set()
            return job
    def future(self, job_id):
        return self.futures[job_id]
    def status(self, job_id):
        return self.store.job(check_id(job_id))
    def wait(self, job_id, timeout):
        deadline = time.monotonic() + timeout
        while True:
            job = self.status(job_id)
            future = self.futures.get(job_id)
            if job["state"] in FINAL or (job["state"] in ("reconciling", "cleanup_pending")
                                        and (future is None or future.done())):
                return job
            if time.monotonic() >= deadline:
                raise TimeoutError("client_wait_timeout")
            time.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))
    def cancel(self, job_id):
        with self.job_mutex.held():
            job = self.store.job(check_id(job_id))
            if job["state"] != "waiting":
                return False
            job.update(state="cancelled", error_code="cancelled_before_submission", updated_at=timestamp())
            self.store.put_job(job)
            return True
    def _finish_pending(self, job_id, state=None, error_code=None):
        with self.job_mutex.held():
            job = self.store.job(job_id)
            if state and job["state"] == "waiting":
                job.update(state=state, error_code=error_code, updated_at=timestamp())
                self.store.put_job(job)
            self.pending.pop(job_id, None)
            future = self.futures[job_id]
        if not future.done():
            future.set_result(None)
    def _schedule(self):
        while True:
            with self.job_mutex.held():
                pending = tuple(self.pending.items())
            for job_id, deadline in pending:
                try:
                    job = self.status(job_id)
                    if job["state"] == "cancelled":
                        self._finish_pending(job_id)
                        continue
                    if self.closed:
                        self._finish_pending(job_id, "cancelled", "supervisor_closing")
                        continue
                    if time.monotonic() >= deadline:
                        self._finish_pending(job_id, "failed", "resource_acquisition_timeout")
                        continue
                    target = self.targets[job["target"]]
                    lease = self.admission.try_acquire(job_id, job["request_hash"],
                                                       target.resources, target.name)
                    if lease is None:
                        continue
                    with self.job_mutex.held():
                        self.pending.pop(job_id, None)
                    self.pool.submit(self._run_owned, job_id, lease, self.futures[job_id])
                except Exception as exc:
                    try:
                        self._finish_pending(job_id, "reconciling", "admission_or_storage_failure")
                    except Exception:
                        future = self.futures[job_id]
                        if not future.done():
                            future.set_exception(exc)
                        self.pending.pop(job_id, None)
            if self.closed:
                return
            self.wake.wait(self.poll_interval)
            self.wake.clear()
    def _run_owned(self, job_id, lease, future):
        try:
            if not future.set_running_or_notify_cancel():
                lease.release({"completion_verified": True, "cleanup_verified": True,
                               "policy": "never_submitted"})
                return
            self._work(job_id, lease)
        except BaseException as exc:
            future.set_exception(exc)
        else:
            future.set_result(None)
        finally:
            lease.close()
    def _work(self, job_id, lease):
        from .backends import CleanupPending, Uncertain
        try:
            job = self.status(job_id)
            target, backend = self.targets[job["target"]], self.backends[job["target"]]
            with self.job_mutex.held():
                current = self.store.job(job_id)
                if current["state"] == "cancelled":
                    lease.release({"completion_verified": True, "cleanup_verified": True,
                                   "policy": "never_submitted"})
                    return
                current.update(state="running", lease_id=lease.owner["lease_id"], updated_at=timestamp())
                self.store.put_job(current)
            lease.mark("submit_intent")
            def progress(phase, detail=None):
                lease.mark(phase, detail)
                self._update(job_id, backend_detail=detail or {}, phase=phase)
            execution = backend.execute(job["operation"], job["payload"],
                                        time.monotonic() + target.execution_timeout, progress)
            lease.mark("cleanup_pending", execution.handle)
            self._update(job_id, state="cleanup_pending", backend_detail=execution.handle,
                         completion_verified=True)
            proof = backend.cleanup(execution, time.monotonic() + target.cleanup_timeout)
            if proof.get("completion_verified") is not True or proof.get("cleanup_verified") is not True:
                raise CleanupPending("cleanup_not_verified")
            result = {"value": execution.value, "media_type": execution.media_type,
                      "artifact_available": execution.artifact is not None}
            if execution.artifact is not None:
                self.store.write_bytes("results/" + job_id + ".bin", execution.artifact)
            self.store.write("results/" + job_id + ".json", result)
            # Result publication and positive cleanup proof precede resource handoff.
            self._update(job_id, state=("failed" if execution.error_code else "success"),
                         error_code=execution.error_code, cleanup_verified=True, cleanup_proof=proof)
            lease.release(proof)
        except CleanupPending as exc:
            if lease:
                lease.mark("cleanup_pending")
            self._update(job_id, state="cleanup_pending", error_code=exc.code)
        except Uncertain as exc:
            if lease:
                lease.mark("reconciling")
            self._update(job_id, state="reconciling", error_code=exc.code)
        except Exception:
            # Completed output must remain retrievable when only the ownership
            # clearing write reports failure after effect. If the old owner
            # remains, retain cleanup_pending instead of pretending handoff.
            current = self.store.job(job_id)
            if current["state"] in FINAL and current.get("cleanup_verified") is True:
                owners = self.admission.snapshot()["owners"]
                still_owned = lease and any(owners.get(r, {}).get("lease_id") ==
                                             lease.owner["lease_id"] for r in lease.resources)
                if still_owned:
                    self._update(job_id, state="cleanup_pending",
                                 error_code="ownership_release_unconfirmed")
                else:
                    self._update(job_id, publication_warning="ownership_release_sync_unconfirmed")
                return
            if lease:
                try:
                    lease.mark("reconciling")
                except Exception:
                    pass
            self._update(job_id, state="reconciling", error_code="supervision_or_storage_failure")
        finally:
            if lease:
                lease.close()
    def result(self, job_id):
        job = self.status(job_id)
        if job["state"] not in FINAL:
            raise Conflict("result_not_terminal")
        value = self.store.read("results/" + check_id(job_id) + ".json", {"value": None})
        return dict(value, state=job["state"], error_code=job["error_code"],
                    publication_warning=job.get("publication_warning"))
    def artifact(self, job_id):
        result = self.result(job_id)
        if not result.get("artifact_available"):
            raise KeyError("no_artifact")
        return self.store.path("results/" + check_id(job_id) + ".bin").read_bytes(), result["media_type"]
    def close(self):
        if self.closed:
            return
        self.closed = True
        self.wake.set()
        self.scheduler.join()
        self.pool.shutdown(wait=True, cancel_futures=False)
        self.service_lock.close()
