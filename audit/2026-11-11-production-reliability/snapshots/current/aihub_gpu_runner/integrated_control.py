"""Runner-integrated control: real adapter, native settlement and worker handoff."""
import copy
import hashlib
import threading
import time
from contextlib import ExitStack

from .core import NativeLock, RecoveryRequired, encode, guest_boot_id, timestamp
from .native_settlement import bound, hold, no_start, completed_output_known
from .practical_recovery import PracticalRecovery, Refused, digest, OWNER_KEYS


def provision(admission):
    with NativeLock(admission.store.path('practical-recovery.lock')).held(), admission.mutex.held():
        s=admission.snapshot()
        if 'practical_control' not in s:
            s['practical_control']=dict(admission='open',active=None,attempts={})
            admission.store.write('ownership.json',s)


class IntegratedRecovery(PracticalRecovery):
    never_started=staticmethod(no_start)
    def __init__(self,runner,adapter,key,settings):
        self.runner=runner;self.settings=settings;self.local=threading.local()
        super().__init__(runner.admission,adapter,key)

    def affected_job(self):
        s=self.a.snapshot()
        if s['owners']:return self.store.job(next(iter(s['owners'].values()))['job_id'])
        c=s['practical_control'];op=c['active'] or next(reversed(c['attempts']),None)
        if op:return c['attempts'][op]['job_before']
        raise Refused('no_retained_wallpaper_job')

    def snapshot(self):
        s=self.a.snapshot()
        if not s.get('practical_control'):raise Refused('recovery_control_not_provisioned')
        if not s['owners']:raise Refused('no_retained_wallpaper_job')
        job=self.store.job(next(iter(s['owners'].values()))['job_id'])
        owner,identity=bound(self.a,s,job)
        matching=[(k,v) for k,v in s.get('automatic_recovery',{}).items()
                  if all(v.get('identity',{}).get(f)==owner[f] for f in OWNER_KEYS)]
        if len(matching)>1:raise Refused('ambiguous_incident_identity')
        if not matching:
            ident={k:v for k,v in identity.items() if k!='container_id'}
            incident=digest(ident)
            matching=[(incident,dict(id=incident,identity=ident,kind='wallpaper',phase='observing',observed_boot=guest_boot_id()))]
        return s,job,matching[0]

    def observe(self,*,never_started=False):
        job=self.affected_job()
        worker=self.runner.handoffs.view(job['request_id']) if self.runner.handoffs else None
        if no_start(job):
            return dict(boot=guest_boot_id(),observed_at=time.time(),worker=worker,
                worker_absent=not(worker and worker['active']),readiness=self.readiness())
        if not self.adapter:raise Refused('destructive_adapter_not_enabled')
        try:
            obs=self.adapter.observe(timeout=5)
            if not 0<=time.time()-obs['observed_at']<=5:raise Refused('essential_observation_stale')
            obs['remote_completed']=None
            obs['remote_output_unavailable']=False
            if obs['container']['running'] and not self.artifacts(job)[0]:
                prompt=job.get('backend_detail',{}).get('prompt_id') or job['wallpaper_lifecycle'].get('prompt') or obs['owned_prompt']
                if prompt:
                    try:
                        result=self.runner.backends['wallpaper'].reconcile(prompt,time.monotonic()+3)
                        if result and result.artifact is not None and not result.error_code:
                            value=dict(value=result.value,media_type=result.media_type,artifact_available=True)
                            obs['remote_completed']=dict(prompt=prompt,result_sha256=digest(value),
                                artifact_sha256=hashlib.sha256(result.artifact).hexdigest())
                        elif result and result.error_code=='artifact_retrieval_or_validation_failed':
                            obs['remote_output_unavailable']=True
                    except Exception:pass  # Not an essential stop identity/exit observer.
            return obs
        except Refused:raise
        except Exception as exc:raise Refused(str(exc)) from None

    def readiness(self):
        return {'ready':None,'reason':'ordinary_admission_open_normal_readiness_checks_apply'}

    def hold_physical(self,stack,state):
        if getattr(self.local,'checking',False):
            job=self.affected_job()
            worker=self.runner.handoffs.view(job['request_id']) if self.runner.handoffs else None
            if worker and worker['active']:return
        self.local.physical=hold(self.a,state,stack)

    def plan(self,mode,state,job,incident,obs):
        if mode not in ('recover','emergency'):raise Refused('automatic_authority_is_runner_internal')
        if obs.get('recovery_capture') and mode!='recover':raise Refused('recovery_capture_requires_normal_recover')
        if mode=='emergency' and not self.settings['destructive']:raise Refused('emergency_not_enabled')
        if not no_start(job) and not self.settings['destructive']:raise Refused('destructive_adapter_not_enabled')
        worker=obs.get('worker')
        if worker and worker['active']:
            if (not self.runner.handoffs or job['wallpaper_lifecycle']['phase'] not in
                    ('submit_intent','artifact_saved','terminal')):
                raise Refused('original_worker_not_at_supported_handoff_phase')
        shadow=copy.deepcopy(job)
        if obs.get('owned_prompt') and not shadow.get('backend_detail'):
            shadow['backend_detail']={'prompt_id':obs['owned_prompt']}
        remote=obs.get('remote_completed')
        if remote:shadow['execution_deadline']=min(shadow.get('execution_deadline',time.time()),time.time()-1)
        plan=super().plan(mode,state,shadow,incident,obs)
        if remote:
            plan.update(case='completed_remote',terminal='success',loss=False,remote_completed=remote)
        plan['unsaved_completed_output']=bool(plan['terminal']!='success' and
            (completed_output_known(job) or obs.get('remote_output_unavailable')))
        if mode=='recover' and plan['unsaved_completed_output']:
            raise Refused('completed_output_preservation_required')
        if mode=='recover' and plan['terminal']=='success' and state['recovery_policy']['policy']['allow_force']:
            # Reuse the already-authorized completed-output cleanup policy.
            # Abandoning unfinished work still needs its separate loss approval.
            plan['stop_stage']='force'
        plan['handoff']=worker if worker and worker['active'] else None
        plan['target_fence']=bool(not no_start(job) and self.adapter and
                                 (plan['stop'] or self.adapter.barrier.read()['mode']=='closed'))
        return plan

    def binding(self,state,job,incident,obs,plan):
        inc=(incident[0],{'id':incident[0],'identity':incident[1]['identity']})
        base=super().binding(state,job,inc,obs,plan)
        recovery=obs.get('recovery_capture')
        stable={k:v for k,v in recovery.items() if k!='collected_at'} if recovery else None
        return digest([base,obs.get('remote_completed'),plan.get('handoff'),stable])

    def check(self,mode,actor):
        self.local.checking=True
        try:
            result=super().check(mode,actor)
            if (result.get('eligible') and mode=='recover' and result['plan']['stop'] and
                    result['plan']['stop_stage']=='force'):
                result['effect']+=' The exact owned execution may receive TERM then KILL under the existing completed-output cleanup policy.'
            if result.get('eligible') and result['plan'].get('unsaved_completed_output'):
                result['effect']+=' Known completed output is not safely saved and may also be lost. Accept that loss only for this emergency.'
            return result
        finally:self.local.checking=False

    def execute(self,token,actor,confirmed,accept_work_loss=False,*,expected_mode='recover',_automatic=False):
        ticket=self.decode(token,actor)
        if (ticket['mode']!=expected_mode or expected_mode not in ('recover','emergency') or confirmed is not True or
                (ticket['loss'] or expected_mode=='emergency') and accept_work_loss is not True):
            raise Refused('explicit_exact_confirmation_and_work_loss_acceptance_required')
        with self.mutex.held(),ExitStack() as stack:
            with self.jobs.held(),self.a.mutex.held():
                state,job,incident=self.snapshot();control=state['practical_control']
                if ticket['operation'] in control['attempts']:raise Refused('duplicate_request')
                obs=self.observe();plan=self.plan(expected_mode,state,job,incident,obs)
                if ticket['binding']!=self.binding(state,job,incident,obs,plan):raise Refused('observed_state_changed_check_again')
                if not plan['handoff']:self.hold_physical(stack,state)
                self.decode(token,actor)
                attempt=dict(id=ticket['operation'],mode=expected_mode,actor=actor,approval='confirmed_exact_action',
                    work_loss_accepted=accept_work_loss,lease=job['lease_id'],binding=ticket['binding'],
                    approved_job=copy.deepcopy(job),job_before=job,owners_before=copy.deepcopy(state['owners']),
                    admission_ready_before=(control['attempts'][plan['parent']].get('admission_ready_before',True)
                                            if plan['parent'] else state['ready']),
                    incident=incident,observation_before=obs,plan=plan,status='fenced',events=['admission_closed'],
                    deadline=time.monotonic()+60)
                if plan['parent']:control['attempts'][plan['parent']]['superseded_by']=attempt['id']
                state.setdefault('automatic_recovery',{}).setdefault(incident[0],dict(incident[1],created_at=timestamp()))
                control['attempts'][attempt['id']]=attempt;control.update(admission='closed',active=attempt['id'])
                # The native admission bit is understood by all existing local
                # launchers, including SDKs predating practical_control.
                state['ready']=False
                self.store.write('ownership.json',state)
            try:
                if plan['handoff']:
                    w=plan['handoff']
                    self.runner.handoffs.request(job['request_id'],job['lease_id'],w['id'],attempt['id'],timeout=15)
                with self.jobs.held(),self.a.mutex.held():
                    state=self.a.snapshot()
                    if plan['handoff']:self.hold_physical(stack,state)
                    if state['owners']!=attempt['owners_before'] or self.store.job(job['request_id'])!=job:
                        raise Refused('job_changed_during_worker_handoff')
                    current=self.observe()
                    compare=dict(current,worker_absent=obs['worker_absent'],worker=obs.get('worker'))
                    if self.binding(state,job,incident,compare,plan)!=ticket['binding']:
                        record=state['practical_control']['attempts'][attempt['id']]
                        record['handoff_drift']=[k for k in set(obs)|set(compare) if k not in ('observed_at','readiness') and obs.get(k)!=compare.get(k)]
                        self.store.write('ownership.json',state)
                        raise Refused('execution_changed_during_handoff')
                if plan['target_fence']:
                    self.adapter.fence(attempt['id'],current['write_epoch'],current['container'],timeout=5)
                # Reuse existing lifecycle result preservation before any stop.
                if plan.get('remote_completed'):
                    promised=plan['remote_completed']
                    result=self.runner.backends['wallpaper'].reconcile(promised['prompt'],time.monotonic()+5)
                    value=dict(value=result.value,media_type=result.media_type,artifact_available=result.artifact is not None) if result else {}
                    if (not result or result.error_code or result.artifact is None or
                            digest(value)!=promised['result_sha256'] or hashlib.sha256(result.artifact).hexdigest()!=promised['artifact_sha256']):
                        raise Refused('completed_output_changed_or_unavailable')
                    self.runner.recovery.lifecycle.persist(job['request_id'],result)
                    self.runner.recovery.update_job(job['request_id'],completion_verified=True)
                with self.jobs.held(),self.a.mutex.held():
                    state=self.a.snapshot();record=state['practical_control']['attempts'][attempt['id']]
                    if plan.get('remote_completed'):
                        record['job_before']=self.store.job(job['request_id'])
                        record['plan']['artifacts']=self.artifacts(record['job_before'])[1]
                    attempt=record
                    current=self.observe()
                    if plan['stop']:
                        if time.monotonic()>=attempt['deadline']:raise Refused('operation_deadline_expired')
                        attempt['stop_intent']=plan['stop_stage'];attempt['events'].append('stop_intent')
                        self.store.write('ownership.json',state)
                        self.adapter.stop_exact(current['container'],job['lease_id'],plan['stop_stage'],attempt['id'],
                                                deadline=min(attempt['deadline'],time.monotonic()+30))
                    self.finish(state,attempt)
            except Exception as exc:
                with self.jobs.held(),self.a.mutex.held():
                    s=self.a.snapshot();a=s['practical_control']['attempts'][attempt['id']]
                    a.update(status='operator_required',error=str(exc));self.store.write('ownership.json',s)
                    if not a.get('stop_intent') and attempt['id'] not in s.get('native_settlements',{}):
                        try:self.abort_safe_locked(s,a)
                        except Exception:pass
                return dict(result='blocked' if self.a.snapshot()['practical_control']['admission']=='open' else 'operator_required',
                            operation=attempt['id'],failed_checks=[str(exc)])
            return self.receipt(attempt['id'])

    def finish(self,state,attempt):
        if not attempt.get('verified_commit'):
            proof=self.gone_proof(attempt)
            self.a.settle_owned_recovery(attempt,proof,self.local.physical)
            self.verify_commit(attempt['id'])
            state=self.a.snapshot();attempt=state['practical_control']['attempts'][attempt['id']]
            attempt.update(verified_commit=True,verified_proof=proof)
            self.store.write('ownership.json',state)
        else:self.verify_commit(attempt['id'])
        if attempt['plan'].get('target_fence',attempt['plan']['stop']):
            self.adapter.unfence(attempt['id'])
            if not self.adapter.access_restored(attempt['id']):raise Refused('target_access_restoration_unverified')
        state=self.a.snapshot();c=state['practical_control'];a=c['attempts'][attempt['id']]
        if c['active']!=attempt['id']:raise Refused('operation_superseded')
        a.update(status='succeeded',verified_at=time.time(),readiness=self.readiness())
        a['events'].append('normal_admission_restored');c.update(active=None,admission='open')
        state['ready']=bool(a.get('admission_ready_before',True) and state.get('verified_boot')==self.a.boot_reader())
        self.store.write('ownership.json',state)
        # Capacity/backend telemetry is intentionally outside the recovery fence.

    def abort_safe_locked(self,state,attempt):
        if attempt.get('stop_intent') or attempt['id'] in state.get('native_settlements',{}):raise Refused('uncertain_effect_cannot_abort')
        if attempt['plan'].get('parent'):raise Refused('parent_operation_requires_verification')
        if state.get('verified_boot')!=self.a.boot_reader() or attempt['job_before']['boot_id']!=self.a.boot_reader():
            raise Refused('abort_boot_changed')
        job=self.store.job(attempt['job_before']['request_id'])
        if any(job.get(k)!=attempt['job_before'].get(k) for k in ('request_id','request_hash','lease_id','boot_id')):
            raise Refused('abort_identity_changed')
        if state['owners']!=attempt['owners_before'] and not (not state['owners'] and job['state'] in ('success','failed')):
            raise Refused('abort_ownership_changed')
        if self.runner.handoffs:
            w=self.runner.handoffs.view(job['request_id'])
            if w and w['active'] and w['pending'] and attempt['plan']['handoff']:
                raise Refused('handoff_still_active')
        if self.adapter:
            self.adapter.unfence(attempt['id'])
            if not self.adapter.access_restored(attempt['id']):raise Refused('abort_access_unverified')
        attempt.update(status='aborted',verified_at=time.time())
        state['ready']=attempt.get('admission_ready_before',True)
        state['practical_control'].update(admission='open',active=None);self.store.write('ownership.json',state)

    def verify_interrupted(self,operation):
        with self.locked(),ExitStack() as stack:
            state=self.a.snapshot();attempt=state['practical_control']['attempts'][operation]
            if attempt['status']=='succeeded':return self.receipt(operation)
            if attempt['status']=='aborted':return dict(result='aborted_verified',operation=operation)
            if state['practical_control']['active']!=operation:raise Refused('operation_superseded')
            self.hold_physical(stack,state)
            if not attempt.get('stop_intent') and operation not in state.get('native_settlements',{}):
                self.abort_safe_locked(state,attempt);return dict(result='aborted_verified',operation=operation)
            self.finish(state,attempt)
            return self.receipt(operation)

    def status(self):
        s=self.a.snapshot();c=s['practical_control'];jobs=[self.store.job(o['job_id']) for o in {o['lease_id']:o for o in s['owners'].values()}.values()]
        active=c['attempts'].get(c['active'])
        last=active or (next(reversed(c['attempts'].values())) if c['attempts'] else None)
        capabilities=[]
        if self.settings['destructive'] and self.adapter:
            try:
                g=self.adapter.barrier.read()['generation']
                from .comfy_plugin import PINNED
                import os
                from pathlib import Path
                helper=self.settings.get('stop_helper')
                helper_ready=self.settings.get('qualification_process') is True
                if helper:
                    st=Path(helper).stat()
                    helper_ready=st.st_uid==0 and not st.st_mode & 0o022 and os.access(helper,os.X_OK)
                if g['sources']==PINNED and g['boot']==guest_boot_id() and hasattr(os,'pidfd_open') and helper_ready:
                    capabilities=['practical_recovery_v1']
            except Exception:pass
        return dict(available=True,integrated=True,readiness=None,admission_ready=s['ready'] and not self.a.maintenance_blocked(),
            capacity_reason=self.readiness()['reason'],owners=s['owners'],affected_jobs=[j['request_id'] for j in jobs],
            recovery_reasons=[j.get('lifecycle_operator_error') or j.get('recovery_error') or j.get('error_code') for j in jobs],
            operator_required=bool(active or any(j.get('recovery_status')=='operator_required' or j['state'] in ('reconciling','cleanup_pending') for j in jobs)),
            lifecycle_phases={j['request_id']:j.get('wallpaper_lifecycle',{}).get('phase') for j in jobs},
            maintenance=c['admission'],observed_at=time.time(),capabilities=capabilities,
            attempt={k:last.get(k) for k in ('id','status','error')} if last else None,
            automatic_no_start=self.settings['automatic_no_start'])
