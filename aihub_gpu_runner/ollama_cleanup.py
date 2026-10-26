"""Physical idle proof under an existing Ollama lease. No inference or control effects."""
import copy
import csv
import io
import math
from pathlib import Path
import time

from .backends import CleanupPending, physical_gpu_uuid, remaining
from .core import RecoveryRequired, timestamp
from .recovery import RecoveryObserver, binding, digest, valid_boot


class _MemoryPending(Exception):
    """Only verified, owned unload memory may consume the bounded polling budget."""


def process_birth(pid):
    return Path('/proc/' + str(pid) + '/stat').read_text().rsplit(')', 1)[1].split()[19]


def cleanup_guard(admission, target, owner, backend, *, observer=None, record=None):
    # Legacy scopes have no accepted physical calibration. Dual sessions already
    # verify both physical GPUs during their separate restoration contract.
    if target.kind != 'ollama' or target.mode_switch is not None:
        return None
    installed = admission.snapshot().get('recovery_policy')
    if installed is None or 'ollama' not in installed['policy']['services']:
        return None
    if observer is None:
        observer = RecoveryObserver(installed['policy'], {target.name: target}, {target.name: backend})
    return OllamaCleanupGuard(admission, target, owner, observer, record=record)


class OllamaCleanupGuard:
    def __init__(self, admission, target, owner, observer, *, record=None, birth_reader=process_birth):
        self.a, self.target, self.owner, self.observer = admission, target, dict(owner), observer
        self.resources = tuple(target.resources)
        installed = admission.snapshot()['recovery_policy']
        self.p, self.policy_id = copy.deepcopy(installed['policy']), installed['policy_id']
        if ('ollama' not in self.p['services'] or self.p['scope'] != admission.scope or
                digest(self.p) != self.policy_id or observer.p != self.p or
                not valid_boot(self.owner.get('boot_id')) or
                not all(isinstance(self.owner.get(k), str) and self.owner[k] for k in
                        ('job_id', 'request_hash', 'lease_id', 'target')) or
                not self.resources or not set(self.resources) <= set(self.p['gpu_uuids'])):
            raise RecoveryRequired('ollama_cleanup_calibration_or_scope_unverified')
        self.record = record or (lambda **fields: None)
        self.birth_reader = birth_reader
        self.context = None

    def bound(self):
        state = self.a.snapshot()
        self.a._check_physical_identities(state)
        installed = state.get('recovery_policy', {})
        if (state.get('ready') is not True or state.get('scope') != self.p['scope'] or installed.get('policy_id') != self.policy_id or
                self.observer.p != self.p or self.owner['target'] != self.target.name or
                installed.get('policy') != self.p or digest(self.p) != self.policy_id or
                self.a.boot_reader() != self.owner.get('boot_id') or
                state.get('verified_boot') != self.owner.get('boot_id') or
                any(binding(state['owners'].get(r, {})) != binding(self.owner) for r in self.resources) or
                {r for r, o in state['owners'].items() if o['lease_id'] == self.owner['lease_id']} != set(self.resources)):
            raise RecoveryRequired('ollama_cleanup_owner_boot_or_policy_changed')
        job = self.a.store.job(self.owner['job_id'])
        if (any(job.get(k) != self.owner[k] for k in ('request_hash', 'lease_id', 'boot_id')) or
                job.get('request_id') != self.owner['job_id'] or job.get('target') != self.target.name or
                tuple(job.get('resources', ())) != self.resources or job.get('state') != 'cleanup_pending'):
            raise RecoveryRequired('ollama_cleanup_job_binding_unverified')
        if job.get('host_stage'):
            requests = job.get('host_requests', [])
            if (type(job.get('host_exit_code')) is not int or job['host_exit_code'] < 0 or
                    any(q.get('state') != 'completed' for q in requests)):
                raise RecoveryRequired('ollama_cleanup_host_completion_unverified')
            return bool(requests)
        if job.get('completion_verified') is not True:
            raise RecoveryRequired('ollama_cleanup_completion_unverified')
        return True

    def identities(self, deadline):
        return {s: self.observer.container(s, deadline) for s in self.p['services']}

    def prepare(self, deadline):
        remaining(deadline, CleanupPending)
        owned_work = self.bound()
        identities = self.identities(deadline)
        wallpaper = identities['wallpaper']
        if 'stopped_idle_memory_mb' in self.p and (wallpaper['running'] or wallpaper['pid'] != 0):
            raise RecoveryRequired('ollama_cleanup_on_demand_wallpaper_must_be_stopped')
        ident = identities['ollama']
        if not ident['running'] or ident['pid'] <= 0:
            raise RecoveryRequired('ollama_cleanup_service_not_running')
        processes = self.observer.processes(ident, deadline)
        if str(ident['pid']) not in processes:
            raise RecoveryRequired('ollama_cleanup_main_process_unverified')
        if self.identities(deadline) != identities:
            raise RecoveryRequired('ollama_cleanup_generation_changed')
        self.context = {'binding': binding(self.owner), 'policy_id': self.policy_id,
                        'resources': list(self.resources), 'identities': identities,
                        'ollama_processes': processes, 'owned_work_completed': owned_work,
                        'captured_at': timestamp()}
        self.record(ollama_cleanup_context=self.context)
        remaining(deadline, CleanupPending)

    @staticmethod
    def number(value):
        try:
            result = float(value)
        except (ValueError, TypeError):
            raise RecoveryRequired('ollama_cleanup_invalid_gpu_number') from None
        if not math.isfinite(result) or result < 0:
            raise RecoveryRequired('ollama_cleanup_invalid_gpu_number')
        return result

    def sample(self, deadline):
        owned_work = self.bound()
        if not self.context or owned_work != self.context['owned_work_completed']:
            raise RecoveryRequired('ollama_cleanup_context_missing_or_changed')
        identities = self.identities(deadline)
        if identities != self.context['identities']:
            raise RecoveryRequired('ollama_cleanup_generation_changed')
        # This requires empty residency and valid backend readiness. Only targets
        # intersecting the owned resources are checked; GPU0 Music can keep working
        # while a routine GPU1 lease converges. Stopped wallpaper is not contacted.
        self.observer.queues(self.resources, deadline, skip_wallpaper=not identities['wallpaper']['running'])
        driver = self.observer.driver_health(deadline)
        known = {}
        for service, ident in identities.items():
            for pid, birth in self.observer.processes(ident, deadline).items():
                if pid in known:
                    raise RecoveryRequired('ollama_cleanup_ambiguous_process')
                if service == 'ollama' and self.context['ollama_processes'].get(pid) != birth:
                    raise RecoveryRequired('ollama_cleanup_new_or_reused_process')
                known[pid] = (service, birth)
        uuid_resources = {self.p['gpu_uuids'][r]: r for r in self.resources}
        usage = {s: {r: 0 for r in self.resources} for s in identities}
        seen, observed_processes = set(), {}
        raw = self.observer.command([self.p['nvidia_smi'],
            '--query-compute-apps=gpu_uuid,pid,used_gpu_memory', '--format=csv,noheader,nounits'], deadline)
        for row in csv.reader(io.StringIO(raw)):
            if not row:
                continue
            if len(row) != 3:
                raise RecoveryRequired('ollama_cleanup_invalid_compute_observation')
            uuid = physical_gpu_uuid(row[0].strip())
            pid = str(int(row[1].strip()))
            resource = uuid_resources.get(uuid)
            if resource is None:
                if pid in known and known[pid][0] == 'ollama':
                    raise RecoveryRequired('ollama_cleanup_unleased_gpu_activity')
                continue
            if pid not in known or (uuid, pid) in seen:
                raise RecoveryRequired('ollama_cleanup_unknown_or_duplicate_gpu_process')
            seen.add((uuid, pid))
            service, birth = known[pid]
            if service == 'ollama' and self.context['ollama_processes'].get(pid) != birth:
                raise RecoveryRequired('ollama_cleanup_new_or_reused_process')
            observed_processes[pid] = birth
            usage[service][resource] += self.number(row[2])
        gpus = {}
        for resource, uuid in ((r, self.p['gpu_uuids'][r]) for r in self.resources):
            raw = self.observer.command([self.p['nvidia_smi'], '--id=' + uuid,
                '--query-gpu=uuid,memory.used,utilization.gpu,temperature.gpu,ecc.errors.uncorrected.volatile.total,retired_pages.pending',
                '--format=csv,noheader,nounits'], deadline)
            rows = list(csv.reader(io.StringIO(raw)))
            if len(rows) != 1 or len(rows[0]) != 6 or physical_gpu_uuid(rows[0][0].strip()) != uuid:
                raise RecoveryRequired('ollama_cleanup_physical_gpu_unverified')
            _, memory, utilization, temperature, ecc, retired = [x.strip() for x in rows[0]]
            memory, utilization, temperature = map(self.number, (memory, utilization, temperature))
            if (temperature > self.p['max_temperature_c'] or ecc not in ('0', '[N/A]', 'N/A') or
                    retired not in ('No', 'no', '0', '[N/A]', 'N/A')):
                raise RecoveryRequired('ollama_cleanup_gpu_health_unverified')
            if utilization > self.p['idle_utilization_pct']:
                raise RecoveryRequired('ollama_cleanup_gpu_activity_unexplained')
            gpus[resource] = {'uuid': uuid, 'memory_mb': memory, 'utilization_pct': utilization,
                              'temperature_c': temperature}
        for pid, birth in observed_processes.items():
            if self.birth_reader(pid) != birth:
                raise RecoveryRequired('ollama_cleanup_process_birth_changed')
        if self.identities(deadline) != identities:
            raise RecoveryRequired('ollama_cleanup_generation_changed')
        self.bound()
        observation = {'observed_at': timestamp(), 'physical_gpus': gpus,
                       'service_memory_mb': usage, 'driver_health': driver}
        # Persist measured values before classifying a breach. Storage failure
        # propagates; it cannot be converted into cleanup success or a retry.
        self.record(ollama_cleanup_observation=observation)
        pending = False
        for service, values in usage.items():
            if any(v > self.p['services'][service]['idle_memory_mb'][r] for r, v in values.items()):
                if service != 'ollama' or not owned_work:
                    raise RecoveryRequired('service_exceeds_calibrated_idle_baseline_' + service)
                pending = True
        stopped = not identities['wallpaper']['running'] and identities['wallpaper']['pid'] == 0
        ceilings = self.p['stopped_idle_memory_mb'] if stopped and 'stopped_idle_memory_mb' in self.p else self.p['idle_memory_mb']
        if any(g['memory_mb'] > ceilings[r] for r, g in gpus.items()):
            if not owned_work:
                raise RecoveryRequired('ollama_cleanup_unowned_memory_residency')
            pending = True
        remaining(deadline, CleanupPending)
        if pending:
            raise _MemoryPending()
        return observation

    def verify(self, deadline):
        stable = 0
        while True:
            remaining(deadline, CleanupPending)
            try:
                observation = self.sample(deadline)
            except _MemoryPending:
                stable = 0
            else:
                stable += 1
                if stable >= self.p['samples']:
                    remaining(deadline, CleanupPending)
                    return {'policy_id': self.policy_id, 'binding': binding(self.owner),
                            'stable_samples': stable, 'observation': observation,
                            'policy': 'owned_unload_then_calibrated_physical_idle'}
            time.sleep(min(self.observer.poll_interval, remaining(deadline, CleanupPending)))
