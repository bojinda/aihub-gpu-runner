"""Opt-in failure-only handoff for acknowledged, never-started wallpaper work.

Uses RecoveryManager's supervision/native locks, policy and ownership transaction.
No start, stop, unload, inference or output publication is authorized here.
"""
import copy
import math
from pathlib import Path
import time

from .backends import CleanupPending, Uncertain, remaining
from .core import RecoveryRequired, timestamp
from .recovery import PersistenceUncertain, digest, now, valid_boot


KIND = 'never-submitted-prestart-failure'
ERROR = 'wallpaper_prestart_failed_no_submission'
RECEIPTS = 'wallpaper_prestart_failure_receipts'


def source_identity():
    import hashlib
    root = Path(__file__).parent
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in
            ('config.py', 'core.py', 'backends.py', 'recovery.py', 'wallpaper_lifecycle.py', 'prestart_recovery.py')}


class PrestartRecovery:
    def __init__(self, manager):
        self.m, self.a, self.store = manager, manager.a, manager.store
        self.p, self.observer = copy.deepcopy(manager.p), manager.observer
        self.enabled = self.p.get('never_submitted_prestart_recovery', {}).get('enabled') is True

    def capture(self, owner, resources):
        state = self.a.snapshot()
        self.a._check_physical_identities(state)
        return {'schema': 1, 'scope': self.a.scope, 'policy_id': self.m.policy_id,
                'lock_identity': copy.deepcopy(state['lock_identity']), 'source_sha256': source_identity()}

    @staticmethod
    def request(job):
        return {k: job[k] for k in ('target', 'operation', 'payload')}

    def no_execution(self, job, record):
        allowed = {'identity', 'phase', 'original_container', 'created_at', 'startup_deadline',
                   'prestart_binding', 'prestart_failure'}
        if (set(record) != allowed or record['phase'] != 'prepared' or
                any(k in job for k in ('execution_container', 'execution_processes', 'execution_deadline',
                    'result', 'receipt', 'output', 'completion_verified', 'cleanup_verified', 'host_requests')) or
                job.get('backend_detail', {}) != {} or job.get('host_stage') or
                job.get('lifecycle_operator_error') or job.get('recovery_status') == 'operator_required' or
                self.store.path('results/' + job['request_id'] + '.json').exists() or
                self.store.path('results/' + job['request_id'] + '.bin').exists()):
            raise RecoveryRequired('prestart_execution_absence_unverified')
        prepared = {k: v for k, v in record.items() if k != 'prestart_failure'}
        failure = record['prestart_failure']
        if (not isinstance(failure, dict) or set(failure) != {'prepared_sha256', 'error', 'recorded_at'} or
                failure['prepared_sha256'] != digest(prepared) or not isinstance(failure['error'], str) or
                not failure['error']):
            raise RecoveryRequired('prestart_acknowledged_failure_required')

    def checked(self, owner, resources, expected_job=None, expected_record=None):
        job = self.m.lifecycle.bound(owner, resources)
        state = self.a.snapshot()
        installed = state.get('recovery_policy', {})
        if (not self.enabled or installed.get('policy') != self.p or self.m.p != self.p or
                installed.get('policy_id') != digest(self.p) or self.m.policy_id != digest(self.p) or
                self.observer.p != self.p or state.get('ready') is not True or
                state.get('verified_boot') != owner.get('boot_id') or not valid_boot(owner.get('boot_id')) or
                tuple(resources) != ('gpu0', 'gpu1') or state.get('recovery_worker_status') == 'operator_required'):
            raise RecoveryRequired('prestart_policy_boot_or_scope_changed')
        if any(owner['lease_id'] in state.get(key, {}) for key in (RECEIPTS, 'wallpaper_lifecycle_receipts')):
            raise RecoveryRequired('prestart_prior_receipt_with_retained_owner')
        record = self.m.lifecycle.record(owner, resources)
        self.no_execution(job, record)
        expiry = record['startup_deadline']
        if type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry <= 0:
            raise RecoveryRequired('prestart_startup_deadline_unverified')
        if (record['prestart_binding'] != self.capture(owner, resources) or
                job.get('request_id') != owner['job_id'] or digest(self.request(job)) != owner['request_hash'] or
                job.get('lease_id') != owner['lease_id'] or job.get('boot_id') != owner['boot_id'] or
                job.get('error_code') != record['prestart_failure']['error'] or
                job.get('state') not in ('reconciling', 'cleanup_pending') or
                job.get('resources') != list(resources) or
                (expected_job is not None and job != expected_job) or
                (expected_record is not None and record != expected_record)):
            raise RecoveryRequired('prestart_source_job_or_journal_changed')
        original = record['original_container']
        b = self.p['services']['wallpaper']
        if (original.get('id') != b['id'] or original.get('contract_sha256') != b['contract_sha256'] or
                original.get('running') is not False or original.get('pid') != 0 or
                not isinstance(original.get('started_at'), str) or not original['started_at']):
            raise RecoveryRequired('prestart_original_stopped_identity_unverified')
        return job, record

    def identities(self, deadline):
        identities = {s: self.observer.container(s, deadline) for s in self.p['services']}
        if any(not identities[s]['running'] or identities[s]['pid'] <= 0 for s in ('music', 'ollama')):
            raise RecoveryRequired('prestart_expected_services_not_running')
        processes = {s: self.observer.processes(i, deadline) for s, i in identities.items()}
        return identities, processes

    def proof(self, owner, resources, job, record, deadline):
        identities, processes = self.identities(deadline)
        if identities['wallpaper'] != record['original_container']:
            raise RecoveryRequired('prestart_stopped_generation_changed')
        stable = []
        while True:
            remaining(deadline)
            self.checked(owner, resources, job, record)
            if self.identities(deadline) != (identities, processes):
                raise RecoveryRequired('prestart_service_or_pid_birth_changed')
            try:
                queues = self.observer.queues(resources, deadline, skip_wallpaper=True)
                # Keep the existing strict per-service / foreign-process semantics.
                sample = self.observer.gpu_sample(resources, deadline, stopped=True)
            except (CleanupPending, Uncertain):
                stable = []
            else:
                if self.identities(deadline) != (identities, processes):
                    raise RecoveryRequired('prestart_service_or_pid_birth_changed')
                self.checked(owner, resources, job, record)
                remaining(deadline)
                stable.append({'queues': queues, 'observations': sample, 'observed_at': timestamp()})
                if len(stable) >= self.p['samples']:
                    return {'idle_ready': True, 'cleanup_verified': True, 'backend_stopped': True,
                            'stable_samples': len(stable), 'samples': stable, 'process_births': processes,
                            'service_identities': identities, 'stopped_container': identities['wallpaper']}
            time.sleep(min(self.observer.poll_interval, remaining(deadline)))

    def recover(self, incident, owner, resources):
        job, record = self.checked(owner, resources)
        if incident.get('attempt_reserved') or incident.get('prestart_observation_deadline'):
            raise RecoveryRequired('prestart_observation_interrupted_no_renewal')
        # One durable budget reservation + deadline. Interrupted attempts retain
        # ownership; a later worker cannot obtain a fresh observation budget.
        def begin(state):
            self.checked(owner, resources, job, record)
            budget = state.setdefault('recovery_budget', [])
            from .recovery import seconds
            if len([e for e in budget if now() - seconds(e['at']) < self.p['window_seconds']]) >= self.p['max_incidents']:
                raise RecoveryRequired('persistent_recovery_rate_limit')
            current = state['automatic_recovery'][incident['id']]
            if current != incident or current['phase'] == 'operator_required':
                raise RecoveryRequired('prestart_incident_changed')
            budget.append({'incident_id': incident['id'], 'at': timestamp()})
            updated = dict(current, kind=KIND, phase='prestart_observing', attempt_reserved=True,
                           prestart_observation_deadline=now() + self.p['observation_timeout'])
            state['automatic_recovery'][incident['id']] = updated
            return updated
        incident = self.m.change(begin)
        deadline = self.m.lifecycle.deadline(incident, 'prestart_observation_deadline', self.p['observation_timeout'])
        proof = self.proof(owner, resources, job, record, deadline)
        receipt = {'schema': 1, 'kind': KIND, 'identity': record['identity'],
                   'scope': self.a.scope, 'incident_id': incident['id'], 'error_code': ERROR,
                   'prepared_sha256': digest(record), 'request': self.request(job),
                   'prestart_binding': record['prestart_binding'], 'proof': proof, 'released_at': timestamp()}
        # jobs -> admission matches maintenance ordering. The worker's physical
        # locks are already exclusively held by RecoveryManager for this call.
        with self.m.jobs.held():
            def release(state):
                self.checked(owner, resources, job, record)
                if state['automatic_recovery'][incident['id']] != incident:
                    raise RecoveryRequired('prestart_incident_changed')
                if self.identities(deadline) != (proof['service_identities'], proof['process_births']):
                    raise RecoveryRequired('prestart_service_or_pid_birth_changed')
                self.checked(owner, resources, job, record)
                remaining(deadline)
                receipts = state.setdefault(RECEIPTS, {})
                if owner['lease_id'] in receipts or any(e.get('incident_id') == incident['id'] for e in state.get('recoveries', [])):
                    raise RecoveryRequired('prestart_receipt_or_audit_already_exists')
                receipts[owner['lease_id']] = receipt
                state['automatic_recovery'][incident['id']] = dict(incident, phase='released',
                    proof=proof, released_at=receipt['released_at'])
                state.setdefault('recoveries', []).append({'incident_id': incident['id'], 'kind': KIND,
                    'identity': incident['identity'], 'proof': proof, 'recovered_at': receipt['released_at'],
                    'observed_boot': owner['boot_id'], 'provenance': 'runner_observations'})
                for r in resources:
                    del state['owners'][r]
            self.m.change(release)
        self.finalize_released()

    def finalize_released(self):
        # Receipt reconciliation has no observer calls, native GPU acquisition,
        # output publication or second release, including after fsync ambiguity.
        with self.m.jobs.held(), self.a.mutex.held():
            state = self.a.snapshot()
            for lease_id, receipt in state.get(RECEIPTS, {}).items():
                identity = receipt['identity']
                job = self.store.job(identity['job_id'])
                record = job.get('wallpaper_lifecycle', {})
                if job.get('prestart_failure_receipt') == lease_id and job['state'] == 'failed' and job.get('error_code') == ERROR:
                    continue
                self.no_execution(job, record)
                incident = state.get('automatic_recovery', {}).get(receipt['incident_id'], {})
                if (receipt.get('schema') != 1 or receipt.get('kind') != KIND or receipt.get('scope') != self.a.scope or
                        identity != record.get('identity') or identity.get('lease_id') != lease_id or
                        any(job.get(k) != identity.get(k) for k in ('request_hash', 'lease_id', 'boot_id', 'target')) or
                        identity.get('job_id') != job['request_id'] or receipt['request'] != self.request(job) or
                        digest(self.request(job)) != identity['request_hash'] or digest(record) != receipt['prepared_sha256'] or
                        receipt['prestart_binding'] != record['prestart_binding'] or
                        incident.get('phase') != 'released' or incident.get('kind') != KIND or
                        len([e for e in state.get('recoveries', []) if e.get('incident_id') == receipt['incident_id'] and e.get('kind') == KIND]) != 1 or
                        any(o['lease_id'] == lease_id or o['job_id'] == job['request_id'] for o in state['owners'].values())):
                    raise RecoveryRequired('prestart_failure_receipt_changed')
                job.update(state='failed', error_code=ERROR, recovery_status='verified',
                           recovery_incident=receipt['incident_id'], cleanup_proof=receipt['proof'],
                           prestart_failure_receipt=lease_id, updated_at=timestamp())
                try:
                    self.store.put_job(job)
                except OSError as exc:
                    raise PersistenceUncertain() from exc
