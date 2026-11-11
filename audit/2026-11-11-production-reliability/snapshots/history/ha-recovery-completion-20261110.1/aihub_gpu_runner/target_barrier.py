"""Durable Wallpaper mutation barrier, shared by the real ComfyUI queue and runner.

No network rules. GETs/WebSockets do not acquire connection-long permits.
All queue insertion/dequeue and HTTP mutations linearize through this lock.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import time
import uuid

from .core import AtomicStore, NativeLock, RecoveryRequired, encode, guest_boot_id


def birth(pid):
    try:
        value=Path('/proc',str(pid),'stat').read_text()
        fields=value[value.rfind(')')+2:].split()
        return fields[19] if fields[0] != 'Z' else None
    except (FileNotFoundError,ProcessLookupError):return None


class SharedStore(AtomicStore):
    def write_bytes(self, name, data):
        dest=self.path(name);tmp=dest.with_name('.'+dest.name+'.'+uuid.uuid4().hex)
        fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            owner=self.root.stat()
            if os.geteuid()==0:os.fchown(fd,owner.st_uid,owner.st_gid)
            with os.fdopen(fd,'wb') as stream:
                stream.write(data);stream.flush();os.fsync(stream.fileno())
            os.replace(tmp,dest)
            directory=os.open(dest.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(directory)
            finally:os.close(directory)
        finally:
            if tmp.exists():tmp.unlink()


class TargetBarrier:
    def __init__(self, directory, *, create=False):
        self.root=Path(directory).resolve()
        if not create and not self.root.is_dir():raise RecoveryRequired('target_barrier_not_installed')
        self.store=SharedStore(self.root)
        self.lock=NativeLock(self.root/'target.lock',create=create)

    def read(self):
        s=self.store.read('target.json')
        if (not isinstance(s,dict) or s.get('schema')!=1 or s.get('mode') not in ('open','closed') or
                not isinstance(s.get('epoch'),int) or not isinstance(s.get('inflight'),dict) or
                not isinstance(s.get('prompts'),dict)):
            raise RecoveryRequired('target_barrier_journal_unavailable')
        return s

    def register(self, sources):
        generation=dict(id=uuid.uuid4().hex,boot=guest_boot_id(),pid=os.getpid(),birth=birth(os.getpid()),
            pidns=os.stat('/proc/self/ns/pid').st_ino,sources=sources)
        with self.lock.held():
            previous=self.store.read('target.json')
            if previous:
                old=previous['generation']
                if (old['boot']==generation['boot'] and old['pidns']==generation['pidns'] and
                        birth(old['pid'])==old['birth']):
                    raise RecoveryRequired('target_server_already_registered')
                archive='generation-'+old['id']+'.json'
                if not self.store.path(archive).exists():self.store.write(archive,previous)
            state=dict(schema=1,generation=generation,epoch=(previous or {}).get('epoch',0)+1,
                mode=(previous or {}).get('mode','open'),operation=(previous or {}).get('operation'),
                inflight={},prompts={},running={},pending=[],signals=(previous or {}).get('signals',{}),
                previous_generation=(previous or {}).get('generation'))
            self.store.write('target.json',state)
        return generation

    def begin_mutation(self):
        with self.lock.held():
            s=self.read()
            if s['mode']!='open':raise RecoveryRequired('wallpaper_recovery_maintenance')
            token=uuid.uuid4().hex
            s['inflight'][token]={'pid':os.getpid(),'birth':birth(os.getpid())}
            s['epoch']+=1;self.store.write('target.json',s)
            return token

    def end_mutation(self, token):
        with self.lock.held():
            s=self.read();s['inflight'].pop(token,None);self.store.write('target.json',s)

    def fence(self, operation, epoch, generation):
        with self.lock.held():
            s=self.read()
            if s['generation']!=generation or s['epoch']!=epoch:
                raise RecoveryRequired('target_write_or_generation_changed')
            if s['inflight']:raise RecoveryRequired('target_mutation_inflight')
            s.update(mode='closed',operation=operation)
            self.store.write('target.json',s)

    def restore(self, operation):
        with self.lock.held():
            s=self.read()
            if s['mode']=='open' and s['operation'] is None:return
            if s['operation']!=operation:raise RecoveryRequired('different_target_fence_owner')
            s.update(mode='open',operation=None);self.store.write('target.json',s)


def submission_ticket(key, generation, identity):
    payload=dict(generation=generation['id'],identity=identity,expires=time.time()+60,nonce=uuid.uuid4().hex)
    raw=encode(payload).hex()
    return raw+'.'+hmac.new(key,raw.encode(),hashlib.sha256).hexdigest()


def verify_submission(key, token, generation):
    try:
        raw,mac=token.split('.')
        if len(token)>8192 or not hmac.compare_digest(mac,hmac.new(key,raw.encode(),hashlib.sha256).hexdigest()):raise ValueError()
        value=json.loads(bytes.fromhex(raw))
        if value['generation']!=generation['id'] or not time.time()<value['expires']<=time.time()+61:raise ValueError()
        identity=value['identity']
        if identity['boot_id']!=guest_boot_id() or any(not isinstance(identity[k],str) or not identity[k]
                for k in ('job_id','lease_id','request_hash')):raise ValueError()
        return value
    except (ValueError,KeyError,TypeError):raise RecoveryRequired('invalid_owned_submission_ticket') from None
