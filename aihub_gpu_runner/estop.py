"""One administrator request; one durable logical ownership transition; no backend action."""
import contextlib,copy,hashlib,os,time,uuid
from .core import NativeLock,RecoveryRequired,encode,lock_identity

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
        state.update(schema=2,verified_boot=boot,authority_revision=0,estop_format=2,estop_revocations={},estop_last=None,
            admission_generation=dict(id=g,sequence=0,paths=origin['paths'],lock_identity=origin['lock_identity'],origin=origin))
        for owner in state['owners'].values():owner['admission_generation']=g
        # Original job files, lease IDs, scopes, policy and physical inodes stay intact.
        s.write('ownership.json',state)
        return g


def compact_state(state):
    """Pure, narrow conversion. No job scan, gate mutation, or ownership change."""
    if state.get('schema') != 2 or state.get('estop_format') == 2:
        raise RecoveryRequired('estop_compaction_not_applicable')
    result = {k:v for k,v in state.items() if k not in ('estop_attempts','estop_intents','estop_revocations')}
    revoked = {}
    for jid,r in state.get('estop_revocations', {}).items():
        job = r['job_before']
        if digest(job) != r['job_sha256'] or job['request_id'] != jid:
            raise RecoveryRequired('estop_legacy_revocation_invalid')
        revoked[jid] = {k:r[k] for k in ('operation','generation','lease_id','job_sha256')}
        revoked[jid]['request_hash'] = job['request_hash']
    attempts = state.get('estop_attempts', {}).values()
    last = max(attempts, key=lambda r:r['committed_at'], default=None)
    result.update(estop_format=2, estop_revocations=revoked,
        estop_last=(dict(timestamp=last['committed_at'],outcome='success',
                         released_leases=last['revoked_leases'],error=None) if last else None))
    return result


def compact_history(admission, backup, expected_sha256, *, confirmed_stopped=False):
    """Installation-only; all native writers stopped; exact verified rollback copy.

    Never invoked by HTTP or startup. Uncertain rename means inspect, never retry.
    Original gate/jobs/results, generations, leases and physical locks are untouched.
    """
    if confirmed_stopped is not True:
        raise RecoveryRequired('coordinated_shutdown_confirmation_required')
    store=admission.store
    with NativeLock(store.path('supervisor.lock'),create=False).held(0), \
            NativeLock(store.path('jobs.lock')).held(), admission.mutex.held(), contextlib.ExitStack() as stack:
        for path in store.root.glob('host-*.lock'):
            stack.enter_context(NativeLock(path,create=False).held(0))
        raw=store.path('ownership.json').read_bytes()
        from pathlib import Path
        backup=Path(backup)
        if hashlib.sha256(raw).hexdigest()!=expected_sha256 or backup.read_bytes()!=raw:
            raise RecoveryRequired('estop_compaction_backup_or_state_changed')
        for path in (backup,backup.parent):
            fd=os.open(path,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
        state=admission.snapshot();new=compact_state(state)
        for o in state['owners'].values():
            job=store._read_raw(store.job_name(o['job_id']),{})
            if (job.get('admission_generation')!=state['admission_generation']['id'] or
                    job.get('request_hash')!=o['request_hash'] or job.get('lease_id')!=o['lease_id']):
                raise RecoveryRequired('estop_compaction_current_owner_binding_changed')
        # Validate only explicitly revoked originals, not every historical job.
        for jid,r in new['estop_revocations'].items():
            if digest(store._read_raw(store.job_name(jid))) != r['job_sha256']:
                raise RecoveryRequired('estop_compaction_original_job_changed')
        store._write_context.estop_compaction=True
        try:store.write('ownership.json',new)
        finally:store._write_context.estop_compaction=False
        return dict(outcome='success',before_bytes=len(raw),after_bytes=store.path('ownership.json').stat().st_size,
                    preserved_leases=len({o['lease_id'] for o in state['owners'].values()}))


class Estop:
    def __init__(self,runner,*,clock=time.time,fault=None,lock_timeout=5):
        self.runner=runner;self.a=runner.admission;self.store=runner.store;self.clock=clock
        self.fault=fault or (lambda phase:None)  # disposable-test injection only
        self.lock_timeout=lock_timeout

    @contextlib.contextmanager
    def locked(self):
        # Bound contention only. There is no arbitrary whole-transaction deadline.
        end=time.monotonic()+self.lock_timeout
        jobs=NativeLock(self.store.path('jobs.lock'));admission=NativeLock(self.store.path('admission.lock'))
        if not jobs.acquire(max(0,end-time.monotonic())):raise RecoveryRequired('estop_jobs_busy')
        try:
            if not admission.acquire(max(0,end-time.monotonic())):raise RecoveryRequired('estop_admission_busy')
            try:yield
            finally:admission.close()
        finally:jobs.close()

    def status(self):
        s=self.a.snapshot()
        return dict(last=s.get('estop_last'),runner_ownership='OWNED' if s['owners'] else 'FREE',
                    current_generation=s['admission_generation']['id'],physical_gpu_state='UNVERIFIED')

    def execute(self,actor):
        # Authentication/active-administrator checks are server-side at ingress.
        group=None;commit_started=False
        def answer(outcome,error=None,count=0):
            return dict(timestamp=self.clock(),outcome=outcome,released_leases=None if outcome=='unknown' else count,error=error)
        try:
            with self.locked():
                state=self.a.snapshot()
                if state.get('schema')!=2 or state.get('estop_format')!=2:
                    raise RecoveryRequired('estop_compaction_required')
                boot=self.a.boot_reader()
                if not boot:raise RecoveryRequired('boot_unavailable')
                self.a._check_physical_identities(state)
                old=state['admission_generation'];g=uuid.uuid4().hex
                directory=self.store.root/'generations'/g;directory.mkdir(parents=True,mode=0o700)
                paths={};locks={}
                for r in ('gpu0','gpu1'):
                    p=directory/(r+'.lock');fd=os.open(p,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
                    try:os.fsync(fd);locks[r]=lock_identity(os.fstat(fd))
                    finally:os.close(fd)
                    paths[r]=str(p.resolve());self.fault(r+'_prepared')
                for p in (directory,directory.parent,self.store.root):
                    fd=os.open(p,os.O_RDONLY|os.O_DIRECTORY)
                    try:os.fsync(fd)
                    finally:os.close(fd)
                self.fault('namespace_durable')
                owners=state['owners'];count=len({o['lease_id'] for o in owners.values()})
                # Old nonterminal work is fenced by generation, with no per-job
                # scan/list. Only terminal-but-still-owned (or untagged/missing)
                # records need a compact exception to preserve revoked precedence.
                for o in {o['job_id']:o for o in owners.values()}.values():
                    try:job=self.store._read_raw(self.store.job_name(o['job_id']),{})
                    except (RecoveryRequired,OSError):job={}
                    if not isinstance(job,dict):job={}
                    if job.get('state') in ('success','failed','cancelled') or not job.get('admission_generation'):
                        state['estop_revocations'][o['job_id']]=dict(operation=g,generation=old['id'],
                            lease_id=o['lease_id'],request_hash=o['request_hash'])
                record=answer('success',count=count)
                state.update(owners={},ready=True,verified_boot=boot,estop_last=record,
                    admission_generation=dict(id=g,sequence=old['sequence']+1,paths=paths,lock_identity=locks,origin=old['origin']),lock_identity=locks)
                c=state.get('practical_control')
                if c:c.update(admission='open',active=None)
                self.fault('before_commit');commit_started=True
                self.store._write_context.generation_switch=True
                try:self.store.write('ownership.json',state)
                finally:self.store._write_context.generation_switch=False
                self.fault('authority_committed')
                group=self.runner._rotate_worker_capacity_locked(g)
            self.runner.wake.set()
            return record
        except RecoveryRequired as exc:
            safe={'estop_jobs_busy','estop_admission_busy','estop_compaction_required','boot_unavailable',
                  'physical_lock_identity_changed','original_physical_lock_changed','generation_lock_binding_changed'}
            code=str(exc) if str(exc) in safe else 'estop_authority_unavailable'
            return answer('unknown' if commit_started else 'failure',code)
        except Exception:
            return answer('unknown' if commit_started else 'failure',
                          'estop_commit_outcome_unknown' if commit_started else 'estop_prepare_failed')
        finally:
            if group:self.runner._retire_executor(group)

