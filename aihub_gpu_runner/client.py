"""Reusable job client and CPU output-operation guard; no GPU admission here."""
from contextvars import ContextVar
import hashlib
import math
import os
import tempfile
from pathlib import Path
import time
from urllib.parse import urlsplit
import urllib.request
import urllib.error
import uuid

from .core import AtomicStore, NativeLock, check_id, encode, strict_json, timestamp
from .backends import NoRedirect


class CoordinationError(Exception):
    """Fail closed on configuration/conflict/uncertain coordinator state."""


class PendingJob(CoordinationError):
    def __init__(self, request_id, state):
        self.request_id, self.state = request_id, state
        super().__init__('runner job ' + request_id + ': ' + state)


class KnownJobFailure(Exception):
    """A terminal failed job whose ownership and descriptor have been released."""


class APITransport:
    def __init__(self, base_url, token, timeout=10):
        url = urlsplit(base_url)
        if (url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password
                or url.path not in ('', '/') or url.query or url.fragment or not token):
            raise CoordinationError('invalid_runner_configuration')
        self.base, self.token, self.timeout = base_url.rstrip('/'), token, timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    def request(self, method, route, payload=None):
        request = urllib.request.Request(self.base + route,
            data=None if payload is None else encode(payload), method=method,
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                data = response.read(32 * 1024 * 1024 + 1)
                if len(data) > 32 * 1024 * 1024:
                    raise OSError('bounded_response_required')
                return response.status, response.headers.get_content_type(), data
        except urllib.error.HTTPError as exc:
            return exc.code, 'application/json', b'{}'
        except Exception:
            raise OSError('runner_transport_unavailable') from None


class JobClient:
    def __init__(self, transport, wait_timeout=3600, poll_interval=.2):
        if (type(wait_timeout) not in (int, float) or not math.isfinite(wait_timeout) or wait_timeout <= 0
                or type(poll_interval) not in (int, float) or not math.isfinite(poll_interval) or poll_interval <= 0):
            raise CoordinationError('invalid_client_deadline')
        self.transport, self.wait_timeout, self.poll_interval = transport, wait_timeout, poll_interval
    @classmethod
    def from_environment(cls):
        token = os.environ.get('GPU_RUNNER_TOKEN', '')
        if len(token) < 32:
            raise CoordinationError('private_runner_token_required')
        try:
            seconds = float(os.environ.get('GPU_RUNNER_WAIT_SECONDS', '3600'))
        except ValueError:
            raise CoordinationError('invalid_client_deadline') from None
        return cls(APITransport(os.environ.get('GPU_RUNNER_URL', ''), token), seconds)
    @staticmethod
    def stage_id(operation_id, step):
        return 'stage-' + hashlib.sha256(encode([check_id(operation_id), step])).hexdigest()[:48]
    def _request(self, request_id, method, route, payload=None, binary=False):
        try:
            status, media, data = self.transport.request(method, route, payload)
        except Exception:
            raise PendingJob(request_id, 'transport_uncertain') from None
        if status >= 500:
            raise PendingJob(request_id, 'coordinator_unavailable')
        if status not in (200, 202):
            raise CoordinationError('runner_request_rejected_' + str(status))
        if binary:
            return data, media
        try:
            value = strict_json(data)
            if not isinstance(value, dict):
                raise ValueError('not_object')
            return value
        except (ValueError, UnicodeError):
            raise PendingJob(request_id, 'invalid_coordinator_response') from None
    def status(self, request_id):
        return self._request(check_id(request_id), 'GET', '/jobs/' + request_id)
    def result(self, request_id):
        return self._request(check_id(request_id), 'GET', '/jobs/' + request_id + '/result')
    def run(self, request_id, target, operation, payload, artifact=False, on_status=None):
        check_id(request_id)
        expected = hashlib.sha256(encode({'target': target, 'operation': operation, 'payload': payload})).hexdigest()
        status = self._request(request_id, 'POST', '/jobs',
                               {'request_id': request_id, 'target': target, 'operation': operation, 'payload': payload})
        deadline = time.monotonic() + self.wait_timeout
        while True:
            if status.get('request_hash') != expected or status.get('target') != target:
                raise CoordinationError('runner_job_identity_mismatch')
            if on_status is not None:
                on_status(status)
            state = status.get('state')
            if state in ('success', 'failed', 'cancelled') and status.get('resources_released') is True:
                result = self._request(request_id, 'GET', '/jobs/' + request_id + '/result')
                if state != 'success':
                    raise KnownJobFailure('runner_job_failed_' + str(result.get('error_code') or state))
                if artifact:
                    if result.get('artifact_available') is not True:
                        raise KnownJobFailure('runner_artifact_missing')
                    return self._request(request_id, 'GET', '/jobs/' + request_id + '/artifact', binary=True)
                return result['value']
            if state == 'reconciling' or state == 'cleanup_pending' and status.get('supervision_active') is False:
                raise PendingJob(request_id, state)
            if time.monotonic() >= deadline:
                raise PendingJob(request_id, state or 'client_wait_timeout')
            time.sleep(self.poll_interval)
            status = self._request(request_id, 'GET', '/jobs/' + request_id)


CURRENT_OPERATION = ContextVar('runner_client_output_operation', default=None)


class OutputBusy(CoordinationError):
    pass


class OutputSlot:
    """Serialize CPU writers to configured output paths, not GPU resources.

    Uses existing NativeLock/AtomicStore primitives. No scheduler, admission
    ledger, backend recovery or automatic workflow replay is implemented here.
    """
    def __init__(self, state_dir, output_paths):
        self.store = AtomicStore(state_dir)
        self.scope = hashlib.sha256(encode([str(Path(path).resolve()) for path in output_paths])).hexdigest()
        self.lock_path = self.store.path('output-' + self.scope + '.lock')
    def begin(self, kind, request_id=None):
        request_id = check_id(request_id or uuid.uuid4().hex)
        lock = NativeLock(self.lock_path)
        if not lock.acquire(0):
            raise OutputBusy('output_operation_busy')
        try:
            name = 'operations/' + request_id + '.json'
            old = self.store.read(name)
            if old:
                if old['scope'] != self.scope or old['kind'] != kind:
                    raise CoordinationError('output_operation_id_conflict')
                if old['status'] != 'ok':
                    raise CoordinationError('previous_output_operation_' + old['status'] + '_no_replay')
                lock.close()
                return OutputOperation(self, name, old, None)
            record = {'request_id': request_id, 'scope': self.scope, 'kind': kind,
                      'status': 'running', 'started': time.time(), 'finished': None, 'error': None}
            self.store.write(name, record)
            self.store.write('active.json', record)
            return OutputOperation(self, name, record, lock)
        except BaseException:
            lock.close()
            raise
    def operation_status(self, request_id):
        record = self.store.read('operations/' + check_id(request_id) + '.json')
        if record is not None and record['status'] == 'running':
            active = self.status()
            if active.get('request_id') != request_id or active['status'] != 'running':
                return dict(record, status='error', error='output_supervisor_unavailable_no_replay')
        return record
    def status(self):
        record = self.store.read('active.json',
                                 {'status': 'idle', 'started': None, 'finished': None, 'error': None})
        if record['status'] == 'running':
            probe = NativeLock(self.lock_path)
            if probe.acquire(0):
                probe.close()
                return dict(record, status='error', error='output_supervisor_unavailable_no_replay')
        return record


class OutputOperation:
    def __init__(self, slot, name, record, lock):
        self.slot, self.name, self.record, self.lock = slot, name, record, lock
        self.cached = lock is None
    @property
    def request_id(self):
        return self.record['request_id']
    def snapshot(self, step, value):
        """Persist randomized backend payload once for this operation's stable ID."""
        key = hashlib.sha256(encode(step)).hexdigest()
        name = 'plans/' + self.request_id + '-' + key + '.json'
        old = self.slot.store.read(name)
        if old is not None:
            return old
        self.slot.store.write(name, value)
        return value
    def update_details(self, **fields):
        """Persist application status while this CPU output operation is owned."""
        if self.cached or self.lock is None:
            raise CoordinationError('output_operation_not_owned')
        details = self.record.setdefault('details', {})
        if all(details.get(key) == value for key, value in fields.items()):
            return
        details.update(fields)
        self.slot.store.write(self.name, self.record)
        self.slot.store.write('active.json', self.record)
    def finish(self, response=None, error=None):
        if self.cached:
            return
        try:
            self.record.update(status='ok' if error is None else 'error', finished=time.time(),
                               error=error, response=response)
            self.slot.store.write(self.name, self.record)
            self.slot.store.write('active.json', self.record)
        finally:
            if self.lock:
                self.lock.close()
                self.lock = None


def publish_bytes(path, data, mode=0o644):
    """Publish validated caller bytes atomically; never expose a partial file."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.tmp-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            if os.name == 'nt':
                os.chmod(temporary, mode)
            else:
                os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        if os.name != 'nt':
            directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
