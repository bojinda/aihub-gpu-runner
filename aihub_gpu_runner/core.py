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
_HELD_NATIVE = {}


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


def guest_boot_id():
    """Only the kernel's boot UUID is reboot evidence; never synthesize a fallback."""
    try:
        value = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        return str(uuid.UUID(value)) if str(uuid.UUID(value)) == value else None
    except (OSError, ValueError):
        return None


class NativeLock:
    """Persistent file identity; Linux flock, Windows byte lock for offline tests."""
    def __init__(self, path, *, create=True):
        self.path = Path(path)
        self.create = create
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
            if self.create:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
            if not self.create:
                flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            fd = os.open(self.path, flags | (os.O_CREAT if self.create else 0), 0o600)
            if not self.create:
                import stat
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise RecoveryRequired("physical_lock_not_regular_file")
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
                    with _THREAD_GUARD:
                        _HELD_NATIVE[id(self)] = (os.getpid(), threading.get_ident(), str(self.path.resolve()))
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
    def is_held(self, expected_identity):
        """Probe OS contention on an identity-checked FD, without the local cache."""
        import stat
        flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(self.path, flags)  # Existing physical file only; never recreate.
        acquired = False
        try:
            value = os.fstat(fd)
            if not stat.S_ISREG(value.st_mode) or lock_identity(value) != expected_identity:
                raise RecoveryRequired("physical_lock_identity_changed_during_acquisition")
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if lock_identity(os.fstat(fd)) != expected_identity:
                    raise RecoveryRequired("physical_lock_identity_changed_during_acquisition")
                return True
            return False
        finally:
            try:
                if acquired:
                    if os.name == "nt":
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
    def close(self):
        if self.fd is None:
            return
        fd, self.fd = self.fd, None
        with _THREAD_GUARD:
            _HELD_NATIVE.pop(id(self), None)
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
    @staticmethod
    def held_by_current_thread(path):
        key = (os.getpid(), threading.get_ident(), str(Path(path).resolve()))
        with _THREAD_GUARD:
            return key in _HELD_NATIVE.values()
    @contextlib.contextmanager
    def held(self, timeout=5):
        # A context object is shared by worker threads; descriptors are not.
        lease = NativeLock(self.path, create=self.create)
        if not lease.acquire(timeout):
            raise TimeoutError("local_lock_timeout")
        try:
            yield lease
        finally:
            lease.close()


class AtomicStore:
    def __init__(self, root):
        self._write_context = threading.local()
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.root / "jobs").mkdir(exist_ok=True, mode=0o700)
        (self.root / "results").mkdir(exist_ok=True, mode=0o700)
    def path(self, name):
        result = (self.root / name).resolve()
        if not result.is_relative_to(self.root):
            raise ValueError("state_path_escape")
        return result
    def _read_raw(self, name, default=None):
        try:
            return strict_json(self.path(name).read_bytes())
        except FileNotFoundError:
            return default
        except (ValueError, UnicodeError):
            raise RecoveryRequired('stored_record_invalid') from None
    @contextlib.contextmanager
    def authority_mutex(self):
        path = self.path('admission.lock')
        if NativeLock.held_by_current_thread(path):
            yield
        else:
            with NativeLock(path).held():
                yield
    def _revocation(self, name):
        parts = Path(name).parts
        if len(parts) != 2 or parts[0] not in ('jobs', 'results'):
            return None
        job_id = Path(parts[1]).stem
        state = self._read_raw('ownership.json', {})
        return state.get('estop_revocations', {}).get(job_id)
    def write_bytes(self, name, data):
        name = self.path(name).relative_to(self.root).as_posix()
        if name == 'ownership.json':
            return self._write_authority(strict_json(data))
        state = self._read_raw('ownership.json', {})
        if state.get('schema') == 2 and (str(name).startswith(('jobs/', 'results/')) or name == 'recovery-control.json'):
            with self.authority_mutex():
                state = self._read_raw('ownership.json')
                if self._revocation(name):
                    raise RecoveryRequired('job_authority_revoked')
                if str(name).startswith('jobs/'):
                    value = strict_json(data)
                    g = state['admission_generation']['id']
                    if value.get('admission_generation', g) != g:
                        raise RecoveryRequired('stale_job_generation')
                    existing = self._read_raw(name, {})
                    if existing.get('admission_generation') and value.get('admission_generation') != g:
                        raise RecoveryRequired('stale_job_generation')
                    if not existing:value['admission_generation'] = g
                    data = encode(value)
                if name == 'recovery-control.json':
                    value = strict_json(data)
                    if value.get('admission_generation') != state['admission_generation']['id']:
                        raise RecoveryRequired('stale_maintenance_generation')
                return self._write_bytes(name, data)
        return self._write_bytes(name, data)
    def _write_authority(self, value):
        if not isinstance(value, dict):
            raise ValueError('ownership_object_required')
        with self.authority_mutex():
            current = self._read_raw('ownership.json')
            if current and current.get('schema') == 2:
                if value.get('schema') != 2 or value.get('authority_revision') != current['authority_revision']:
                    raise RecoveryRequired('stale_authority_revision')
                switching = getattr(self._write_context, 'generation_switch', False)
                if not switching and value.get('admission_generation') != current['admission_generation']:
                    raise RecoveryRequired('stale_authority_generation')
                if switching and (value['admission_generation']['origin'] != current['admission_generation']['origin'] or
                        value['admission_generation']['sequence'] != current['admission_generation']['sequence']+1):
                    raise RecoveryRequired('generation_transition_changed')
                for owner in value.get('owners', {}).values():
                    if (owner.get('admission_generation') != value['admission_generation']['id'] or
                            owner.get('job_id') in current.get('estop_revocations', {})):
                        raise RecoveryRequired('revoked_owner_cannot_be_restored')
                for name in ('estop_revocations', 'estop_attempts', 'estop_intents'):
                    if any(value.get(name, {}).get(k) != v for k, v in current.get(name, {}).items()):
                        raise RecoveryRequired('estop_history_changed')
                value = dict(value, authority_revision=current['authority_revision'] + 1)
            self._write_bytes('ownership.json', encode(value))
            return value
    def _write_bytes(self, name, data):
        guard = getattr(self._write_context, 'guard', None)
        if guard:
            guard()
        destination = self.path(name)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = destination.with_name("." + destination.name + "." + uuid.uuid4().hex + ".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            if guard:
                guard()
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        if os.name != "nt":
            fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    def write(self, name, value):
        name = self.path(name).relative_to(self.root).as_posix()
        if name == 'ownership.json':
            committed = self._write_authority(value)
            value.update(committed)
            return
        self.write_bytes(name, encode(value))
    def read(self, name, default=None):
        name = self.path(name).relative_to(self.root).as_posix()
        value = self._read_raw(name, default)
        record = self._revocation(name)
        if record:
            if str(name).startswith('jobs/'):
                return dict(record['job_before'], state='failed', host_exit_code=70,
                    error_code='administrator_estop_no_replay', authority_revoked=True,
                    resources_released=True, estop_operation=record['operation'], replayed=False,old_work_may_continue=True)
            return dict(value=None, artifact_available=False, authority_revoked=True,
                        error_code='administrator_estop_no_replay')
        return value
    def job_name(self, job_id):
        return "jobs/" + check_id(job_id) + ".json"
    def job(self, job_id):
        value = self.read(self.job_name(job_id))
        if value is None:
            raise KeyError(job_id)
        return value
    def put_job(self, value):
        state = self._read_raw('ownership.json', {})
        if state.get('schema') == 2 and not self.path(self.job_name(value['request_id'])).exists():
            value.setdefault('admission_generation', state['admission_generation']['id'])
        self.write(self.job_name(value["request_id"]), value)
    def jobs(self):
        return [self.job(path.stem) for path in sorted(self.path("jobs").glob("*.json"))]


def lock_identity(stat_value):
    import stat
    return {"device": stat_value.st_dev, "inode": stat_value.st_ino,
            "mode": stat.S_IMODE(stat_value.st_mode),
            "uid": stat_value.st_uid, "gid": stat_value.st_gid}


def generation_paths(state, root, original_paths=None):
    """One journal, derived paths, immutable original lock trust; no side registry."""
    g = state.get('admission_generation')
    if state.get('schema') != 2 or not isinstance(g, dict) or not re.fullmatch('[a-f0-9]{32}', str(g.get('id', ''))):
        raise RecoveryRequired('invalid_admission_generation')
    if type(g.get('sequence')) is not int or g['sequence'] < 0:
        raise RecoveryRequired('invalid_generation_sequence')
    origin = g['origin']
    if original_paths is not None and origin['paths'] != {r:str(Path(p).resolve()) for r,p in original_paths.items()}:
        raise RecoveryRequired('original_lock_configuration_changed')
    paths = ({r:Path(p) for r,p in origin['paths'].items()} if g['sequence']==0 else
             {r:Path(root)/'generations'/g['id']/(r+'.lock') for r in ('gpu0','gpu1')})
    if (set(paths) != {'gpu0','gpu1'} or g.get('paths') != {r:str(p.resolve()) for r,p in paths.items()}
            or g.get('lock_identity') != state.get('lock_identity')):
        raise RecoveryRequired('generation_lock_binding_changed')
    for r,p in origin['paths'].items():
        if lock_identity(Path(p).lstat()) != origin['lock_identity'][r]:
            raise RecoveryRequired('original_physical_lock_changed')
    return paths


class Admission:
    """Every participating launcher must use this gate, never the OS lock alone."""
    def __init__(self, store, lock_paths, scope, require_existing_locks=False,
                 boot_reader=guest_boot_id, control_required=False):
        self.store = store
        self.control_required = control_required
        self.boot_reader = boot_reader
        if not lock_paths or set(lock_paths) - {"gpu0", "gpu1"}:
            raise ValueError("invalid_resources")
        self.lock_paths = {key: Path(value).resolve() for key, value in lock_paths.items()}
        self.original_lock_paths = dict(self.lock_paths)
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
                if store.jobs() or (store.root/'generations').exists():
                    raise RecoveryRequired("ownership_missing_with_existing_jobs")
                store.write("ownership.json", {"schema": 1, "scope": scope, "ready": False,
                    "bootstrap_id": uuid.uuid4().hex, "owners": {}})
            else:
                self._validate(current)
                if require_existing_locks and current["ready"]:
                    self._check_physical_identities(current)
    def _check_physical_identities(self, state):
        for resource, path in self.lock_paths.items():
            if (not path.is_file() or lock_identity(path.stat()) != state["lock_identity"][resource]):
                raise RecoveryRequired("physical_lock_identity_changed")
    def _validate(self, state):
        if isinstance(state, dict) and state.get('schema') == 2:
            self.lock_paths = generation_paths(state, self.store.root, self.original_lock_paths)
            if type(state.get('authority_revision')) is not int or state['authority_revision']<0 or any(
                    not isinstance(state.get(k),dict) for k in ('estop_intents','estop_attempts','estop_revocations')):
                raise RecoveryRequired('generation_journal_invalid')
        if (not isinstance(state, dict) or state.get("schema") not in (1, 2) or
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
    def retire_prepared_wallpaper(self, job_id):
        from .native_settlement import retire_prepared
        return retire_prepared(self, job_id)
    def finalize_native_settlements(self):
        from .native_settlement import finalize_all
        return finalize_all(self)
    def settle_owned_recovery(self, attempt, proof, locks):
        from .native_settlement import settle_owned_recovery
        return settle_owned_recovery(self, attempt, proof, locks)
    def maintenance_blocked(self):
        from .maintenance import blocked
        return blocked(self)
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
                    lock = NativeLock(path, create=not self.require_existing_locks)
                    if not lock.acquire(0):
                        raise RecoveryRequired("bootstrap_resource_busy")
                    locks.append(lock)
                identities = {r: lock_identity(os.fstat(lock.fd))
                              for r, lock in zip(sorted(self.lock_paths), locks)}
                self._check_physical_identities({"lock_identity": identities})
                state["lock_identity"] = identities
                state["ready"] = True
                state["bootstrap_evidence"] = dict(evidence, provenance="local_operator_attestation")
                self.store.write("ownership.json", state)
            finally:
                for lock in reversed(locks):
                    lock.close()
    def try_acquire(self, job_id, request_hash, resources, target, *, deadline=None):
        check_id(job_id)
        resources = tuple(sorted(set(resources)))
        if not resources or set(resources) - set(self.lock_paths):
            raise ValueError("unknown_resource")
        locks = []
        with self.mutex.held():
            state = self.snapshot()
            if self.store._revocation(self.store.job_name(job_id)):
                raise RecoveryRequired('job_authority_revoked')
            if state.get('schema') == 2:
                # Before inference, resolve an earlier rename/directory-flush error.
                for path in (self.store.path('ownership.json'), self.store.root):
                    fd = os.open(path, os.O_RDONLY)
                    try: os.fsync(fd)
                    finally: os.close(fd)
            if self.maintenance_blocked():
                return None
            if deadline is not None and time.monotonic() >= deadline:
                return None
            if state.get('recovery_policy') and (
                    not self.boot_reader() or state.get('verified_boot') != self.boot_reader()):
                return None
            if not state["ready"] or any(r in state["owners"] for r in resources):
                return None
            try:
                for resource in resources:
                    path = self.lock_paths[resource]
                    if (not path.is_file() or lock_identity(path.stat()) !=
                            state["lock_identity"][resource]):
                        raise RecoveryRequired("physical_lock_identity_changed")
                    lock = NativeLock(self.lock_paths[resource], create=False)
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
                owner['boot_id'] = self.boot_reader()
                if state.get('schema') == 2:
                    owner['admission_generation'] = state['admission_generation']['id']
                if deadline is not None and time.monotonic() >= deadline:
                    for lock in reversed(locks):
                        lock.close()
                    return None
                for resource in resources:
                    state["owners"][resource] = dict(owner)
                # Durable ownership precedes submission, even when the response is lost.
                self.store.write("ownership.json", state)
                return Lease(self, owner, resources, locks)
            except BaseException:
                for lock in reversed(locks):
                    lock.close()
                raise
    def _modify(self, owner, resources, phase=None, clear=False, detail=None, before_clear=None, validate_clear=None):
        with self.mutex.held():
            state = self.snapshot()
            if state.get('schema') == 2 and owner.get('admission_generation') != state['admission_generation']['id']:
                raise RecoveryRequired('stale_lease_generation')
            if any(state["owners"].get(r, {}).get("lease_id") != owner["lease_id"]
                   for r in resources):
                raise RecoveryRequired("stale_lease")
            if validate_clear is not None:
                if not clear:
                    raise ValueError('clear_validation_requires_release')
                validate_clear()
            if before_clear is not None:
                receipt = before_clear()  # Bound Music proof, using the existing mutex.
                receipts = state.setdefault('music_release_receipts', {})
                if not clear or owner['lease_id'] in receipts:
                    raise RecoveryRequired('music_release_receipt_already_exists')
                receipts[owner['lease_id']] = receipt
            for resource in resources:
                if clear:
                    del state["owners"][resource]
                else:
                    state["owners"][resource]["phase"] = phase
                    if detail:
                        state["owners"][resource]["detail"] = detail
            self.store.write("ownership.json", state)
    def recover(self, job_id, evidence, *, validate_commit=None, timeout=5, commit_deadline=None):
        # Local administrative path only, never exposed by the request API.
        with self.mutex.held(timeout):
            state = self.snapshot()
            matches = {r: o for r, o in state["owners"].items() if o["job_id"] == job_id}
            if not matches:
                records = [d for d in state.get('recovery_dispositions', {}).values()
                           if d.get('owner', {}).get('job_id') == job_id and
                           d.get('approval_ref') == evidence.get('approval_ref') and
                           all(d['owner'].get(k) == evidence.get(k) for k in
                               ('job_id', 'request_hash', 'lease_id', 'target')) and
                           all(evidence.get(k) is True for k in ('operator_approved_backend_recovery',
                               'backend_quiescent', 'cleanup_verified'))]
                if len(records) == 1:
                    return  # Metadata finalization only; no new audit/control/release.
                raise ValueError("no_matching_owner")
            owner = next(iter(matches.values()))
            job = self.store.read(self.store.job_name(job_id), {})
            if job.get('dual_gpu_session') is True:
                proof = job.get('dual_restoration_verified', {})
                if (set(matches) != {'gpu0', 'gpu1'} or
                        any(evidence.get(k) is not True or proof.get(k) is not True
                            for k in ('gpu1_restored', 'backend_ready', 'placement_verified')) or
                        evidence.get('switch_binding') != job.get('switch_binding') or
                        proof.get('switch_binding') != job.get('switch_binding') or
                        proof.get('lease_id') != owner['lease_id'] or proof.get('job_id') != job_id):
                    raise ValueError('dual_gpu1_restoration_evidence_required')
                if proof.get('mode') != 'gpu1' or proof.get('physical_gpus') != ['gpu1']:
                    raise ValueError('dual_original_placement_proof_required')
            if (not evidence.get("approval_ref") or
                    evidence.get("operator_approved_backend_recovery") is not True or
                    evidence.get("backend_quiescent") is not True or
                    evidence.get("cleanup_verified") is not True or
                    any(evidence.get(k) != owner[k] for k in
                        ("job_id", "request_hash", "lease_id", "target")) or
                    any(o["lease_id"] != owner["lease_id"] for o in matches.values())):
                raise ValueError("invalid_or_stale_recovery_evidence")
            if job and (job.get('request_id') != job_id or
                    any(job.get(k) != owner[k] for k in ('request_hash','target')) or
                    ('lease_id' in job and job['lease_id'] != owner['lease_id']) or
                    sorted(job.get('resources', [])) != sorted(matches) or
                    ('boot_id' in job and job['boot_id'] != owner.get('boot_id'))):
                raise ValueError('recovery_job_owner_binding_changed')
            locks = []
            try:
                # Manual idle validation observes both devices. Hold the
                # original physical locks through that proof and atomic clear.
                # The trusted callback is internal, never deserialized from API.
                for resource in sorted(self.lock_paths if validate_commit is not None else matches):
                    path = self.lock_paths[resource]
                    if (not path.is_file() or lock_identity(path.stat()) !=
                            state["lock_identity"][resource]):
                        raise RecoveryRequired("physical_lock_identity_changed")
                    lock = NativeLock(self.lock_paths[resource], create=False)
                    if not lock.acquire(0):
                        raise RecoveryRequired("owner_still_active")
                    locks.append(lock)
                    if lock_identity(os.fstat(lock.fd)) != state["lock_identity"][resource]:
                        raise RecoveryRequired("physical_lock_identity_changed_during_acquisition")
                if validate_commit is not None:
                    validate_commit(state, job, evidence)
                    if self.snapshot() != state or self.store.job(job_id) != job:
                        raise RecoveryRequired('recovery_state_changed_before_commit')
                    self._check_physical_identities(state)
                audit = dict(evidence, recovered_at=timestamp())
                state.setdefault("recoveries", []).append(audit)
                # The failed disposition is durable in the same write as clear.
                # The job file is finalized separately, under the jobs mutex.
                if job:
                    state.setdefault('recovery_dispositions', {})[owner['lease_id']] = {
                        'owner': {k:owner.get(k) for k in ('job_id','request_hash','lease_id','target','boot_id')},
                        'resources': sorted(matches), 'approval_ref': evidence['approval_ref'],
                        'job_sha256': hashlib.sha256(encode(job)).hexdigest(),
                        'audit_sha256': hashlib.sha256(encode(audit)).hexdigest(),
                        'error_code': 'operator_recovered_no_automatic_resubmission'}
                for resource in matches:
                    del state["owners"][resource]
                if commit_deadline is not None and time.monotonic() >= commit_deadline:
                    raise RecoveryRequired('recovery_deadline_before_commit')
                self.store.write("ownership.json", state)
            finally:
                for lock in reversed(locks):
                    lock.close()

    def finalize_recoveries(self, job_id=None, *, timeout=5, checkpoint=None):
        """Idempotent failed-only metadata completion; never backend or lease work."""
        with NativeLock(self.store.path('jobs.lock')).held(timeout):
            state = self.snapshot()
            for lease_id, record in state.get('recovery_dispositions', {}).items():
                if checkpoint:checkpoint()
                owner = record.get('owner', {})
                if job_id is not None and owner.get('job_id') != job_id:
                    continue
                audits=[]
                for a in state.get('recoveries', []):
                    if checkpoint:checkpoint()
                    if hashlib.sha256(encode(a)).hexdigest()==record.get('audit_sha256'):audits.append(a)
                job = self.store.job(owner['job_id'])
                if (lease_id != owner.get('lease_id') or len(audits) != 1 or
                        record.get('error_code') != 'operator_recovered_no_automatic_resubmission' or
                        any(audits[0].get(k) != owner.get(k) for k in ('job_id','request_hash','lease_id','target')) or
                        audits[0].get('approval_ref') != record.get('approval_ref') or
                        not audits[0].get('recovered_at') or not record.get('approval_ref') or
                        any(audits[0].get(k) is not True for k in ('operator_approved_backend_recovery','backend_quiescent','cleanup_verified')) or
                        any(job.get(k) != owner.get(k) for k in ('request_hash','target')) or
                        ('lease_id' in job and job['lease_id'] != owner.get('lease_id')) or
                        ('boot_id' in job and job['boot_id'] != owner.get('boot_id')) or
                        sorted(job.get('resources', [])) != record.get('resources') or
                        any(o.get('job_id') == owner['job_id'] or o.get('lease_id') == lease_id
                            for o in state['owners'].values())):
                    raise RecoveryRequired('recovery_disposition_binding_unverified')
                if job['state'] == 'failed' and job.get('error_code') == record['error_code']:
                    continue
                if hashlib.sha256(encode(job)).hexdigest() != record.get('job_sha256'):
                    raise RecoveryRequired('recovery_disposition_job_changed')
                job.update(state='failed', error_code=record['error_code'], updated_at=timestamp())
                if job.get('host_stage'):
                    job['host_exit_code'] = 70
                if checkpoint:checkpoint()
                self.store.put_job(job)


class Lease:
    def __init__(self, admission, owner, resources, locks):
        self.admission, self.owner = admission, owner
        self.resources, self.locks = resources, locks
    def mark(self, phase, detail=None):
        self.admission._modify(self.owner, self.resources, phase=phase, detail=detail)
    def release(self, proof, *, before_release=None, before_clear=None, validate_clear=None):
        if (proof.get("completion_verified") is not True or
                proof.get("cleanup_verified") is not True):
            raise ValueError("unverified_release")
        if before_release is not None:
            before_release()  # External observations never hold the shared journal mutex.
        self.admission._modify(self.owner, self.resources, clear=True, before_clear=before_clear,
                               validate_clear=validate_clear)
    def close(self):
        for lock in reversed(self.locks):
            lock.close()
        self.locks = []


def retained_completed_ollama(admission, job, owners):
    """Narrow restart preservation, not recovery eligibility or adoption."""
    if not hasattr(admission, 'boot_reader'):
        return False
    if (job.get('target') != 'ollama' or job.get('resources') != ['gpu1'] or
            job.get('state') not in ('reconciling', 'cleanup_pending') or
            job.get('phase') != 'execution_complete' or job.get('completion_verified') is not True or
            job.get('host_stage') or job.get('dual_gpu_session')):
        return False
    owner = owners.get('gpu1', {})
    keys = ('job_id', 'request_hash', 'lease_id', 'target', 'boot_id')
    expected = dict(zip(keys, (job.get('request_id'), *(job.get(k) for k in keys[1:]))))
    if (any(not isinstance(v, str) or not v for v in expected.values()) or
            any(owner.get(k) != v for k, v in expected.items()) or
            {r for r, o in owners.items() if o.get('job_id') == job['request_id']} != {'gpu1'}):
        return False
    state = admission.snapshot()
    context = job.get('ollama_cleanup_context', {})
    policy = state.get('recovery_policy', {})
    if (owner['boot_id'] != admission.boot_reader() or state.get('verified_boot') != owner['boot_id'] or
            context.get('binding') != expected or context.get('resources') != ['gpu1'] or
            context.get('owned_work_completed') is not True or
            policy.get('policy_id') != hashlib.sha256(encode(policy.get('policy'))).hexdigest() or
            context.get('policy_id') != policy.get('policy_id') or
            policy.get('policy', {}).get('scope') != state.get('scope')):
        return False
    admission._check_physical_identities(state)
    return True


class Runner:
    def __init__(self, admission, targets, backends, workers=4, poll_interval=.05,
                 max_pending=64, recovery=None):
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
        self._workers = workers
        self._worker_generation = self._authority_generation()
        self._pool_tasks = {}
        self._retired_executors = []
        self.wake = threading.Event()
        self.recovery = recovery
        self.handoffs = None
        if getattr(admission,'integrated_settings',{}).get('destructive',False):
            from .worker_handoff import WorkerHandoffs
            self.handoffs = WorkerHandoffs(admission)
            if 'wallpaper' in backends:
                backends['wallpaper'].handoff_checkpoint = self.handoffs.checkpoint
        try:
            if self.recovery is None and admission.snapshot().get('recovery_policy'):
                from .recovery import RecoveryManager
                self.recovery = RecoveryManager(admission, targets, backends)
            if any(getattr(t, 'lifecycle', 'always_on') == 'on_demand' for t in targets.values()) and (
                    self.recovery is None or self.recovery.lifecycle is None):
                raise RecoveryRequired('on_demand_requires_authorized_scope_bound_policy')
            # Finish already-committed local recovery metadata before generic
            # startup can rewrite its original, hash-bound nonterminal job.
            if isinstance(admission,Admission):admission.finalize_native_settlements()
            if not admission.maintenance_blocked():
                admission.finalize_recoveries()
                if self.recovery:
                    self.recovery.startup()
            with self.job_mutex.held():
                owners = self.admission.snapshot()["owners"]
                owner_ids = {o["job_id"] for o in owners.values()}
                for job in self.store.jobs():
                    if admission.maintenance_blocked():
                        break  # Preserve exact evidence across interrupted recovery.
                    # Local host stages supervise themselves with the same
                    # Admission journal; API restart cannot rewrite an active stage.
                    if job.get("host_stage") is True:
                        continue
                    # A CPU handover does not reinterpret completed retained
                    # work or erase its precise cause. This is evidence
                    # preservation only; it grants no execution/release authority.
                    if retained_completed_ollama(admission, job, owners):
                        continue
                    # Retain an anchorless ready generation as evidence across attended
                    # installation. No startup replay, adoption or release is authorized.
                    if (job["request_id"] in owner_ids and job.get('target')=='wallpaper' and
                            job.get('state')=='reconciling' and not job.get('execution_anchor') and
                            job.get('wallpaper_lifecycle',{}).get('phase')=='ready' and
                            not job['wallpaper_lifecycle'].get('submission_intent')):
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
            if self.recovery:
                self.recovery.start()
            self.scheduler.start()
        except BaseException:
            if self.recovery:
                self.recovery.close()
            self.service_lock.close()
            raise
    def _authority_generation(self):
        """Read-only: never acquire admission recursively from a store guard."""
        state = self.admission.snapshot()
        generation = state.get('admission_generation')
        if generation is None:
            return 'legacy'
        value = generation.get('id') if isinstance(generation, dict) else None
        if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{32}', value):
            raise RecoveryRequired('invalid_admission_generation')
        return value
    def _assert_worker_generation(self, generation):
        if generation != self._worker_generation or generation != self._authority_generation():
            raise RecoveryRequired('worker_generation_revoked')
    def _reap_retired_locked(self):
        # Keep executor/task/future references until its submitted work has exited.
        self._retired_executors[:] = [g for g in self._retired_executors
            if any(not f.done() for f in g['tasks'])]
        self._pool_tasks = {f: binding for f, binding in self._pool_tasks.items() if not f.done()}
    def _rotate_worker_capacity_locked(self, generation):
        """Caller holds jobs.lock, after a DURABLE authority switch. No I/O effect.

        Return the retired group for nonblocking shutdown OUTSIDE journal locks:
        Future.cancel callbacks must not run under jobs/admission mutexes.
        This method does not implement or authorize E-stop or revoke any lease.
        """
        if self.closed:
            raise Conflict('supervisor_closing')
        if generation != self._authority_generation():
            raise RecoveryRequired('worker_generation_not_committed')
        if generation == self._worker_generation:
            return None
        self._reap_retired_locked()
        replacement = concurrent.futures.ThreadPoolExecutor(max_workers=self._workers,
            thread_name_prefix='gpu-runner-'+generation[:8])
        old = dict(generation=self._worker_generation, pool=self.pool,
                   tasks=self._pool_tasks, futures=self.futures, pending=self.pending)
        self.pool, self._pool_tasks = replacement, {}
        self.futures, self.pending = {}, {}
        self._worker_generation = generation
        self._retired_executors.append(old)
        return old
    @staticmethod
    def _retire_executor(group):
        if group is None:
            return
        # Does not interrupt or join a running callable. Queued tasks' callbacks
        # close only their captured old Lease descriptors; never release owners.
        group['pool'].shutdown(wait=False, cancel_futures=True)
        for future in group['futures'].values():
            if not future.running() and not future.done():
                future.cancel()
    def rotate_worker_capacity(self, generation):
        """CPU capacity handover only; not a public administrative mutation API."""
        with self.job_mutex.held():
            group = self._rotate_worker_capacity_locked(generation)
        self._retire_executor(group)
        self.wake.set()
        return group is not None
    @staticmethod
    def _queued_task_done(task, lease, future):
        if task.cancelled():
            lease.close()
            future.cancel()
    def _submit_owned_locked(self, job_id, lease, future):
        generation = self._worker_generation
        task = self.pool.submit(self._run_owned, job_id, lease, future, generation)
        self._pool_tasks[task] = (lease, future)
        task.add_done_callback(lambda value: self._queued_task_done(value, lease, future))
        return task
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
        if self.targets[target].mode_switch is not None:
            raise ValueError('dual_target_requires_local_meeting_session')
        request_hash = hashlib.sha256(encode({"target": target, "operation": operation,
                                              "payload": payload})).hexdigest()
        with self.job_mutex.held():
            if self.admission.maintenance_blocked():
                raise Conflict('recovery_maintenance')
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
            job['boot_id'] = self.admission.boot_reader()
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
            if self.admission.maintenance_blocked():
                raise Conflict('recovery_maintenance')
            job = self.store.job(check_id(job_id))
            if job["state"] != "waiting":
                return False
            job.update(state="cancelled", error_code="cancelled_before_submission", updated_at=timestamp())
            self.store.put_job(job)
            return True
    def _finish_pending(self, job_id, state=None, error_code=None, *, generation=None):
        with self.job_mutex.held():
            if generation is not None and generation != self._worker_generation:
                return
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
            try:
                with self.job_mutex.held():
                    g = self._authority_generation()
                    group = self._rotate_worker_capacity_locked(g) if g != self._worker_generation else None
                self._retire_executor(group)
            except Exception:
                if self.closed:
                    return
                self.wake.wait(self.poll_interval);self.wake.clear()
                continue
            if self.admission.maintenance_blocked() and not self.closed:
                self.wake.wait(self.poll_interval)
                self.wake.clear()
                continue
            with self.job_mutex.held():
                self._reap_retired_locked()
                pending = tuple(self.pending.items())
                generation = self._worker_generation
            for job_id, deadline in pending:
                try:
                    job = self.status(job_id)
                    if job["state"] == "cancelled":
                        self._finish_pending(job_id, generation=generation)
                        continue
                    if self.closed:
                        self._finish_pending(job_id, "cancelled", "supervisor_closing", generation=generation)
                        continue
                    if time.monotonic() >= deadline:
                        self._finish_pending(job_id, "failed", "resource_acquisition_timeout", generation=generation)
                        continue
                    target = self.targets[job["target"]]
                    with self.job_mutex.held():
                        if generation != self._worker_generation:
                            continue
                        lease = self.admission.try_acquire(job_id, job["request_hash"],
                                                           target.resources, target.name, deadline=deadline)
                        if lease is None:
                            continue
                        self.pending.pop(job_id, None)
                        if time.monotonic() >= deadline:
                            lease.release({'completion_verified': True, 'cleanup_verified': True,
                                           'policy': 'never_submitted'})
                            lease.close()
                            # Finish outside the non-reentrant jobs mutex.
                            expired = True
                        else:
                            expired = False
                            self._submit_owned_locked(job_id, lease, self.futures[job_id])
                    if expired:
                        self._finish_pending(job_id, 'failed', 'resource_acquisition_timeout', generation=generation)
                except Exception as exc:
                    try:
                        self._finish_pending(job_id, "reconciling", "admission_or_storage_failure", generation=generation)
                    except Exception:
                        with self.job_mutex.held():
                            if generation != self._worker_generation:
                                continue
                            future = self.futures[job_id]
                            self.pending.pop(job_id, None)
                        if not future.done():
                            future.set_exception(exc)
            if self.closed:
                return
            self.wake.wait(self.poll_interval)
            self.wake.clear()
    def _run_owned(self, job_id, lease, future, generation=None):
        generation = self._worker_generation if generation is None else generation
        previous_guard = getattr(self.store._write_context, 'guard', None)
        self.store._write_context.guard = lambda: self._assert_worker_generation(generation)
        handoff = self.handoffs if lease.owner['target']=='wallpaper' else None
        entered = False
        try:
            self._assert_worker_generation(generation)
            if handoff:
                handoff.enter(job_id,lease)
                entered = True
            if not future.set_running_or_notify_cancel():
                lease.release({"completion_verified": True, "cleanup_verified": True,
                               "policy": "never_submitted"})
                return
            self._work(job_id, lease)
            self._assert_worker_generation(generation)
            if self.recovery and generation == self._worker_generation:
                self._assert_worker_generation(generation)
                self.recovery.notify()
        except BaseException as exc:
            from .worker_handoff import WorkerHandoff
            if not future.done():
                if isinstance(exc,WorkerHandoff):future.set_result(None)
                else:future.set_exception(exc)
        else:
            if not future.done():future.set_result(None)
        finally:
            lease.close()
            try:
                if entered and generation == self._worker_generation:
                    self._assert_worker_generation(generation)
                    handoff.exit(job_id,lease)
            finally:
                self.store._write_context.guard = previous_guard
    def _work(self, job_id, lease):
        from .backends import CleanupPending, Uncertain
        guard, cleanup_stage, music = None, None, None
        def cleanup_details(exc):
            if cleanup_stage is None:
                return {}
            from .ollama_cleanup import cleanup_failure
            detail = guard.failure(exc) if guard else cleanup_failure(exc, cleanup_stage)
            return {'ollama_cleanup_failure': detail}
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
            if getattr(target, 'lifecycle', 'always_on') == 'on_demand':
                self.recovery.lifecycle.run(self, job, lease)
                return
            lease.mark("submit_intent")
            if self.recovery:
                self.recovery.prepare(job_id, lease)
            def progress(phase, detail=None):
                lease.mark(phase, detail)
                self._update(job_id, backend_detail=detail or {}, phase=phase)
            execution = backend.execute(job["operation"], job["payload"],
                                        time.monotonic() + target.execution_timeout, progress)
            if self.recovery and execution.error_code:
                self._update(job_id, owned_execution_failed=True)
            lease.mark("cleanup_pending", execution.handle)
            self._update(job_id, state="cleanup_pending", backend_detail=execution.handle,
                         completion_verified=True)
            from .ollama_cleanup import cleanup_guard
            cleanup_deadline = time.monotonic() + target.cleanup_timeout
            cleanup_stage = 'factory'
            guard = cleanup_guard(self.admission, target, lease.owner, backend,
                observer=self.recovery.observer if self.recovery else None,
                record=lambda **fields: self._update(job_id, **fields))
            if guard:
                guard.prepare(cleanup_deadline)
                guard.mark_phase('owned_unload')
            else:
                cleanup_stage = None
            from .music_cleanup import music_guard
            music = music_guard(self.admission, target, lease.owner,
                self.recovery.observer if self.recovery else None,
                lambda **fields: self._update(job_id, **fields))
            if music:
                music.prepare(cleanup_deadline)
                proof = backend.cleanup(execution, cleanup_deadline, music_guard=music)
                proof = dict(proof, music_release_idle=music.verify(cleanup_deadline))
            else:
                proof = backend.cleanup(execution, cleanup_deadline)
            if guard:
                proof = dict(proof, physical_ollama_idle=guard.verify(cleanup_deadline))
            if proof.get("completion_verified") is not True or proof.get("cleanup_verified") is not True:
                raise CleanupPending("cleanup_not_verified")
            # Keep diagnostic attribution through publication and durable release.
            cleanup_stage = 'limits' if guard else None
            if music:
                music.recheck(cleanup_deadline)
            if guard:
                guard.commit_check(cleanup_deadline)
            result = {"value": execution.value, "media_type": execution.media_type,
                      "artifact_available": execution.artifact is not None}
            if execution.artifact is not None:
                self.store.write_bytes("results/" + job_id + ".bin", execution.artifact)
            self.store.write("results/" + job_id + ".json", result)
            if music:
                music.recheck(cleanup_deadline)
            if guard:
                guard.commit_check(cleanup_deadline)
            # Result publication and positive cleanup proof precede resource handoff.
            self._update(job_id, state=("failed" if execution.error_code else "success"),
                         error_code=execution.error_code, cleanup_verified=True, cleanup_proof=proof)
            if music:
                music.recheck(cleanup_deadline, terminal=True)
            if music:
                lease.release(proof, before_release=lambda: music.observe_release(cleanup_deadline),
                              before_clear=lambda: music.release_binding(cleanup_deadline))
            elif guard:
                lease.release(proof, before_release=lambda: guard.commit_check(cleanup_deadline, terminal=True),
                              validate_clear=lambda: guard.release_binding(cleanup_deadline))
            else:
                lease.release(proof)
        except CleanupPending as exc:
            if lease:
                lease.mark("cleanup_pending")
            self._update(job_id, state="cleanup_pending", error_code=exc.code,
                         **cleanup_details(exc),
                         **({'lifecycle_failure': exc.code} if self.store.job(job_id).get('wallpaper_lifecycle') else {}))
        except Uncertain as exc:
            if lease:
                lease.mark("reconciling")
            self._update(job_id, state="reconciling", error_code=exc.code,
                         **cleanup_details(exc),
                         **({'lifecycle_failure': exc.code} if self.store.job(job_id).get('wallpaper_lifecycle') else {}))
        except Exception as exc:
            # Completed output must remain retrievable when only the ownership
            # clearing write reports failure after effect. If the old owner
            # remains, retain cleanup_pending instead of pretending handoff.
            current = self.store.job(job_id)
            from .music_cleanup import failure_code
            music_code = failure_code(exc) if music is not None else None
            if current["state"] in FINAL and current.get("cleanup_verified") is True:
                owners = self.admission.snapshot()["owners"]
                still_owned = lease and any(owners.get(r, {}).get("lease_id") ==
                                             lease.owner["lease_id"] for r in lease.resources)
                if still_owned:
                    detail = cleanup_details(exc)
                    self._update(job_id, state="cleanup_pending",
                                 error_code=(detail['ollama_cleanup_failure']['reason'] if detail else
                                             music_code or "ownership_release_unconfirmed"),
                                 publication_warning="ownership_release_unconfirmed", **detail)
                else:
                    self._update(job_id, publication_warning="ownership_release_sync_unconfirmed")
                return
            if lease:
                try:
                    lease.mark("reconciling")
                except Exception:
                    pass
            detail = cleanup_details(exc)
            self._update(job_id, state="reconciling",
                         error_code=(detail['ollama_cleanup_failure']['reason'] if detail else
                                     music_code or "supervision_or_storage_failure"),
                         **detail,
                         **({'lifecycle_operator_error': str(exc)} if isinstance(exc, RecoveryRequired) and
                            self.store.job(job_id).get('wallpaper_lifecycle') else {}))
        finally:
            if lease:
                lease.close()
    def result(self, job_id):
        job = self.status(job_id)
        if job.get('authority_revoked'):
            return dict(value=None, state='failed', error_code=job['error_code'],
                        artifact_available=False, authority_revoked=True, replayed=False)
        if job.get('ollama_cleanup_context'):
            future = self.futures.get(job_id)
            if ((future is not None and not future.done()) or any(
                    o.get('job_id') == job_id or o.get('lease_id') == job.get('lease_id')
                    for o in self.admission.snapshot()['owners'].values())):
                raise Conflict('ollama_result_ownership_not_released')
        if job.get('music_cleanup_context'):
            state = self.admission.snapshot()
            future = self.futures.get(job_id)
            if ((future is not None and not future.done()) or any(o['job_id'] == job_id for o in
                                                        state['owners'].values()) or any(
                    o.get('lease_id') == job.get('lease_id') for o in state['owners'].values())):
                raise Conflict('music_result_ownership_not_released')
            from .music_cleanup import result_released
            if not result_released(job, state):
                raise Conflict('music_release_receipt_unverified')
        if job.get('wallpaper_lifecycle'):
            future = self.futures.get(job_id)
            if ((future is not None and not future.done()) or any(o['job_id'] == job_id for o in
                                                        self.admission.snapshot()['owners'].values())):
                raise Conflict('wallpaper_result_ownership_not_released')
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
        for group in self._retired_executors:
            group['pool'].shutdown(wait=True, cancel_futures=False)
        self._retired_executors.clear()
        if self.recovery:
            self.recovery.close()
        self.service_lock.close()
