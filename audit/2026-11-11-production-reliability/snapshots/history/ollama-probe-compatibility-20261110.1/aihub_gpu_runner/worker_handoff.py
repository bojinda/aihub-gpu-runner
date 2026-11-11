"""Cooperative handoff of an exact original worker; never unlock another thread."""
import threading
import uuid
from .core import RecoveryRequired, timestamp


class WorkerHandoff(BaseException):
    pass


class WorkerHandoffs:
    def __init__(self, admission):
        self.a=admission;self.lock=threading.RLock();self.entries={};self.local=threading.local()

    def enter(self, job_id, lease):
        with self.lock:
            entry=dict(id=uuid.uuid4().hex,job_id=job_id,lease_id=lease.owner['lease_id'],
                       request=None,accepted=False,done=threading.Event(),active=True)
            self.entries[job_id]=entry;self.local.entry=entry

    def checkpoint(self):
        with self.lock:
            entry=getattr(self.local,'entry',None)
            if entry and entry['request']:
                entry['accepted']=True
                raise WorkerHandoff()

    def view(self, job_id):
        with self.lock:
            e=self.entries.get(job_id)
            return dict({k:e[k] for k in ('id','job_id','lease_id','active')},pending=bool(e['request'])) if e else None

    def exit(self, job_id, lease):
        # Caller has closed its own Lease descriptors before this receipt.
        if lease.locks:raise RecoveryRequired('worker_handoff_locks_not_closed')
        with self.lock:
            e=self.entries[job_id]
        # Admission readers may ask for the worker view. Never take admission
        # while holding the registry lock (the opposite lock order).
        if e['accepted']:
            with self.a.mutex.held():
                state=self.a.snapshot()
                state.setdefault('worker_handoffs',{})[e['request']]={k:e[k] for k in ('id','job_id','lease_id')}
                state['worker_handoffs'][e['request']]['released_at']=timestamp()
                self.a.store.write('ownership.json',state)
        with self.lock:
            e['active']=False
            e['done'].set();self.local.entry=None

    def request(self, job_id, lease_id, worker_id, operation, timeout=15):
        with self.lock:
            e=self.entries.get(job_id)
            if not e or e['id']!=worker_id or e['lease_id']!=lease_id:
                raise RecoveryRequired('original_worker_changed')
            if e['request'] not in (None,operation):raise RecoveryRequired('worker_handoff_already_pending')
            if not e['active']:return False
            e['request']=operation
        if not e['done'].wait(timeout):
            with self.lock:
                if not e['accepted']:e['request']=None
            raise RecoveryRequired('worker_handoff_pending' if e['accepted'] else 'worker_handoff_timeout_no_lock_break')
        receipt=self.a.snapshot().get('worker_handoffs',{}).get(operation)
        if not receipt or any(receipt[k]!=e[k] for k in ('id','job_id','lease_id')):
            raise RecoveryRequired('worker_handoff_receipt_missing')
        return True
