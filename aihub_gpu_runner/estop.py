"""Explicit administrative coordination relinquishment. Never calls a backend.

All journal writers must be upgraded together. No mixed-version compatibility.
"""
import base64,contextlib,copy,hashlib,hmac,os,re,time,uuid
from pathlib import Path
from .core import NativeLock,RecoveryRequired,encode,strict_json,lock_identity

ERROR='administrator_estop_no_replay'
def digest(value):return hashlib.sha256(encode(value)).hexdigest()

def activate_generations(admission, *, confirmed_coordinated_shutdown=False):
    """E2 installation-only. Preserve leases/history; never called by HTTP/startup."""
    if confirmed_coordinated_shutdown is not True:
        raise RecoveryRequired('coordinated_shutdown_confirmation_required')
    s=admission.store
    with NativeLock(s.path('jobs.lock')).held(),admission.mutex.held(),contextlib.ExitStack() as stack:
        state=admission.snapshot()
        if state.get('schema')==2:raise RecoveryRequired('generation_protocol_already_active')
        if set(admission.lock_paths)!={'gpu0','gpu1'}:raise RecoveryRequired('both_gpu_resources_required')
        boot=admission.boot_reader()
        if not boot or state.get('verified_boot') not in (None,boot):raise RecoveryRequired('activation_boot_binding_changed')
        for p in [s.path('supervisor.lock'),*s.root.glob('host-*.lock'),*admission.lock_paths.values()]:
            stack.enter_context(NativeLock(p,create=p.name=='supervisor.lock').held(0))
        admission._check_physical_identities(state)
        g=uuid.uuid4().hex;origin={'paths':{r:str(p) for r,p in admission.lock_paths.items()},'lock_identity':copy.deepcopy(state['lock_identity'])}
        state.update(schema=2,verified_boot=boot,authority_revision=0,estop_revocations={},estop_attempts={},estop_intents={},
            admission_generation=dict(id=g,sequence=0,paths=origin['paths'],lock_identity=origin['lock_identity'],origin=origin))
        for owner in state['owners'].values():owner['admission_generation']=g
        # Original job files, lease IDs, scopes, policy and physical inodes stay intact.
        s.write('ownership.json',state)
        return g

class Estop:
    def __init__(self,runner,key,clock=time.time,*,fault=None):
        if len(key)<32:raise ValueError('strong_estop_key_required')
        self.runner=runner;self.a=runner.admission;self.store=runner.store;self.key=key;self.clock=clock
        self.fault=fault or (lambda phase:None) # test injection, never caller configuration
    @contextlib.contextmanager
    def locked(self):
        deadline=time.monotonic()+3
        jobs=NativeLock(self.store.path('jobs.lock'));admission=NativeLock(self.store.path('admission.lock'))
        if not jobs.acquire(max(0,deadline-time.monotonic())):raise RecoveryRequired('estop_jobs_busy')
        try:
            if not admission.acquire(max(0,deadline-time.monotonic())):raise RecoveryRequired('estop_admission_busy')
            try:yield deadline
            finally:admission.close()
        finally:jobs.close()
    def state(self):
        state=self.a.snapshot()
        if state.get('schema')!=2:raise RecoveryRequired('generation_protocol_upgrade_required')
        if not self.a.boot_reader():raise RecoveryRequired('boot_unavailable')
        return state
    def affected(self,state):
        ids={o['job_id'] for o in state['owners'].values()}
        jobs={j['request_id']:j for j in self.store.jobs() if not j.get('authority_revoked') and (j['state'] not in ('success','failed','cancelled') or j['request_id'] in ids)}
        if ids-set(jobs):raise RecoveryRequired('owner_job_unavailable')
        for r,o in state['owners'].items():
            job=jobs[o['job_id']]
            if job.get('request_hash')!=o['request_hash'] or job.get('lease_id',o['lease_id'])!=o['lease_id']:
                raise RecoveryRequired('owner_job_binding_changed')
        return jobs
    def binding(self,state,jobs):
        return dict(action='global_estop',generation=state['admission_generation'],boot=self.a.boot_reader(),scope=state['scope'],
            policy=state.get('recovery_policy'),accepted_boot=state.get('verified_boot'),owners={r:{k:o.get(k) for k in ('job_id','request_hash','lease_id','target','boot_id','admission_generation')} for r,o in state['owners'].items()},
            jobs={k:{f:v.get(f) for f in ('request_id','request_hash','lease_id','resources','admission_generation')} for k,v in jobs.items()})
    def status(self):
        s=self.state()
        return dict(current_generation=s['admission_generation']['id'],runner_ownership='FREE' if not s['owners'] else 'OWNED',
            owners=s['owners'],revoked_leases=len({r['lease_id'] for r in s['estop_revocations'].values() if r.get('lease_id')}),
            physical_gpu_state='NOT_ASSERTED',capability='global_estop_v1',
            worker_capacity_ready=self.runner._worker_generation==s['admission_generation']['id'])
    def check(self,actor):
        with self.locked():
            state=self.state();self.a._check_physical_identities(state);jobs=self.affected(state)
            expiry=int(time.monotonic())+60;op=f'{expiry:08x}'+uuid.uuid4().hex[:24];expires=self.clock()+60
            value=dict(operation=op,actor=actor,expires=expires,deadline=expiry,boot=self.a.boot_reader(),binding=digest(self.binding(state,jobs)),action='global_estop')
            data=encode(value);token=base64.urlsafe_b64encode(data).decode()+'.'+hmac.new(self.key,data,hashlib.sha256).hexdigest()
            return dict(eligible=True,action='estop',operation=op,expires=expires,token=token,
                effect='Relinquish GPU0 and GPU1 runner ownership. GPU work is NOT stopped and may overlap new work. Fail affected requests without replay.',
                generation=state['admission_generation']['id'],owners=state['owners'],jobs=self.binding(state,jobs)['jobs'],
                physical_gpu_state='NOT_ASSERTED')
    def decode(self,token,actor):
        try:
            raw,signature=token.split('.');data=base64.b64decode(raw,altchars=b'-_',validate=True)
            if not hmac.compare_digest(signature,hmac.new(self.key,data,hashlib.sha256).hexdigest()):raise ValueError()
            value=strict_json(data)
            if value['actor']!=actor or value['action']!='global_estop' or value['expires']<=self.clock():raise ValueError()
            if not re.fullmatch('[a-f0-9]{32}',value['operation']):raise ValueError()
            if value['boot']!=self.a.boot_reader() or value['deadline']!=int(value['operation'][:8],16) or time.monotonic()>=value['deadline']:raise ValueError()
            return value
        except Exception:raise RecoveryRequired('invalid_or_expired_estop_ticket') from None
    def execute(self,token,actor,confirmed):
        if confirmed is not True:raise RecoveryRequired('explicit_estop_confirmation_required')
        ticket=self.decode(token,actor);op=ticket['operation'];group=None
        try:
            with self.locked() as deadline:
                state=self.state()
                if op in state['estop_intents']:raise RecoveryRequired('duplicate_estop_verify_only')
                jobs=self.affected(state)
                if ticket['binding']!=digest(self.binding(state,jobs)):raise RecoveryRequired('stale_estop_binding')
                self.a._check_physical_identities(state)
                # No flock of GPU resource files, process/GPU/backend observation or cleanup.
                g=uuid.uuid4().hex
                state['estop_intents'][op]=dict(actor=actor,binding=ticket['binding'],new_generation=g,created_at=self.clock())
                self.store.write('ownership.json',state);self.fault('intent_persisted')
                directory=self.store.root/'generations'/g;directory.mkdir(parents=True,mode=0o700)
                locks={};paths={}
                for r in ('gpu0','gpu1'):
                    p=directory/(r+'.lock');fd=os.open(p,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
                    try:os.fsync(fd);locks[r]=lock_identity(os.fstat(fd))
                    finally:os.close(fd)
                    paths[r]=str(p.resolve());self.fault(r+'_prepared')
                for p in (directory,directory.parent,self.store.root):
                    fd=os.open(p,os.O_RDONLY|os.O_DIRECTORY)
                    try:os.fsync(fd)
                    finally:os.close(fd)
                self.fault('namespace_durable');self.decode(token,actor)
                owners=copy.deepcopy(state['owners']);old=copy.deepcopy(state['admission_generation'])
                record=dict(operation=op,result='ownership_relinquished',actor=actor,confirmed=True,
                    binding=ticket['binding'],signed_approval=ticket,approval_signature=token.split('.')[1],
                    old_verified_boot=state.get('verified_boot'),old_generation=old,new_generation=g,owners=owners,
                    revoked_leases=len({o['lease_id'] for o in owners.values()}),revoked_requests=len(jobs),
                    physical_gpu_state='NOT_ASSERTED',backend_operations=0,replayed=False,committed_at=self.clock(),
                    maintenance_before=self.store._read_raw('recovery-control.json'))
                for job_id,job in jobs.items():
                    lease=next((o['lease_id'] for o in owners.values() if o['job_id']==job_id),job.get('lease_id'))
                    state['estop_revocations'][job_id]=dict(operation=op,job_before=copy.deepcopy(job),
                        job_sha256=digest(job),lease_id=lease,generation=old['id'])
                state['estop_attempts'][op]=record
                state.update(owners={},ready=True,verified_boot=self.a.boot_reader(),admission_generation=dict(id=g,sequence=old['sequence']+1,
                    paths=paths,lock_identity=locks,origin=old['origin']),lock_identity=locks)
                c=state.get('practical_control')
                if c:
                    if c.get('active'):c['attempts'][c['active']]['superseded_by_estop']=op
                    c.update(admission='open',active=None)
                self.fault('before_commit')
                if time.monotonic()>=deadline:raise RecoveryRequired('estop_commit_deadline')
                self.store._write_context.generation_switch=True
                try:self.store.write('ownership.json',state)
                finally:self.store._write_context.generation_switch=False
                self.fault('authority_committed')
                group=self.runner._rotate_worker_capacity_locked(g)
            self.runner.wake.set()
            return self.verify(op)
        except OSError:
            # A rename may have taken effect. Never repeat this operation.
            return dict(operation=op,result='uncertain',verify_required=True)
        finally:
            if group:self.runner._retire_executor(group)
    def verify(self,op):
        if not isinstance(op,str) or not re.fullmatch('[a-f0-9]{32}',op):raise ValueError('invalid_operation')
        with self.locked():
            state=self.state()
            # Resolve directory-flush uncertainty BEFORE accepting this authority.
            for p in (self.store.path('ownership.json'),self.store.root):
                fd=os.open(p,os.O_RDONLY)
                try:os.fsync(fd)
                finally:os.close(fd)
            if op in state['estop_attempts']:
                record=state['estop_attempts'][op]
                # Full signed audit/history stays in the native journal, not in
                # a potentially enormous status response or browser storage.
                value={k:record[k] for k in ('operation','result','new_generation','revoked_leases','revoked_requests','physical_gpu_state','backend_operations','replayed','committed_at')}
                return dict(value,old_generation={'id':record['old_generation']['id']},verified=True)
            if op in state['estop_intents']:return dict(operation=op,result='not_committed',verified=True,verified_no_effect=True)
            if time.monotonic()>=int(op[:8],16):
                return dict(operation=op,result='not_committed',verified=True,verified_no_effect=True)
            return dict(operation=op,result='unknown_operation',verify_required=True)
