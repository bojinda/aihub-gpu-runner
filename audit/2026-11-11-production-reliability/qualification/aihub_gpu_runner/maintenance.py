"""Durable, fail-closed admission gate. No timers reopen maintenance.

The deployment must set control_required=True in EVERY Admission user. Gate
mutations and ingress permits share admission.lock with physical admission.
The journal belongs to the runner service, never to Home Assistant.
"""
import contextlib
import uuid
from .core import Conflict, NativeLock, RecoveryRequired, timestamp

FILE = 'recovery-control.json'


def read_gate(store, required=False):
    value = store.read(FILE)
    if value is None and not required:
        return None
    if (not isinstance(value, dict) or value.get('schema') != 1 or
            value.get('mode') not in ('open', 'closed') or
            not isinstance(value.get('attempts'), dict) or
            not isinstance(value.get('permits'), dict) or
            type(value.get('epoch')) is not int or value['epoch'] < 0 or
            (value['mode'] == 'closed' and value.get('active') not in value['attempts']) or
            (value['mode'] == 'open' and value.get('active') is not None)):
        raise RecoveryRequired('maintenance_journal_unavailable')
    return value


def blocked(admission):
    try:
        # Successor contract: paired retirement and its admission fence share
        # the ownership journal. No new behavior without explicit provisioning.
        state = admission.snapshot()
        if 'practical_control' in state:
            control = state['practical_control']
            if (not isinstance(control, dict) or control.get('admission') != 'open' or
                    control.get('active') is not None or not isinstance(control.get('attempts'), dict)):
                return True
        value = read_gate(admission.store, admission.control_required)
        return value is not None and value['mode'] != 'open'
    except Exception:
        return True


def initialize(admission):
    """Installation-only operation; never called by Check, startup or HTTP."""
    with admission.mutex.held():
        if admission.store.path(FILE).exists():
            raise Conflict('maintenance_already_initialized')
        for name in ('recovery-control.lock', 'maintenance.lock', 'recovery.lock'):
            with NativeLock(admission.store.path(name)).held():
                pass
        admission.store.write(FILE, dict(schema=1, mode='open', active=None,
                                        epoch=0, attempts={}, permits={}))


class IngressGate:
    """One permit per complete proxied TCP connection, including WebSockets.

    Lost processes leave a durable permit: no timeout guesses that work ended.
    Read-only observer routes are separate, fixed and unexposed to clients.
    """
    def __init__(self, admission, client):
        self.a, self.client = admission, client

    @contextlib.contextmanager
    def connection(self):
        permit = uuid.uuid4().hex
        with self.a.mutex.held():
            gate = read_gate(self.a.store, True)
            if gate['mode'] != 'open':
                raise Conflict('recovery_maintenance')
            gate['permits'][permit] = dict(client=self.client, boot=self.a.boot_reader(),
                                           opened_at=timestamp())
            gate['epoch'] += 1
            self.a.store.write(FILE, gate)
        try:
            yield permit
        finally:
            with self.a.mutex.held():
                gate = read_gate(self.a.store, True)
                if permit not in gate['permits']:
                    raise RecoveryRequired('ingress_permit_lost')
                del gate['permits'][permit]
                gate['epoch'] += 1
                self.a.store.write(FILE, gate)
