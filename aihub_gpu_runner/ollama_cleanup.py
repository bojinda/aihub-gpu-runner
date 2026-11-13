"""Idle evidence or completed cooperative handoff under an existing Ollama lease."""
import copy
import csv
import functools
import io
import math
import time

from .backends import CleanupPending, Uncertain, physical_gpu_uuid, remaining
from .core import RecoveryRequired, timestamp
from .recovery import (ProcessDisappeared, RecoveryObserver, binding, digest,
                       process_birth, valid_boot)


class _MemoryPending(Exception):
    """Physical memory is not yet at the calibrated idle ceiling."""


class _ProcessPending(Exception):
    """Discard turnover/raced samples; process exit itself is never idle proof."""


class _UtilizationPending(Exception):
    """A complete safe observation is still above the accepted idle utilization."""


# Never persist arbitrary exception strings, paths, commands, response bodies or
# user data. These are fixed runner-owned reason codes, not a message regex.
_SAFE_REASONS = {
    'backend_deadline', 'ollama_cleanup_uncertain', 'ollama_unload_not_confirmed',
    'ollama_cleanup_utilization_pending',
    'ollama_cleanup_process_settling_pending', 'ollama_cleanup_process_turnover_limit',
    'ollama_cleanup_calibration_or_scope_unverified', 'ollama_cleanup_owner_boot_or_policy_changed',
    'ollama_cleanup_job_binding_unverified', 'ollama_cleanup_host_completion_unverified',
    'ollama_cleanup_completion_unverified', 'ollama_cleanup_on_demand_wallpaper_must_be_stopped',
    'ollama_cleanup_service_not_running', 'ollama_cleanup_main_process_unverified',
    'ollama_cleanup_generation_changed', 'ollama_cleanup_invalid_gpu_number',
    'ollama_cleanup_context_missing_or_changed', 'ollama_cleanup_ambiguous_process',
    'ollama_cleanup_new_or_reused_process', 'ollama_cleanup_invalid_compute_observation',
    'ollama_cleanup_probe_identity_changed',
    'ollama_cleanup_unleased_gpu_activity', 'ollama_cleanup_unknown_or_duplicate_gpu_process',
    'ollama_cleanup_physical_gpu_unverified', 'ollama_cleanup_gpu_health_unverified',
    'ollama_cleanup_gpu_activity_unexplained', 'ollama_cleanup_process_birth_changed',
    'ollama_cleanup_unowned_memory_residency', 'ollama_cleanup_unverified_process_disappearance',
    'ollama_cleanup_no_longer_idle',
    'container_process_identity_unverified', 'container_process_stat_invalid',
    'container_main_process_unverified', 'container_identity_or_supervision_changed',
    'container_name_identity_changed', 'container_inspect_invalid',
    'kernel_gpu_driver_fault_requires_operator', 'kernel_health_log_requires_operator',
    'kernel_health_boot_unverified', 'kernel_health_log_boot_unverified',
    'kernel_health_log_invalid', 'kernel_health_log_unavailable',
    'physical_lock_identity_changed', 'recovery_control_or_observation_uncertain',
    'recovery_command_timeout', 'recovery_command_failed', 'recovery_command_os_error',
}
_SAFE_REASONS.update(prefix + s for s in ('ollama', 'music', 'wallpaper') for prefix in
    ('service_exceeds_calibrated_idle_baseline_', 'unexplained_backend_activity_',
     'invalid_expected_queue_', 'backend_readiness_unverified_'))
_PHASES = {'factory', 'capture', 'binding', 'container_identity', 'service_processes', 'context_persistence',
           'owned_unload', 'queues', 'driver_health', 'compute_apps', 'gpu_health',
           'process_birth_recheck', 'observation_persistence', 'limits', 'deadline'}


def cleanup_failure(exc, phase, *, service=None, pid=None):
    """Small sanitized diagnostic, suitable for the caller's durable job update."""
    phase = phase if phase in _PHASES else 'factory'
    reason = str(exc) if isinstance(exc, (RecoveryRequired, CleanupPending, Uncertain)) else None
    if reason not in _SAFE_REASONS:
        if isinstance(exc, ProcessDisappeared):
            reason = 'ollama_cleanup_unverified_process_disappearance'
        elif isinstance(exc, OSError) and phase in ('context_persistence', 'observation_persistence'):
            reason = 'ollama_cleanup_persistence_error'
        elif isinstance(exc, PermissionError):
            reason = 'ollama_cleanup_permission_denied'
        elif isinstance(exc, OSError):
            reason = 'ollama_cleanup_os_error'
        elif isinstance(exc, RecoveryRequired):
            reason = 'ollama_cleanup_recovery_required'
        elif isinstance(exc, (ValueError, IndexError, KeyError, UnicodeError)):
            reason = 'ollama_cleanup_invalid_observation'
        else:
            reason = 'ollama_cleanup_observation_error'
    number = getattr(exc, 'errno', None)
    pid = getattr(exc, 'pid', pid)
    return {'schema': 1, 'reason': reason, 'phase': phase,
            'exception_type': type(exc).__name__ if type(exc).__name__ in {
                'ProcessDisappeared', 'RecoveryRequired', 'CleanupPending', 'Uncertain',
                'FileNotFoundError', 'ProcessLookupError', 'PermissionError', 'OSError',
                'ValueError', 'IndexError', 'KeyError', 'UnicodeDecodeError', 'TypeError'} else 'Exception',
            'errno': number if type(number) is int and 0 < number < 4096 else None,
            'service': service if service in ('ollama', 'music', 'wallpaper') else None,
            'pid': str(pid) if str(pid).isdecimal() and 0 < int(pid) < 2**31 else None,
            'observed_at': timestamp()}


def _report_errors(method):
    @functools.wraps(method)
    def call(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception as exc:
            self.report_failure(exc)
            raise
    return call


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


def submission_ready(admission, target, backend):
    """Read-only preflight before submit intent. Busy is safe to skip, not idle proof."""
    installed = admission.snapshot().get('recovery_policy')
    if (target.kind != 'ollama' or target.name != 'ollama' or target.mode_switch is not None or
            not installed or 'ollama' not in installed['policy'].get('services', {})):
        return True
    p = installed['policy']
    observer = RecoveryObserver(p, {target.name: target}, {target.name: backend})
    deadline = time.monotonic() + p['observation_timeout']
    try:
        observer.queues(target.resources, deadline)
        observer.gpu_sample(target.resources, deadline,
                            stopped='stopped_idle_memory_mb' in p, require_no_compute=True)
    except (RecoveryRequired, CleanupPending) as exc:
        if str(exc) in ('unexplained_backend_activity_ollama', 'unexplained_gpu_process_or_memory',
                        'gpu_not_at_calibrated_idle_gpu1') or str(exc).startswith(
                            'service_exceeds_calibrated_idle_baseline_'):
            return False
        raise
    return True


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
        self.release_proof = None
        self.process_births = {}
        self.phase, self.service, self.pid = 'factory', None, None

    def mark_phase(self, phase, service=None, pid=None):
        self.phase, self.service, self.pid = phase, service, pid

    def failure(self, exc):
        detail = cleanup_failure(exc, self.phase, service=self.service, pid=self.pid)
        detail['boundary'] = getattr(self, 'boundary', 'capture')
        command = getattr(exc, 'observation_failure', None)
        if isinstance(command, dict):
            # Constructed only by RecoveryObserver.command from fixed categories
            # and bounded numbers; no argv, output, environment or exception text.
            detail['command'] = command
        return detail

    def report_failure(self, exc):
        detail = self.failure(exc)
        fields = {'ollama_cleanup_failure': detail}
        try:
            self.record(**fields)
        except Exception:
            # A diagnostic write cannot override the original failure or make
            # an uncertain journal safe. The outer caller also tries its update.
            pass
        return detail

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
                tuple(job.get('resources', ())) != self.resources or
                job.get('state') not in (('success', 'failed') if getattr(self, 'terminal', False) else ('cleanup_pending',))):
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

    def acknowledge_expiration(self, proof):
        if proof.get('expiration_acknowledged') is not True or not self.bound():
            raise RecoveryRequired('ollama_cleanup_completion_unverified')
        self.context['expiration_acknowledged'] = True
        self.record(ollama_cleanup_context=self.context)

    def completed_release(self):
        return {'completion_verified': True, 'cleanup_verified': False,
                'expiration_acknowledged': True, 'release_outcome': 'completed_without_idle'}

    def release_evidence(self, observation):
        if observation.get('release_outcome') == 'completed_without_idle':
            proof = observation
        else:
            proof = {'completion_verified': True, 'cleanup_verified': True,
                     'physical_ollama_idle': observation}
        self.release_proof = proof
        return proof

    def can_relinquish(self):
        return bool(self.resources == ('gpu1',) and self.target.name == 'ollama' and
                    self.context and self.context.get('expiration_acknowledged'))

    @_report_errors
    def prepare(self, deadline):
        self.mark_phase('capture')
        remaining(deadline, CleanupPending)
        owned_work = self.bound()
        identities = self.identities(deadline)
        wallpaper = identities['wallpaper']
        if 'stopped_idle_memory_mb' in self.p and (wallpaper['running'] or wallpaper['pid'] != 0):
            raise RecoveryRequired('ollama_cleanup_on_demand_wallpaper_must_be_stopped')
        ident = identities['ollama']
        if not ident['running'] or ident['pid'] <= 0:
            raise RecoveryRequired('ollama_cleanup_service_not_running')
        self.mark_phase('service_processes', 'ollama')
        processes = self.observer.processes(ident, deadline)
        if str(ident['pid']) not in processes:
            raise RecoveryRequired('ollama_cleanup_main_process_unverified')
        if self.identities(deadline) != identities:
            raise RecoveryRequired('ollama_cleanup_generation_changed')
        self.process_births = dict(processes)
        self.context = {'binding': binding(self.owner), 'policy_id': self.policy_id,
                        'resources': list(self.resources), 'identities': identities,
                        'ollama_processes': processes, 'owned_work_completed': owned_work,
                        'captured_at': timestamp()}
        self.mark_phase('context_persistence')
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

    def missing_child(self, pid):
        # A raced census invalidates this sample. It proves no historical origin
        # and never supplies idle evidence. The container main must survive.
        if pid == str(self.context['identities']['ollama']['pid']):
            return False
        self.sample_discarded = True
        return True

    def sample(self, deadline):
        self.sample_discarded = False
        self.mark_phase('binding')
        owned_work = self.bound()
        if not self.context or owned_work != self.context['owned_work_completed']:
            raise RecoveryRequired('ollama_cleanup_context_missing_or_changed')
        self.mark_phase('container_identity')
        identities = self.identities(deadline)
        if identities != self.context['identities']:
            raise RecoveryRequired('ollama_cleanup_generation_changed')
        # This requires empty residency and valid backend readiness. Only targets
        # intersecting the owned resources are checked; GPU0 Music can keep working
        # while a routine GPU1 lease converges. Stopped wallpaper is not contacted.
        self.mark_phase('driver_health')
        driver = self.observer.driver_health(deadline)
        # Births detect contradictory/reused lifetimes, not request ownership.
        # One plain census per service; no executable, ancestry or helper roles.
        known = {}
        for service, ident in identities.items():
            self.mark_phase('service_processes', service)
            processes = self.observer.processes(ident, deadline,
                **({'on_missing': self.missing_child} if service == 'ollama' else {'pids_only': True}))
            if service == 'ollama':
                for pid, birth in processes.items():
                    if pid in self.process_births and self.process_births[pid] != birth:
                        raise RecoveryRequired('ollama_cleanup_new_or_reused_process')
                if len(self.process_births.keys() | processes.keys()) > 64:
                    raise RecoveryRequired('ollama_cleanup_process_turnover_limit')
                self.process_births.update(processes)
            for pid, birth in processes.items():
                if pid in known:
                    raise RecoveryRequired('ollama_cleanup_ambiguous_process')
                known[pid] = (service, birth)
        uuid_resources = {self.p['gpu_uuids'][r]: r for r in self.resources}
        seen, observed_processes = set(), {}
        self.mark_phase('compute_apps')
        raw = self.observer.command([self.p['nvidia_smi'],
            '--query-compute-apps=gpu_uuid,pid,used_gpu_memory', '--format=csv,noheader,nounits'], deadline)
        for row in csv.reader(io.StringIO(raw)):
            if not row:
                continue
            if len(row) != 3:
                raise RecoveryRequired('ollama_cleanup_invalid_compute_observation')
            uuid = physical_gpu_uuid(row[0].strip())
            pid = row[1].strip()
            if not pid.isdecimal() or not 0 < int(pid) < 2**31:
                raise RecoveryRequired('ollama_cleanup_invalid_compute_observation')
            pid = str(int(pid))
            self.mark_phase('compute_apps', known.get(pid, (None,))[0], pid)
            self.number(row[2])
            if uuid not in uuid_resources:
                if pid in self.process_births:
                    raise RecoveryRequired('ollama_cleanup_unleased_gpu_activity')
                continue  # Independent GPU0 Music need not be idle.
            if (uuid, pid) in seen:
                raise RecoveryRequired('ollama_cleanup_unknown_or_duplicate_gpu_process')
            seen.add((uuid, pid))
            if pid in known:
                service, birth = known[pid]
                if service != 'ollama' and not self.can_relinquish():
                    raise RecoveryRequired('ollama_cleanup_gpu_activity_unexplained')
                observed_processes[pid] = birth
            # EVERY compute row invalidates the sample, even zero-MiB rows and
            # previously captured workers. Unknown is never promoted to trusted.
            self.sample_discarded = True
        gpus = {}
        self.mark_phase('gpu_health')
        for resource, uuid in ((r, self.p['gpu_uuids'][r]) for r in self.resources):
            raw = self.observer.command([self.p['nvidia_smi'], '--id=' + uuid,
                '--query-gpu=uuid,memory.used,utilization.gpu,temperature.gpu,ecc.errors.uncorrected.volatile.total,retired_pages.pending',
                '--format=csv,noheader,nounits'], deadline)
            rows = list(csv.reader(io.StringIO(raw)))
            if len(rows) != 1 or len(rows[0]) != 6 or physical_gpu_uuid(rows[0][0].strip()) != uuid:
                raise RecoveryRequired('ollama_cleanup_physical_gpu_unverified')
            _, memory, utilization, temperature, ecc, retired = [x.strip() for x in rows[0]]
            memory, utilization, temperature = map(self.number, (memory, utilization, temperature))
            if (utilization > 100 or temperature > self.p['max_temperature_c'] or ecc not in ('0', '[N/A]', 'N/A') or
                    retired not in ('No', 'no', '0', '[N/A]', 'N/A')):
                raise RecoveryRequired('ollama_cleanup_gpu_health_unverified')
            gpus[resource] = {'uuid': uuid, 'memory_mb': memory, 'utilization_pct': utilization,
                              'temperature_c': temperature}
        main = str(identities['ollama']['pid'])
        for pid, birth in self.process_births.items():
            self.mark_phase('process_birth_recheck', 'ollama', pid)
            try:
                current = self.birth_reader(pid)
            except ProcessDisappeared as exc:
                if pid == main or exc.pid != pid:
                    raise
                if pid in observed_processes:
                    self.sample_discarded = True
                continue
            if current != birth:
                raise RecoveryRequired('ollama_cleanup_process_birth_changed')
        self.mark_phase('queues')
        queues = self.observer.queues(self.resources, deadline,
            skip_wallpaper=not identities['wallpaper']['running'],
            **({'allow_ollama_residency': True} if self.can_relinquish() else {}))
        if self.can_relinquish() and any(q.get('models') for name, q in queues.items()
                                       if self.observer.targets[name].kind == 'ollama'):
            self.sample_discarded = True
        self.mark_phase('container_identity')
        if self.identities(deadline) != identities:
            raise RecoveryRequired('ollama_cleanup_generation_changed')
        self.bound()
        observation = {'observed_at': timestamp(), 'physical_gpus': gpus,
                       'driver_health': driver,
                       'process_snapshot_discarded': self.sample_discarded}
        # Keep one bounded physical observation in the existing job. No helper
        # attestations or per-process diagnostic journal on the success path.
        self.mark_phase('observation_persistence')
        self.record(ollama_cleanup_observation=observation)
        self.mark_phase('limits')
        pending = False
        stopped = not identities['wallpaper']['running'] and identities['wallpaper']['pid'] == 0
        ceilings = self.p['stopped_idle_memory_mb'] if stopped and 'stopped_idle_memory_mb' in self.p else self.p['idle_memory_mb']
        if any(g['memory_mb'] > ceilings[r] for r, g in gpus.items()):
            if not owned_work:
                raise RecoveryRequired('ollama_cleanup_unowned_memory_residency')
            pending = True
        remaining(deadline, CleanupPending)
        if self.sample_discarded:
            raise _ProcessPending()
        # Only after every identity/queue/driver/health check and the durable
        # observation write may normal post-unload utilization consume the
        # existing budget. Foreign compute on an owned GPU remains unexplained;
        # independent GPU0 Music is outside this GPU1 observation as before.
        if any(g['utilization_pct'] > self.p['idle_utilization_pct'] for g in gpus.values()):
            if not owned_work:
                raise RecoveryRequired('ollama_cleanup_gpu_activity_unexplained')
            raise _UtilizationPending()
        remaining(deadline, CleanupPending)
        if pending:
            raise _MemoryPending()
        return observation

    @_report_errors
    def verify(self, deadline):
        stable = 0
        pending = None
        try:
            while True:
                self.mark_phase('deadline')
                remaining(deadline, CleanupPending)
                delay = self.observer.poll_interval
                try:
                    observation = self.sample(deadline)
                except _UtilizationPending:
                    stable = 0
                    if self.can_relinquish():
                        return self.completed_release()
                    pending = 'ollama_cleanup_utilization_pending'
                    delay = 1.0
                except (_MemoryPending, _ProcessPending) as exc:
                    stable = 0
                    if self.can_relinquish():
                        return self.completed_release()
                    pending = ('ollama_cleanup_process_settling_pending' if isinstance(exc, _ProcessPending)
                               else None)
                else:
                    stable += 1
                    required = max(3, self.p['samples'])
                    if stable >= required:
                        remaining(deadline, CleanupPending)
                        return {'policy_id': self.policy_id, 'binding': binding(self.owner),
                                'stable_samples': stable, 'observation': observation,
                                'policy': 'owned_unload_then_calibrated_physical_idle'}
                self.mark_phase('deadline')
                time.sleep(min(delay, remaining(deadline, CleanupPending)))
        except CleanupPending as exc:
            if pending and exc.code == 'backend_deadline':
                raise CleanupPending(pending) from None
            raise

    @_report_errors
    def commit_check(self, deadline, *, terminal=False):
        """One fresh full sample; restabilize only after proof is invalidated.

        Callers establish stability with verify() first. A healthy final sample
        confirms that proof; turnover/residency/utilization must instead obtain
        a new consecutive stable run inside the SAME deadline. No backend effect.
        """
        self.terminal = terminal
        if terminal:
            self.boundary = 'prerelease'
        try:
            self.mark_phase('deadline')
            remaining(deadline, CleanupPending)
            try:
                observation = self.sample(deadline)
            except (_MemoryPending, _ProcessPending, _UtilizationPending):
                evidence = self.completed_release() if self.can_relinquish() else self.verify(deadline)
                if self.release_proof is not None and evidence.get('release_outcome') == 'completed_without_idle':
                    self.release_proof.clear()
                    self.release_proof.update(evidence)
                    self.record(cleanup_verified=False, cleanup_proof=self.release_proof)
                return evidence
            remaining(deadline, CleanupPending)
            return {'policy_id': self.policy_id, 'binding': binding(self.owner),
                    'stable_samples': 1, 'observation': observation,
                    'policy': 'fresh_sample_after_stable_physical_idle'}
        finally:
            self.terminal = False

    def release_binding(self, deadline):
        """Local-only last checks under the existing ownership mutex."""
        self.terminal = True
        try:
            self.bound()
            if self.release_proof and self.release_proof.get('release_outcome') == 'completed_without_idle':
                job = self.a.store.job(self.owner['job_id'])
                if (job.get('cleanup_verified') is not False or
                        job.get('cleanup_proof') != self.release_proof or
                        not job.get('ollama_cleanup_context', {}).get('expiration_acknowledged')):
                    raise RecoveryRequired('ollama_cleanup_completion_unverified')
            remaining(deadline, CleanupPending)
        finally:
            self.terminal = False
