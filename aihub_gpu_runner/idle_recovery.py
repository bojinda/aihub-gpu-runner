"""Explicit HA approval for one idle Ollama lease. No backend control or replay."""
import copy,time,stat
from .backends import Uncertain
from contextlib import ExitStack,contextmanager,nullcontext
from .core import NativeLock,RecoveryRequired,lock_identity,encode
from .maintenance import FILE,read_gate
from .recovery import ProcessDisappeared,process_birth
from .recovery_control import RecoveryControl,Blocked
from .recovery_checks import digest

CASE='ollama_completed_idle_manual_v1'
ERRORS=frozenset({'ollama_cleanup_new_or_reused_process','ollama_cleanup_probe_identity_changed',
    'ollama_cleanup_process_birth_changed','ollama_cleanup_unverified_process_disappearance'})
KEYS=('job_id','lease_id','request_hash','target','boot_id')

class IdleRecovery(RecoveryControl):
    def configuration_errors(self):
        errors=[]
        for name in ('practical-recovery.lock','recovery-control.lock','maintenance.lock','recovery.lock'):
            code=name.removesuffix('.lock').replace('-','_')
            try:
                value=self.store.path(name).lstat()
                if not stat.S_ISREG(value.st_mode):errors.append('idle_coordination_lock_invalid_'+code)
            except FileNotFoundError:errors.append('idle_coordination_lock_missing_'+code)
            except OSError:errors.append('idle_coordination_lock_unreadable_'+code)
        return errors

    @contextmanager
    def exclusive(self):
        with NativeLock(self.store.path('practical-recovery.lock'),create=False).held(0),super().exclusive():yield

    def candidate(self):
        s=self.a.snapshot();gate=read_gate(self.store,True)
        def need(ok,why):
            if not ok:raise Blocked([why])
        need(self.a.control_required,'mandatory_admission_gate_not_configured')
        need(s['ready'] is True,'admission_baseline_not_ready')
        need(s.get('practical_control',{}).get('admission')=='open' and s['practical_control'].get('active') is None,'other_recovery_active')
        need(set(s['owners'])=={'gpu1'},'exact_single_gpu1_owner_required')
        owner=s['owners']['gpu1'];j=self.store.job(owner['job_id'])
        need(owner['target']=='ollama' and j.get('target')=='ollama' and j.get('resources')==['gpu1'],'ollama_gpu1_binding_required')
        need(j.get('request_id')==owner['job_id'] and all(j.get(k)==owner[k] for k in KEYS if k!='job_id'),'job_owner_binding_changed')
        need(owner['boot_id']==self.a.boot_reader()==s.get('verified_boot'),'same_boot_required')
        need(not self.active(),'runner_supervision_or_queue_active')
        self.a._check_physical_identities(s)
        p=s.get('recovery_policy',{});ctx=j.get('ollama_cleanup_context',{})
        need(p.get('policy_id')==digest(p.get('policy')) and ctx.get('policy_id')==p['policy_id'],'accepted_policy_binding_changed')
        need(ctx.get('binding')=={k:owner[k] for k in KEYS} and ctx.get('resources')==['gpu1'],'cleanup_context_binding_changed')
        need(j.get('state') in ('reconciling','cleanup_pending') and j.get('error_code') in ERRORS,'unsupported_retained_failure_class')
        need(j.get('phase')=='execution_complete' and j.get('completion_verified') is True and ctx.get('owned_work_completed') is True,'backend_execution_completion_unverified')
        need(not j.get('host_stage') and not j.get('dual_gpu_session'),'unsupported_execution_scope')
        handoffs=getattr(self.runner,'handoffs',None)
        need(not handoffs or not (handoffs.view(j['request_id']) or {}).get('active'),'worker_handoff_active')
        need(not gate['permits'],'client_connections_not_drained')
        need(all(x['request_id']==j['request_id'] or x['state'] in ('success','failed','cancelled') for x in self.store.jobs()),'competing_nonterminal_job')
        need(not any(x.get('owner',{}).get('lease_id')==owner['lease_id'] for x in s.get('recovery_dispositions',{}).values()),'lease_already_retired')
        identity={k:owner[k] for k in KEYS}|{'resources':['gpu1'],'policy_id':p['policy_id'],'scope':s['scope'],
            'physical_gpu_uuids':p['policy']['gpu_uuids'],'physical_locks':s['lock_identity']}
        return s,j,identity

    def proof(self,state,job):
        """Use installed read-only observer; no cleanup, model unload or stop."""
        observer=self.observer;deadline=time.monotonic()+30
        if observer.p!=state['recovery_policy']['policy']:raise Blocked(['observer_policy_changed'])
        before={s:observer.container(s,deadline) for s in observer.p['services']}
        if before!=job['ollama_cleanup_context']['identities']:raise Blocked(['cleanup_service_generation_changed'])
        main=str(before['ollama']['pid']);births=job['ollama_cleanup_context']['ollama_processes']
        if process_birth(main)!=births.get(main):raise Blocked(['ollama_parent_birth_changed'])
        departed=set(births)-{main}
        rejected=job.get('ollama_cleanup_failure',{}).get('pid')
        if rejected and rejected!=main:departed.add(rejected)
        for pid in departed:
            try:process_birth(pid)
            except ProcessDisappeared as exc:
                import errno
                if exc.pid!=pid or exc.errno not in (errno.ENOENT,errno.ESRCH):raise Blocked(['positive_process_exit_unverified'])
                continue
            raise Blocked(['prior_or_rejected_process_still_present'])
        processes=observer.processes(before['ollama'],deadline)
        if processes!={main:births[main]}:raise Blocked(['ollama_child_process_present'])
        proof=observer.stopped_ready(('gpu0','gpu1'),deadline)
        if proof.get('stable_samples',0)<max(3,observer.p['samples']) or proof.get('idle_ready') is not True:raise Blocked(['three_accepted_idle_samples_required'])
        if proof['queues']!={'ollama':{'models':[]},'music':{'queue_running':[],'queue_pending':[]}}:raise Blocked(['backend_queues_or_residency_nonempty'])
        after={s:observer.container(s,deadline) for s in observer.p['services']}
        if after!=before or observer.processes(after['ollama'],deadline)!=processes:raise Blocked(['service_or_process_generation_changed'])
        return dict(proof,identities=before,ollama_processes=processes,departed_pids=sorted(departed))

    def snapshot(self,fenced=False,*,jobs_locked=False):
        with (nullcontext() if jobs_locked else self.runner.job_mutex.held()):
            s,j,identity=self.candidate();gate=read_gate(self.store,True)
            if gate['mode']!=('closed' if fenced else 'open'):raise Blocked(['maintenance_in_progress'])
            jobs=self.store.jobs();files=self.files()
            with ExitStack() as stack:
                for resource,path in sorted(self.a.lock_paths.items()):
                    lock=stack.enter_context(NativeLock(path,create=False).held(0))
                    import os
                    if lock_identity(os.fstat(lock.fd))!=s['lock_identity'][resource]:raise Blocked(['physical_lock_replaced'])
                proof=self.proof(s,j)
                if self.a.snapshot()!=s or self.store.jobs()!=jobs or self.files()!=files or self.active() or read_gate(self.store,True)!=gate:raise Blocked(['state_changed_during_idle_proof'])
            binding=dict(case=CASE,identity=identity,state=digest(s),jobs=digest(jobs),files=digest(files),epoch=gate['epoch'],
                generations=digest([proof['identities'],proof['ollama_processes']]))
            return s,j,proof,binding

    def check(self,principal):
        errors=self.configuration_errors()
        if errors:return dict(eligible=False,action='recover',classification='configuration_error',
            operator_required=True,failed_checks=errors)
        try:
            with self.exclusive():
                gate=read_gate(self.store,True)
                active=gate['attempts'].get(gate['active'])
                if active and active.get('case')==CASE:
                    raise Blocked(['recovery_outcome_requires_verification' if active['authority_intent'] else 'interrupted_before_authority_verify_required'])
                s,j,proof,binding=self.snapshot()
                token,ticket=self.tickets.issue(binding,principal,'idle-recover')
                return dict(eligible=True,failed_checks=[],token='idle1.'+token,expires=ticket['expires'],operation=ticket['nonce'],
                    action='recover',case=CASE,classification='manual_idle_only',operator_required=True,
                    job_id=j['request_id'],identity=binding['identity'],incident=digest(binding['identity']),phase=j['phase'],
                    effect='Fail this completed job and release only its GPU1 lease after fresh full idle verification. No cleanup, restart or replay. Any uncommitted output remains failed.')
        except (Blocked,RecoveryRequired,TimeoutError) as exc:
            return dict(eligible=False,action='recover',classification='blocked',operator_required=True,
                failed_checks=exc.checks if isinstance(exc,Blocked) else [str(exc)[:160] or 'control_busy'])
        except (OSError,Uncertain) as exc:
            # Observation failure is a refusal, never evidence of physical idle.
            # Recheck prerequisites to name a file removed after the first read.
            errors=self.configuration_errors()
            return dict(eligible=False,action='recover',classification='configuration_error' if errors else 'observation_unavailable',
                operator_required=True,failed_checks=errors or ['idle_recovery_observation_unavailable'],
                observation_error=type(exc).__name__,observation_errno=getattr(exc,'errno',None))

    def recover(self,token,principal,confirmed):
        if confirmed is not True:raise Blocked(['explicit_confirmation_required'])
        errors=self.configuration_errors()
        if errors:raise Blocked(errors)
        ticket=self.tickets.verify(token.removeprefix('idle1.'),principal,'idle-recover');op=ticket['nonce']
        with self.exclusive():
            gate=read_gate(self.store,True)
            if op in gate['attempts']:raise Blocked(['duplicate_approval_consumed'])
            s,j,proof,binding=self.snapshot()
            if binding!=ticket['binding']:raise Blocked(['approved_state_changed'])
            with self.runner.job_mutex.held(),self.a.mutex.held():
                gate=read_gate(self.store,True)
                if gate['mode']!='open' or gate['permits'] or gate['epoch']!=binding['epoch'] or self.active() or self.a.snapshot()!=s or digest(self.store.jobs())!=binding['jobs']:raise Blocked(['concurrent_submission'])
                attempt=dict(id=op,case=CASE,phase='fenced',job_id=j['request_id'],principal=principal,binding=binding,
                    before_state=s,before_job=j,files=self.files(),other_jobs=digest([x for x in self.store.jobs() if x['request_id']!=j['request_id']]),
                    authority_intent=False,checks=[],events=[],created_at=self.clock())
                gate['attempts'][op]=attempt;gate.update(mode='closed',active=op,last_operation=op);self.store.write(FILE,gate)
            try:
                with self.runner.job_mutex.held():
                    s,j,proof,current=self.snapshot(True,jobs_locked=True)
                    if current!=binding:raise Blocked(['state_changed_after_fence'])
                    self.tickets.verify(token.removeprefix('idle1.'),principal,'idle-recover')
                    evidence=dict(binding['identity'],approval_ref=op,operator_approved_backend_recovery=True,backend_quiescent=True,
                        cleanup_verified=True,case=CASE,operator=principal,proof=proof,proof_sha256=digest(proof),no_backend_action=True,no_inference_replay=True)
                    attempt=self.save_attempt(op,phase='authority_intent',authority_intent=True,evidence=evidence)
                    self.a.recover(j['request_id'],evidence)  # Exactly once; no retry path.
                return self.finish(attempt)
            except Exception as exc:
                checks=exc.checks if isinstance(exc,Blocked) else [str(exc)[:160] or 'execution_uncertain']
                self.save_attempt(op,phase='operator_required',checks=checks)
                return dict(result='operator_required',operation=op,failed_checks=checks,maintenance='closed')

    def verify_commit(self,attempt,*,terminal=True):
        state=self.a.snapshot();old=attempt['before_state'];job=self.store.job(attempt['job_id']);original=attempt['before_job']
        audits=state.get('recoveries',[]);previous=old.get('recoveries',[])
        if len(audits)!=len(previous)+1 or audits[:-1]!=previous:raise Blocked(['recovery_audit_not_exactly_once'])
        audit=audits[-1]
        if not audit.get('recovered_at') or audit!=dict(attempt['evidence'],recovered_at=audit.get('recovered_at')):raise Blocked(['audit_binding_changed'])
        identity=attempt['binding']['identity']
        record=dict(owner={k:identity[k] for k in KEYS},resources=['gpu1'],approval_ref=attempt['id'],job_sha256=digest(original),
            audit_sha256=digest(audit),error_code='operator_recovered_no_automatic_resubmission')
        expected=copy.deepcopy(old);expected['owners']={};expected['recoveries']=audits
        expected.setdefault('recovery_dispositions',{})[identity['lease_id']]=record
        if state!=expected:raise Blocked(['release_or_history_changed'])
        retired=dict(original,state='failed',error_code=record['error_code'],updated_at=job.get('updated_at'))
        if job!=retired and (terminal or job!=original):raise Blocked(['terminal_job_unverified'])
        if digest([x for x in self.store.jobs() if x['request_id']!=attempt['job_id']])!=attempt['other_jobs'] or self.files()!=attempt['files']:raise Blocked(['unrelated_history_changed'])
        self.a._check_physical_identities(state)
        if self.a.boot_reader()!=identity['boot_id'] or self.active():raise Blocked(['boot_or_supervision_changed'])
        return state

    def finish(self,attempt):
        self.verify_commit(attempt,terminal=False)
        self.a.finalize_recoveries(attempt['job_id'])
        with self.runner.job_mutex.held(),self.a.mutex.held():
            self.verify_commit(attempt)
            gate=read_gate(self.store,True)
            if gate['active']!=attempt['id'] or gate['permits']:raise Blocked(['maintenance_owner_changed'])
            gate['attempts'][attempt['id']].update(phase='succeeded',checks=[],verified_at=self.clock())
            gate.update(mode='open',active=None,epoch=gate['epoch']+1);self.store.write(FILE,gate)
        return dict(result='released_readiness_pending',operation=attempt['id'],verified_release=True,job_id=attempt['job_id'],
            resources=['gpu1'],terminal='failed',error_code='operator_recovered_no_automatic_resubmission',replayed=False)

    def reconcile(self,operation):
        with self.exclusive():
            gate=read_gate(self.store,True);attempt=gate['attempts'].get(operation)
            if not attempt or attempt.get('case')!=CASE:raise Blocked(['unknown_idle_operation'])
            if attempt['phase']=='succeeded':return dict(result='released_readiness_pending',operation=operation,verified_release=True,historical=True)
            if attempt['phase']=='aborted':return dict(result='aborted_verified',operation=operation,ownership_released=False,historical=True)
            if gate['active']!=operation:raise Blocked(['maintenance_owner_changed'])
            if not attempt['authority_intent']:
                # No authority call was possible without a durable intent. A
                # fresh proof may reopen only an entirely unchanged incident.
                self.verify_unchanged(attempt)
                _,_,_,binding=self.snapshot(True)
                if binding!=attempt['binding']:raise Blocked(['abort_original_state_changed'])
                with self.runner.job_mutex.held(),self.a.mutex.held():
                    self.verify_unchanged(attempt);gate=read_gate(self.store,True)
                    if gate['active']!=operation or gate['permits']:raise Blocked(['maintenance_owner_changed'])
                    gate['attempts'][operation].update(phase='aborted',checks=[],verified_at=self.clock())
                    gate.update(mode='open',active=None,epoch=gate['epoch']+1);self.store.write(FILE,gate)
                return dict(result='aborted_verified',operation=operation,ownership_released=False)
            return self.finish(attempt)  # Verifies native receipt; never calls recover.
