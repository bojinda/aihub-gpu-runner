"""Native paired ownership settlement. No backend, network, process or GPU I/O."""
import hashlib
import os
from contextlib import ExitStack

from .core import NativeLock, RecoveryRequired, encode, lock_identity, timestamp

PAIR = ('gpu0', 'gpu1')
KEYS = ('job_id','request_hash','lease_id','target','boot_id')


def sha(value):
    return hashlib.sha256(encode(value)).hexdigest()


def no_start(job):
    life = job.get('wallpaper_lifecycle', {})
    original=life.get('original_container',{})
    return (job.get('target') == 'wallpaper' and life.get('phase') == 'prepared' and
            original.get('running') is False and original.get('pid')==0 and
            all(isinstance(original.get(k),str) and original[k] for k in ('id','started_at','contract_sha256')) and
            not set(life) - {'identity','original_container','phase','created_at','startup_deadline'} and
            not any(k in job for k in ('execution_container','execution_processes','execution_deadline','backend_detail')) and
            not job.get('completion_verified'))


def completed_output_known(job):
    life=job.get('wallpaper_lifecycle',{})
    return bool(life.get('result',{}).get('artifact_available') is True or
                job.get('completion_verified') is True and job.get('owned_execution_failed') is not True or
                any(job.get(k)=='wallpaper_artifact_unavailable_keep_backend'
                    for k in ('error_code','lifecycle_operator_error','recovery_error')))


def bound(a, state, job):
    a._check_physical_identities(state)
    owners = state['owners']
    if set(owners) != set(PAIR) or owners['gpu0'] != owners['gpu1']:
        raise RecoveryRequired('complete_identical_wallpaper_pair_required')
    owner = owners['gpu0']
    if (not owner.get('boot_id') or owner['boot_id'] != a.boot_reader() or
            state.get('verified_boot') != a.boot_reader() or owner['target'] != 'wallpaper' or
            job.get('request_id') != owner['job_id'] or job.get('resources') != list(PAIR) or
            any(job.get(k) != owner[k] for k in KEYS if k != 'job_id')):
        raise RecoveryRequired('same_boot_job_owner_binding_required')
    identity = job.get('wallpaper_lifecycle', {}).get('identity', {})
    policy = state.get('recovery_policy', {})
    if (any(identity.get(k) != owner[k] for k in KEYS) or identity.get('resources') != list(PAIR) or
            identity.get('policy_id') != policy.get('policy_id') or
            identity.get('physical_gpu_uuids') != policy.get('policy', {}).get('gpu_uuids') or
            identity.get('container_id') != job['wallpaper_lifecycle'].get('original_container',{}).get('id') or
            identity.get('container_id') != policy.get('policy',{}).get('wallpaper',identity.get('container_id'))):
        raise RecoveryRequired('prepared_identity_or_policy_changed')
    return owner, identity


def hold(a, state, stack):
    locks = {}
    for r in PAIR:
        lock = stack.enter_context(NativeLock(a.lock_paths[r], create=False).held(timeout=0))
        locks[r] = lock
        if lock_identity(os.fstat(lock.fd)) != state['lock_identity'][r]:
            raise RecoveryRequired('physical_lock_replaced')
    return locks


def validate_locks(a, state, locks):
    a._check_physical_identities(state)
    if set(locks) != set(PAIR):
        raise RecoveryRequired('original_paired_locks_required')
    for r, lock in locks.items():
        if lock.fd is None or lock_identity(os.fstat(lock.fd)) != state['lock_identity'][r]:
            raise RecoveryRequired('original_paired_locks_required')


def finalize_locked(a, operation):
    state = a.snapshot()
    receipt = state.get('native_settlements', {}).get(operation)
    if receipt is None:
        raise RecoveryRequired('native_settlement_receipt_missing')
    owner = receipt['owner']
    if any(o.get('lease_id') == owner['lease_id'] for o in state['owners'].values()):
        raise RecoveryRequired('settlement_still_owns_resources')
    audits = [x for x in state.get('recoveries', []) if x.get('operation') == operation]
    if len(audits) != 1 or sha(audits[0]) != receipt['audit_sha256']:
        raise RecoveryRequired('native_settlement_audit_changed')
    job = a.store.job(owner['job_id'])
    if job.get('native_settlement') == operation:
        if job.get('state') != receipt['terminal'] or job.get('resources_released') is not True:
            raise RecoveryRequired('native_terminal_record_changed')
        return receipt
    if sha(job) != receipt['job_sha256']:
        raise RecoveryRequired('native_settlement_original_job_changed')
    job.update(state=receipt['terminal'], error_code=receipt['error_code'], resources_released=True,
               supervision_active=False, native_settlement=operation, recovery_status='verified', updated_at=timestamp())
    if receipt['mode']!='automatic_no_start':
        job.update(practical_recovery_operation=operation,cleanup_verified=True)
    a.store.put_job(job)
    return receipt


def retire_prepared(a, job_id):
    """Own all locks; settle/finalize once. False means not this safe case.

    This operation deliberately does NOT install a maintenance fence or call a
    readiness observer. Ordinary admission resumes when paired release commits.
    """
    with NativeLock(a.store.path('jobs.lock')).held(), a.mutex.held(), ExitStack() as stack:
        state = a.snapshot()
        job = a.store.job(job_id)
        previous = [(op, r) for op, r in state.get('native_settlements', {}).items()
                    if r['owner']['job_id'] == job_id and r['mode'] == 'automatic_no_start']
        if previous:
            if len(previous) != 1:
                raise RecoveryRequired('multiple_settlement_receipts')
            finalize_locked(a, previous[0][0])
            return True
        if not no_start(job):
            return False
        if a.maintenance_blocked():
            return False  # Never override somebody else's operation fence.
        owner, identity = bound(a, state, job)
        try:
            locks = hold(a, state, stack)
        except TimeoutError:
            return False  # Original worker is still authoritative.
        if not no_start(a.store.job(job_id)):
            raise RecoveryRequired('prepared_job_changed')
        validate_locks(a, state, locks)
        operation = sha(['automatic_no_start_v1', identity])
        audit = dict(operation=operation, identity=identity, recovered_at=timestamp(), observed_boot=a.boot_reader(),
                     provenance='runner_no_start_policy_v1', mode='automatic_no_start', no_replay=True,
                     proof={'durable_prepared_no_start_or_submission_intent':True,
                            'original_physical_locks_acquired':True, 'backend_operations':0},
                     original_error=job.get('lifecycle_operator_error') or job.get('error_code'))
        receipt = dict(operation=operation, owner={k:owner[k] for k in KEYS}, mode='automatic_no_start',
                       terminal='failed', error_code='automatic_prestart_failed_no_replay',
                       job_sha256=sha(job), audit_sha256=sha(audit), released_at=audit['recovered_at'])
        state.setdefault('recoveries', []).append(audit)
        state.setdefault('native_settlements', {})[operation] = receipt
        state['owners'] = {}
        a.store.write('ownership.json', state)
        finalize_locked(a, operation)
        return True


def finalize_all(a):
    with NativeLock(a.store.path('jobs.lock')).held(), a.mutex.held():
        for op in a.snapshot().get('native_settlements', {}):
            finalize_locked(a, op)


def settle_owned_recovery(a, attempt, proof, locks):
    """Runner-internal authority, called with jobs/admission/original locks held.

    Never exposed as a generic endpoint; the native authority revalidates the
    actual job, complete pair, output, approval scope and positive exit evidence.
    """
    state=a.snapshot();op=attempt['id']
    validate_locks(a,state,locks)
    if op in state.get('native_settlements',{}):
        return finalize_locked(a,op)
    job=a.store.job(attempt['job_before']['request_id'])
    owner,identity=bound(a,state,job)
    if job!=attempt['job_before'] or state['owners']!=attempt['owners_before']:
        raise RecoveryRequired('native_recovery_binding_changed')
    mode=attempt['mode'];plan=attempt['plan']
    if mode not in ('recover','emergency') or attempt['approval']!='confirmed_exact_action':
        raise RecoveryRequired('native_explicit_recovery_approval_required')
    if mode=='emergency' and attempt.get('work_loss_accepted') is not True:
        raise RecoveryRequired('native_emergency_work_loss_not_accepted')
    life=job['wallpaper_lifecycle']
    if not no_start(job):
        old=life.get('running_container');current=proof.get('container',{})
        if (not old or proof.get('original_execution_gone') is not True or current.get('running') is not False or
                current.get('pid')!=0 or any(old.get(k)!=current.get(k) for k in ('id','started_at','contract_sha256'))):
            raise RecoveryRequired('native_exact_execution_exit_required')
    hashes={}
    for ext in ('.json','.bin'):
        p=a.store.path('results/'+job['request_id']+ext)
        hashes[ext]=hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
    if hashes!=plan['artifacts']:raise RecoveryRequired('native_artifact_changed')
    if plan['terminal']=='success':
        result=a.store.read('results/'+job['request_id']+'.json')
        if (not result or result!=life.get('result') or result.get('artifact_available') is not True or
                hashes['.bin']!=life.get('artifact_sha256') or hashes['.json']!=life.get('result_sha256') or
                not hashes['.bin'] or life.get('execution_error')):
            raise RecoveryRequired('native_completed_output_not_preserved')
    elif not no_start(job):
        import time
        if (mode=='recover' and completed_output_known(job) or attempt.get('work_loss_accepted') is not True or
                mode=='recover' and not (job.get('owned_execution_failed') is True or
                    isinstance(job.get('execution_deadline'),(int,float)) and job['execution_deadline']<=time.time())):
            raise RecoveryRequired('native_abandonment_not_authorized')
    audit=dict(operation=op,incident_id=attempt['incident'][0],identity=identity,mode=mode,
        provenance='confirmed_exact_action',actor=attempt['actor'],work_loss_accepted=attempt['work_loss_accepted'],
        before_binding=attempt['binding'],proof=proof,no_replay=True,recovered_at=timestamp(),observed_boot=a.boot_reader())
    receipt=dict(operation=op,owner={k:owner[k] for k in KEYS},mode=mode,terminal=plan['terminal'],
        error_code=None if plan['terminal']=='success' else 'emergency_abandoned_no_replay' if mode=='emergency' else 'operator_recovered_no_replay',
        job_sha256=sha(job),audit_sha256=sha(audit),released_at=audit['recovered_at'],
        job_id=job['request_id'],lease=owner['lease_id'],job_before_sha256=sha(job),artifacts=hashes,proof=proof)
    state.setdefault('recoveries',[]).append(audit)
    state.setdefault('native_settlements',{})[op]=receipt
    state.setdefault('practical_receipts',{})[op]=receipt
    state['owners']={}
    state['practical_control']['attempts'][op]['status']='committed'
    state['practical_control']['attempts'][op]['events'].append('native_paired_release_committed')
    a.store.write('ownership.json',state)
    return finalize_locked(a,op)
