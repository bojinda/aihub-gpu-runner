"""Real target observation and Linux pidfd stop. No docker kill/restart or inference."""
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import time

from .core import RecoveryRequired, encode, guest_boot_id
from .target_barrier import TargetBarrier, birth, submission_ticket
from .comfy_plugin import PINNED


def helper_error(process,fallback):
    try:
        code=json.loads(process.stdout).get('error')
        if isinstance(code,str) and __import__('re').fullmatch('[a-z0-9_]{1,100}',code):return code
    except (ValueError,AttributeError):pass
    return fallback


def kernel_anchor(pid):
    p=Path('/proc',str(pid));started=birth(pid)
    if not started:raise RecoveryRequired('original_process_not_alive')
    exe=os.stat(p/'exe')
    return dict(pid=pid,birth=started,boot=guest_boot_id(),pidns=os.stat(p/'ns/pid').st_ino,
                cgroup=(p/'cgroup').read_text(),exe=[exe.st_dev,exe.st_ino])


class AnchoredTarget:
    """Observe the original kernel execution, independently of Docker Health/API."""
    def __init__(self,anchor,container):self.anchor,self.expected=anchor,container
    def container(self,deadline):
        a=self.anchor
        if a['boot']!=guest_boot_id():raise RecoveryRequired('execution_anchor_boot_changed')
        if birth(a['pid'])!=a['birth']:return dict(self.expected,pid=0,running=False)
        # /proc/<root container>/exe and namespace links may require privilege.
        # Boot + kernel PID birth identifies the original execution without
        # Docker Health or daemon availability. The fixed privileged helper
        # independently verifies the full anchor again before sending a signal.
        if Path('/proc',str(a['pid']),'cgroup').read_text()!=a['cgroup']:
            raise RecoveryRequired('execution_kernel_cgroup_changed')
        return dict(self.expected,pid=a['pid'],running=True)


class ProcessTarget:
    """A fixed, private barrier identifies a real local ComfyUI process.

    Used for CPU qualification. No caller-supplied PID or command is accepted.
    The production deployment selects DockerTarget instead.
    """
    def __init__(self,barrier):self.barrier=barrier
    def container(self,deadline):
        g=self.barrier.read()['generation']
        alive=birth(g['pid'])==g['birth'] and g['pidns']==os.stat('/proc/self/ns/pid').st_ino
        return dict(id=g['id'],started_at=g['birth'],contract_sha256=hashlib.sha256(encode(g['sources'])).hexdigest(),
                    pid=g['pid'] if alive else 0,running=alive)
    def processes(self,container,deadline):
        return {str(container['pid']):birth(container['pid'])} if container['running'] else {}
    def server_matches(self,g,processes):return processes.get(str(g['pid']))==g['birth']


class DockerTarget:
    def __init__(self,observer):self.observer=observer
    def container(self,deadline):return self.observer.container('wallpaper',deadline)
    def processes(self,container,deadline):return self.observer.processes(container,deadline)
    def server_matches(self,g,processes):
        for pid,started in processes.items():
            if started!=g['birth']:continue
            try:
                status=Path('/proc',str(pid),'status').read_text()
                ids=next(x.split()[1:] for x in status.splitlines() if x.startswith('NSpid:'))
                if int(ids[-1])==g['pid'] and os.stat('/proc/'+str(pid)+'/ns/pid').st_ino==g['pidns']:
                    return True
            except (OSError,StopIteration):continue
        return False


class PidfdActuator:
    """Effect-time pinning, durable per-signal intents, bounded TERM/KILL.

    Reentry observes an already-issued signal; it never repeats that signal.
    Kernel handles prevent PID reuse/container restart from redirecting it.
    """
    def __init__(self,barrier,probe,grace=2):self.barrier,self.probe,self.grace=barrier,probe,grace
    def stop(self,expected,original_birth,lease,operation,stage,deadline):
        if stage not in ('graceful','force'):raise RecoveryRequired('invalid_stop_stage')
        b=self.barrier
        with b.lock.held():
            s=b.read()
            self.scope(s,lease,operation)
            current=self.probe.container(deadline)
            if any(current.get(k)!=expected.get(k) for k in ('id','started_at','contract_sha256')):
                raise RecoveryRequired('stop_generation_changed')
            if not current['running']:return
            if current['pid']!=expected['pid'] or birth(expected['pid'])!=original_birth:
                raise RecoveryRequired('stop_process_birth_changed')
            fd=os.pidfd_open(expected['pid'],0)
            try:
                # Close both inspect->open and open->signal substitution gaps.
                if birth(expected['pid'])!=original_birth or self.probe.container(deadline)!=current:
                    raise RecoveryRequired('stop_generation_changed_after_pidfd_open')
                rec=s['signals'].setdefault(operation,dict(pid=expected['pid'],birth=original_birth,stage=stage))
                if any(rec[k]!=v for k,v in [('pid',expected['pid']),('birth',original_birth),('stage',stage)]):
                    raise RecoveryRequired('stop_operation_binding_changed')
                if 'term_intent' not in rec:
                    rec['term_intent']=time.time();b.store.write('target.json',s)
                    signal.pidfd_send_signal(fd,signal.SIGTERM)
            except BaseException:
                os.close(fd);raise
        try:
            poll=select.poll();poll.register(fd,select.POLLIN)
            if poll.poll(max(0,int(min(self.grace,max(0,deadline-time.monotonic()))*1000))):return
            if stage=='graceful':raise RecoveryRequired('exact_execution_did_not_exit_after_term')
            with b.lock.held():
                s=b.read();self.scope(s,lease,operation)
                rec=s['signals'][operation]
                if 'kill_intent' not in rec:
                    rec['kill_intent']=time.time();b.store.write('target.json',s)
                    signal.pidfd_send_signal(fd,signal.SIGKILL)
            if not poll.poll(max(0,int((deadline-time.monotonic())*1000))):
                raise RecoveryRequired('exact_execution_exit_unverified')
        finally:os.close(fd)

    @staticmethod
    def scope(s,lease,operation):
        if s.get('queue_intents'):raise RecoveryRequired('target_queue_write_unresolved')
        if s['mode']!='closed' or s['operation']!=operation or s['inflight']:
            raise RecoveryRequired('exact_target_barrier_required')
        active=list(s['running'].values())+s['pending']
        if any((s['prompts'].get(p,{}).get('identity') or {}).get('lease_id')!=lease for p in active):
            raise RecoveryRequired('unrelated_active_prompt')


class OwnedExecutionAdapter:
    def __init__(self,runner,settings):
        self.runner=runner;self.a=runner.admission;self.store=runner.store;self.settings=settings
        self.barrier=TargetBarrier(settings['barrier_dir'])
        self.key=(self.barrier.root/'submission.key').read_bytes()
        self.probe=(ProcessTarget(self.barrier) if settings.get('target_kind')=='process' else
                    DockerTarget(runner.recovery.observer))
        self.actuator=PidfdActuator(self.barrier,self.probe,settings.get('grace_seconds',2))

    def job(self):
        s=self.a.snapshot();owners=s['owners']
        if owners:return self.store.job(next(iter(owners.values()))['job_id'])
        c=s.get('practical_control',{})
        op=c.get('active') or next(reversed(c.get('attempts',{})),None)
        if op:return c['attempts'][op]['job_before']
        raise RecoveryRequired('no_affected_execution')

    def capture(self,job,container):
        if self.settings.get('stop_helper'):
            p=subprocess.run(['/usr/bin/sudo','-n',self.settings['stop_helper']],input=encode({'capture_job':job['request_id']}),
                             capture_output=True,timeout=8,check=False)
            if p.returncode:raise RecoveryRequired(helper_error(p,'privileged_execution_capture_failed'))
            value=json.loads(p.stdout)
            if set(value)!={'barrier_generation','execution_init_birth','execution_anchor'}:
                raise RecoveryRequired('invalid_privileged_capture')
            return value
        s=self.barrier.read();g=s['generation'];p=self.probe.processes(container,time.monotonic()+5)
        if g['sources']!=PINNED or g['boot']!=guest_boot_id() or not self.probe.server_matches(g,p):
            raise RecoveryRequired('target_adapter_generation_unverified')
        if s['mode']!='open':raise RecoveryRequired('target_recovery_maintenance')
        anchor=kernel_anchor(container['pid'])
        if self.settings.get('target_kind')!='process':
            status=Path('/proc',str(container['pid']),'status').read_text()
            ns=next(x.split()[1:] for x in status.splitlines() if x.startswith('NSpid:'))
            if ns[-1]!='1' or container['id'] not in anchor['cgroup']:
                raise RecoveryRequired('owned_container_init_namespace_unverified')
        return dict(barrier_generation=g,execution_init_birth=anchor['birth'],execution_anchor=anchor)

    @staticmethod
    def initial_processes(container):
        started=birth(container['pid'])
        if not started:raise RecoveryRequired('container_init_exit_before_capture')
        # Killing a namespace init terminates the entire namespace. Health
        # subprocesses and transient descendants are not immutable identities.
        return {str(container['pid']):started}

    def owned_ticket(self,job):
        g=job['barrier_generation']
        identity={k:job[k] for k in ('lease_id','request_hash','boot_id')}
        identity['job_id']=job['request_id']
        return submission_ticket(self.key,g,identity)

    def readiness(self):
        state=self.a.snapshot()
        if state['owners']:return {'ready':False,'reason':'resources_owned'}
        return {'ready':None,'reason':'admission_open_normal_workload_readiness_checks_apply'}

    def observe(self,*,timeout=5):
        job=self.job();life=job['wallpaper_lifecycle']
        if not job.get('execution_anchor'):raise RecoveryRequired('execution_anchor_missing_requires_new_managed_run')
        probe=AnchoredTarget(job['execution_anchor'],life['running_container'])
        c=probe.container(time.monotonic()+timeout)
        s=self.barrier.read();g=s['generation'];expected=job.get('barrier_generation')
        if s.get('queue_intents'):raise RecoveryRequired('target_queue_write_unresolved')
        worker=self.runner.handoffs.view(job['request_id']) if self.runner.handoffs else None
        gone=all(birth(pid)!=start for pid,start in life.get('original_pids',{}).items())
        identity_verified=(not c['running'] or birth(c['pid'])==job.get('execution_init_birth'))
        capable=bool(expected and expected==g and g['sources']==PINNED and g['boot']==guest_boot_id())
        owned=[p for p,r in s['prompts'].items() if (r.get('identity') or {}).get('lease_id')==job['lease_id']]
        running=list(s['running'].values()) if c['running'] else []
        pending=s['pending'] if c['running'] else []
        return dict(boot=guest_boot_id(),observed_at=time.time(),container=c,worker_absent=not(worker and worker['active']),
            worker=worker,process_identity_verified=identity_verified,original_execution_gone=gone and not c['running'],
            queue_available=capable,queue_running=running,queue_pending=pending,owned_prompt=owned[0] if len(owned)==1 else None,
            write_epoch=s['epoch'],write_barrier_available=capable,
            stop_capability='reviewed_generation_bound' if capable else 'unavailable',
            exclusive_execution_lease=job['lease_id'] if capable and all(p in owned for p in running+pending) and not s['inflight'] else None,
            readiness=self.readiness())

    def fence(self,operation,epoch,container,*,timeout=5):
        job=self.job()
        probe=AnchoredTarget(job['execution_anchor'],job['wallpaper_lifecycle']['running_container'])
        if probe.container(time.monotonic()+timeout)!=container:raise RecoveryRequired('target_generation_changed')
        old=self.barrier.read().get('operation')
        if old and old!=operation:
            state=self.a.snapshot()
            previous=state.get('practical_control',{}).get('attempts',{}).get(old) or state.get('normal_stop_intents',{}).get(old)
            if not previous or previous.get('lease')!=job['lease_id']:raise RecoveryRequired('unrelated_target_fence')
        self.barrier.fence(operation,epoch,job['barrier_generation'])

    def fence_verified(self,operation):
        s=self.barrier.read();return s['mode']=='closed' and s['operation']==operation
    def unfence(self,operation):self.barrier.restore(operation)
    def access_restored(self,operation):
        s=self.barrier.read();return s['mode']=='open' and s['operation'] is None

    def stop_exact(self,container,lease,stage,operation,*,deadline):
        job=self.job()
        probe=AnchoredTarget(job['execution_anchor'],job['wallpaper_lifecycle']['running_container'])
        helper=self.settings.get('stop_helper')
        if helper:
            p=subprocess.run(['/usr/bin/sudo','-n',helper],input=encode({'operation':operation}),
                             capture_output=True,timeout=max(.1,deadline-time.monotonic()),check=False)
            if p.returncode:raise RecoveryRequired(helper_error(p,'privileged_exact_stop_failed'))
        else:
            PidfdActuator(self.barrier,probe,self.settings.get('grace_seconds',2)).stop(
                container,job['execution_init_birth'],lease,operation,stage,deadline)
        while time.monotonic()<deadline:
            current=probe.container(deadline)
            if any(current[k]!=container[k] for k in ('id','started_at','contract_sha256')):
                raise RecoveryRequired('target_replaced_during_stop')
            if not current['running'] and all(birth(pid)!=start for pid,start in job['wallpaper_lifecycle']['original_pids'].items()):return
            time.sleep(.05)
        raise RecoveryRequired('original_execution_exit_unverified')

    def original_container(self,job,deadline):
        return AnchoredTarget(job['execution_anchor'],job['wallpaper_lifecycle']['running_container']).container(deadline)

    def shutdown_completed(self,job,deadline):
        """Normal lifecycle cleanup uses the same actual barrier and actuator."""
        lifecycle=self.runner.recovery.lifecycle
        lifecycle.verify_result(job['request_id'],job['wallpaper_lifecycle'])
        op=hashlib.sha256(('normal-completion:'+job['lease_id']).encode()).hexdigest()[:32]
        current=self.original_container(job,deadline)
        with self.a.mutex.held():
            state=self.a.snapshot()
            policy=state['recovery_policy']
            stage='force' if policy['policy']['allow_force'] else 'graceful'
            state.setdefault('normal_stop_intents',{}).setdefault(op,dict(id=op,lease=job['lease_id'],
                job_id=job['request_id'],mode='normal_completed',stage=stage,policy_id=policy['policy_id'],
                container=job['wallpaper_lifecycle']['running_container'],
                result_sha256=job['wallpaper_lifecycle']['result_sha256'],artifact_sha256=job['wallpaper_lifecycle']['artifact_sha256'],status='prepared'))
            intent=state['normal_stop_intents'][op]
            if intent['stage']!=stage or intent.get('policy_id')!=policy['policy_id']:
                raise RecoveryRequired('normal_completion_policy_changed')
            self.store.write('ownership.json',state)
        if current['running']:
            s=self.barrier.read()
            self.fence(op,s['epoch'],current,timeout=5)
            try:PidfdActuator.scope(self.barrier.read(),job['lease_id'],op)
            except Exception:
                self.unfence(op);raise
            lifecycle.save(job['request_id'],phase='stopping',term_intent=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat())
            self.stop_exact(current,job['lease_id'],stage,op,deadline=deadline)
        stopped=self.original_container(job,deadline)
        if stopped['running']:raise RecoveryRequired('original_execution_exit_unverified')
        proof=dict(cleanup_verified=True,ownership_release_ready=True,original_execution_gone=True,
                   backend_stopped=True,stopped_container=stopped,container=stopped,readiness=self.readiness(),
                   cleanup_basis='owned_kernel_execution_exit',operation=op)
        lifecycle.save(job['request_id'],phase='verified',cleanup_proof=proof)
        return proof

    def restore_normal_receipts(self):
        state=self.a.snapshot()
        for op,record in state.get('normal_stop_intents',{}).items():
            if record.get('status')=='restored':continue
            receipt=state.get('wallpaper_lifecycle_receipts',{}).get(record['lease'])
            if not receipt:continue
            job=self.store.job(record['job_id'])
            if (job['state'] not in ('success','failed') or any(o['lease_id']==record['lease'] for o in state['owners'].values()) or
                    receipt['result_sha256']!=record['result_sha256'] or receipt['artifact_sha256']!=record['artifact_sha256']):
                raise RecoveryRequired('normal_completion_receipt_unverified')
            if self.original_container(job,time.monotonic()+5)['running']:raise RecoveryRequired('normal_execution_exit_unverified')
            self.unfence(op)
            if not self.access_restored(op):raise RecoveryRequired('normal_access_restoration_unverified')
            with self.a.mutex.held():
                current=self.a.snapshot();current['normal_stop_intents'][op]['status']='restored'
                self.store.write('ownership.json',current)
