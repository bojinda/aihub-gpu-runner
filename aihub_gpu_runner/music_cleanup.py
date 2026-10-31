"""Music GPU0 handoff proof under its original lease and cleanup deadline.

No scheduler, recovery action, unload transport, or independent locking authority.
Only the existing Comfy cleanup may send /free, once after durable intent.
"""
import copy
import csv
import io
import time

from .backends import CleanupPending, physical_gpu_uuid, remaining
from .core import FINAL, RecoveryRequired, timestamp
from .recovery import binding, digest, process_birth, valid_boot


def capture_execution(observer, deadline):
    identity = observer.container('music', deadline)
    if not identity['running'] or identity['pid'] <= 0:
        raise RecoveryRequired('music_release_service_not_running')
    processes = observer.processes(identity, deadline)
    if observer.container('music', deadline) != identity:
        raise RecoveryRequired('music_release_generation_changed')
    return {'identity': identity, 'processes': processes}


def music_guard(admission, target, owner, observer, record):
    if target.name != 'music' or target.kind != 'comfy':
        return None
    installed = admission.snapshot().get('recovery_policy')
    # Legacy standalone scopes have no accepted stopped-Wallpaper calibration.
    if not installed or 'stopped_idle_memory_mb' not in installed['policy']:
        return None
    return MusicReleaseGuard(admission, target, owner, observer, record)


class MusicReleaseGuard:
    def __init__(self, admission, target, owner, observer, record):
        self.a, self.target, self.owner = admission, target, copy.deepcopy(owner)
        self.observer, self.record = observer, record
        installed = admission.snapshot()['recovery_policy']
        self.p, self.policy_id = copy.deepcopy(installed['policy']), installed['policy_id']
        if (tuple(target.resources) != ('gpu0',) or target.lifecycle != 'always_on' or
                observer is None or observer.p != self.p or digest(self.p) != self.policy_id or
                self.p['scope'] != admission.scope or not valid_boot(owner.get('boot_id')) or
                not {'music', 'wallpaper'} <= set(self.p['services'])):
            raise RecoveryRequired('music_release_scope_or_policy_invalid')
        self.context = None
        self.deadline = None
        self.proof = None
        self.sequence = 0
        self.final_observation = None

    def bound(self, *, terminal=False):
        state = self.a.snapshot()
        self.a._check_physical_identities(state)
        job = self.a.store.job(self.owner['job_id'])
        if (state.get('ready') is not True or state.get('scope') != self.p['scope'] or
                state.get('verified_boot') != self.owner['boot_id'] or
                self.a.boot_reader() != self.owner['boot_id'] or
                state.get('recovery_policy', {}).get('policy_id') != self.policy_id or
                state.get('recovery_policy', {}).get('policy') != self.p or self.observer.p != self.p or
                binding(state['owners'].get('gpu0', {})) != binding(self.owner) or
                {r for r,o in state['owners'].items() if o.get('lease_id') == self.owner['lease_id']} != {'gpu0'}):
            raise RecoveryRequired('music_release_owner_boot_or_policy_changed')
        if (job.get('request_id') != self.owner['job_id'] or job.get('target') != 'music' or
                any(job.get(k) != self.owner[k] for k in ('request_hash', 'lease_id', 'boot_id')) or
                job.get('resources') != ['gpu0'] or job.get('host_stage') or
                job.get('completion_verified') is not True or
                job.get('state') not in (FINAL if terminal else ('cleanup_pending',))):
            raise RecoveryRequired('music_release_job_binding_changed')
        if self.context is not None and (
                job.get('music_cleanup_context') != self.context or
                job.get('backend_detail', {}).get('prompt_id') != self.context['prompt_id'] or
                job.get('music_execution') != self.context['execution']):
            raise RecoveryRequired('music_release_context_changed')
        return job

    def prepare(self, deadline):
        remaining(deadline, CleanupPending)
        job = self.bound()
        if any(k in job for k in ('music_cleanup_context', 'music_free_intent', 'music_free_ack')):
            raise RecoveryRequired('music_cleanup_intent_exists_no_replay')
        original = job.get('music_execution')
        if not original or capture_execution(self.observer, deadline) != original:
            raise RecoveryRequired('music_release_execution_changed')
        identities = {s:self.observer.container(s, deadline) for s in self.p['services']}
        if identities['wallpaper']['running'] or identities['wallpaper']['pid'] != 0:
            raise RecoveryRequired('music_release_wallpaper_not_stopped')
        # Bind other services only where they currently have GPU0 contexts.
        # A GPU1-only Ollama child may start/exit during this capture without
        # causing Music to wait for its process birth or idleness.
        raw = self.observer.command([self.p['nvidia_smi'],
            '--query-compute-apps=gpu_uuid,pid', '--format=csv,noheader,nounits'], deadline)
        gpu0_pids = set()
        for row in csv.reader(io.StringIO(raw)):
            if not row:
                continue
            if len(row) != 2 or not row[1].strip().isdecimal() or int(row[1]) <= 0:
                raise RecoveryRequired('music_release_invalid_compute_capture')
            if physical_gpu_uuid(row[0].strip()) == self.p['gpu_uuids']['gpu0']:
                pid = str(int(row[1]))
                if pid in gpu0_pids:
                    raise RecoveryRequired('music_release_invalid_compute_capture')
                gpu0_pids.add(pid)
        processes = {'music':self.observer.processes(identities['music'], deadline)}
        for service, ident in identities.items():
            if service == 'music':
                continue
            processes[service] = {}
            if ident['running']:
                raw = self.observer.command([self.p['docker'], 'top', ident['id'], '-eo', 'pid'], deadline)
                lines = raw.strip().splitlines()
                if not lines or lines[0].strip() != 'PID':
                    raise RecoveryRequired('service_process_identity_unverified')
                for line in lines[1:]:
                    pid = str(int(line.strip()))
                    if pid in gpu0_pids:
                        processes[service][pid] = process_birth(pid)
        if (identities['music'] != original['identity'] or processes['music'] != original['processes']):
            raise RecoveryRequired('music_release_execution_changed')
        prompt = job.get('backend_detail', {}).get('prompt_id')
        if not isinstance(prompt, str) or not prompt:
            raise RecoveryRequired('music_release_prompt_missing')
        self.deadline = deadline
        context = {'schema':1, 'owner':binding(self.owner), 'resources':['gpu0'],
                   'policy_id':self.policy_id, 'physical_gpu_uuids':{'gpu0':self.p['gpu_uuids']['gpu0']},
                   'prompt_id':prompt, 'execution':original, 'identities':identities,
                   'processes':processes, 'captured_at':timestamp(),
                   'cleanup_deadline_monotonic':deadline, 'cleanup_timeout_seconds':self.target.cleanup_timeout}
        self.record(music_cleanup_context=context)
        self.context = context
        self.bound()
        remaining(deadline, CleanupPending)

    def identities(self, deadline):
        for service in ('music', 'wallpaper'):
            if self.observer.container(service, deadline) != self.context['identities'][service]:
                raise RecoveryRequired('music_release_generation_changed')
        if self.observer.processes(self.context['identities']['music'], deadline) != self.context['processes']['music']:
            raise RecoveryRequired('music_release_process_birth_changed')

    def intent(self, deadline):
        self.bound()
        self.identities(deadline)
        if self.a.store.job(self.owner['job_id']).get('music_free_intent'):
            raise RecoveryRequired('music_cleanup_intent_exists_no_replay')
        remaining(deadline, CleanupPending)
        self.record(music_free_intent={'at':timestamp(), 'context_sha256':digest(self.context)})
        # A failed/ambiguous write never permits the effect.
        self.bound()
        remaining(deadline, CleanupPending)

    def acknowledged(self, deadline):
        self.bound()
        job = self.a.store.job(self.owner['job_id'])
        if not job.get('music_free_intent') or job.get('music_free_ack'):
            raise RecoveryRequired('music_cleanup_ack_without_unique_intent')
        self.record(music_free_ack={'at':timestamp(), 'context_sha256':digest(self.context)})
        remaining(deadline, CleanupPending)

    def observe(self, deadline, *, terminal=False, persist=True):
        if deadline != self.deadline:
            raise RecoveryRequired('music_release_deadline_changed')
        remaining(deadline, CleanupPending)
        job = self.bound(terminal=terminal)
        for key in ('music_free_intent', 'music_free_ack'):
            if job.get(key, {}).get('context_sha256') != digest(self.context):
                raise RecoveryRequired('music_release_free_ack_unverified')
        self.identities(deadline)
        # Only Music's queue is relevant to this GPU0 handoff. Ollama GPU1 is
        # neither polled nor unloaded. Same completed prompt may unwind; a new
        # prompt is a hard error even if memory is still settling.
        transport = self.observer.backends['music'].transport
        queue = transport.json('GET', '/queue', timeout=remaining(deadline))
        if any(not isinstance(queue.get(k), list) for k in ('queue_running','queue_pending')):
            raise RecoveryRequired('invalid_expected_queue_music')
        entries = queue['queue_running'] + queue['queue_pending']
        if any(not isinstance(e,list) or len(e)<2 or e[1]!=self.context['prompt_id'] for e in entries):
            raise RecoveryRequired('unexplained_backend_activity_music')
        stats = transport.json('GET', '/system_stats', timeout=remaining(deadline))
        if not isinstance(stats.get('system'),dict):
            raise RecoveryRequired('backend_readiness_unverified_music')
        sample = self.observer.gpu_sample(('gpu0',), deadline, stopped=True, music_context=self.context)
        self.identities(deadline)
        self.bound(terminal=terminal)
        remaining(deadline, CleanupPending)
        self.sequence += 1
        observation = {'at':timestamp(), 'sequence':self.sequence, 'queue_empty':not entries,
                       'sample':sample, 'context_sha256':digest(self.context)}
        if persist:
            self.record(music_cleanup_observation=observation)
        remaining(deadline, CleanupPending)
        return observation

    def verify(self, deadline):
        stable = []
        while True:
            observation = self.observe(deadline)
            if observation['queue_empty'] and not observation['sample']['music_settling']:
                stable.append(observation)
            else:
                stable = []
            if len(stable) >= max(3, self.p['samples']):
                proof = {'schema':1, 'cleanup_verified':True, 'policy':'music_release_idle',
                         'context_sha256':digest(self.context), 'owner':binding(self.owner),
                         'resources':['gpu0'], 'policy_id':self.policy_id, 'stable_samples':len(stable),
                         'observations':stable, 'verified_at':timestamp()}
                self.record(music_release_idle=proof)
                self.proof = proof
                self.bound()
                remaining(deadline, CleanupPending)
                return copy.deepcopy(proof)
            time.sleep(min(self.observer.poll_interval, remaining(deadline, CleanupPending)))

    def recheck(self, deadline, *, terminal=False):
        job = self.bound(terminal=terminal)
        if self.proof is None or job.get('music_release_idle') != self.proof:
            raise RecoveryRequired('music_release_proof_missing_or_changed')
        observation = self.observe(deadline, terminal=terminal)
        if not observation['queue_empty'] or observation['sample']['music_settling']:
            raise RecoveryRequired('music_release_no_longer_idle')
        self.record(music_release_commit_check=observation)
        self.bound(terminal=terminal)
        self.identities(deadline)
        remaining(deadline, CleanupPending)

    def observe_release(self, deadline):
        # Last full observation outside the shared ownership mutex. No waiting
        # or retry: rebound after certification retains ownership immediately.
        job = self.bound(terminal=True)
        if job.get('music_release_idle') != self.proof or not job.get('music_release_commit_check'):
            raise RecoveryRequired('music_release_proof_missing_or_changed')
        observation = self.observe(deadline, terminal=True, persist=False)
        if not observation['queue_empty'] or observation['sample']['music_settling']:
            raise RecoveryRequired('music_release_no_longer_idle')
        self.final_observation = observation

    def release_binding(self, deadline):
        # Called under the EXISTING ownership mutex. Local identity/journal
        # reads only; no Docker/HTTP/GPU probes, sleeps or jobs-lock inversion.
        job = self.bound(terminal=True)
        if (job.get('music_release_idle') != self.proof or self.final_observation is None or
                self.final_observation['context_sha256'] != digest(self.context)):
            raise RecoveryRequired('music_release_proof_missing_or_changed')
        for pid, birth in self.context['processes']['music'].items():
            if process_birth(pid) != birth:
                raise RecoveryRequired('music_release_process_birth_changed')
        remaining(deadline, CleanupPending)
        return {'schema':1, 'owner':binding(self.owner), 'resources':['gpu0'],
                'policy_id':self.policy_id, 'proof_sha256':digest(self.proof),
                'final_observation':self.final_observation, 'released_at':timestamp()}
