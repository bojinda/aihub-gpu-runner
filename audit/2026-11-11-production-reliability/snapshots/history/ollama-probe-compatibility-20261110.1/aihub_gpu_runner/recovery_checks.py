"""Narrow recovery eligibility and stable/volatile observation separation."""
import hashlib
from datetime import datetime
from .core import encode, FINAL, lock_identity

CASE = 'wallpaper_prestart_v1'
REASONS = frozenset({'service_exceeds_calibrated_idle_baseline_music',
                     'service_exceeds_calibrated_idle_baseline_ollama'})
LIFECYCLE_FIELDS = {'identity', 'original_container', 'phase', 'created_at', 'startup_deadline'}
STATE_FIELDS = {'Status', 'Running', 'Paused', 'Restarting', 'OOMKilled', 'Dead',
                'Pid', 'ExitCode', 'Error', 'StartedAt', 'FinishedAt', 'Health'}


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def generation(meta):
    """Health churn is NOT process generation; unexpected State fields block."""
    state = meta['State']
    required = {'Id', 'Image', 'RestartCount', 'contract', 'pid_birth', 'netns', 'executable', 'address',
                'State', 'health_fresh', 'readiness'}
    if not required <= set(meta) or any(not meta[k] for k in ('Id', 'Image', 'contract')):
        raise ValueError('container_identity_incomplete')
    if state.get('Running') and any(not meta[k] for k in ('pid_birth', 'netns', 'executable', 'address')):
        raise ValueError('running_process_identity_incomplete')
    if set(state) != STATE_FIELDS and set(state) != STATE_FIELDS - {'Health'}:
        raise ValueError('unsupported_docker_state_schema')
    # Capture all supplied outer identity/runtime fields. The trusted observer
    # must include contract, kernel PID birth, namespace and route identities.
    return dict(meta, State={k: v for k, v in state.items() if k != 'Health'})


def semantic_observation(value):
    return {'boot': value['boot'], 'source': value['source'],
            'configuration': value['configuration'],
            'isolation': value['isolation']['binding'],
            'containers': {k: generation(v) for k, v in value['containers'].items()}}


def observation_checks(value, now, policy, *, fenced=False):
    failed = []
    def need(ok, code):
        if not ok:
            failed.append(code)
    need(type(value.get('at')) in (int, float) and 0 <= now-value['at'] <= 10,
         'observation_stale')
    need(value.get('backend_available') is True, 'backend_unavailable')
    isolation = value.get('isolation', {})
    need(isolation.get('verified') is True, 'ingress_isolation_unverified')
    required = {'ipv4', 'ipv6', 'loopback', 'bridge', 'direct_ip', 'published_ports',
                'established_connections', 'manual_comfyui', 'docker_control', 'local_gpu_exec'}
    need(set(isolation.get('coverage', {})) == required and
         all(v is True for v in isolation.get('coverage', {}).values()),
         'producer_exclusion_incomplete')
    need(isolation.get('observer_route_verified') is True, 'observer_route_unverified')
    need(isolation.get('unmanaged_clients') == [], 'unmanaged_clients')
    if fenced:
        need(isolation.get('gate_closed') is True and isolation.get('denial_verified') is True,
             'maintenance_denial_unverified')
    need(value.get('manual_activity') is False, 'manual_activity_or_unknown')
    proof = value.get('proof', {})
    try:
        stamp = datetime.fromisoformat(proof['observed_at'])
        need(stamp.tzinfo is not None and 0 <= now-stamp.timestamp() <= 10, 'physical_proof_stale')
    except (ValueError, KeyError, TypeError):
        failed.append('physical_proof_stale')
    for k in ('cleanup_verified', 'idle_ready', 'backend_stopped', 'original_execution_gone'):
        need(proof.get(k) is True, k + '_required')
    need(type(proof.get('stable_samples')) is int and
         proof['stable_samples'] >= max(3, policy.get('samples', 3)), 'stable_samples_required')
    queues = proof.get('queues', {})
    need(queues.get('music') == {'queue_running': [], 'queue_pending': []} and
         queues.get('ollama') == {'models': []}, 'queues_or_residency_not_empty')
    physical = proof.get('observations', {})
    need(physical.get('driver_health', {}).get('reported_gpu_faults') is False and
         physical.get('driver_health', {}).get('boot_id') == value.get('boot'), 'gpu_driver_health_unverified')
    try:
        for resource in ('gpu0', 'gpu1'):
            gpu = physical['gpus'][resource]
            need(gpu['uuid'] == policy['gpu_uuids'][resource], 'physical_gpu_identity_changed')
            need(0 <= gpu['memory_mb'] <= policy['stopped_idle_memory_mb'][resource] and
                 0 <= gpu['utilization_pct'] <= policy['idle_utilization_pct'] and
                 0 <= gpu['temperature_c'] <= policy['max_temperature_c'], 'gpu_not_idle')
            for service in ('music', 'ollama', 'wallpaper'):
                used = physical['service_memory_mb'][service][resource]
                need(0 <= used <= policy['services'][service]['idle_memory_mb'][resource],
                     service + '_residency_above_accepted_policy')
        need(set(physical['service_identities']) == {'music', 'ollama', 'wallpaper'},
             'attributed_service_inventory_incomplete')
    except (KeyError, TypeError, ValueError):
        failed.append('physical_observations_unavailable')
    for service in ('music', 'ollama'):
        meta = value.get('containers', {}).get(service, {})
        state = meta.get('State', {})
        health = state.get('Health', {})
        need(state.get('Running') is True and state.get('Paused') is False and
             state.get('Restarting') is False and state.get('Dead') is False and
             state.get('OOMKilled') is False, service + '_lifecycle_not_ready')
        need(health.get('Status') == 'healthy' and health.get('FailingStreak') == 0 and
             meta.get('health_fresh') is True and meta.get('readiness') is True,
             service + '_health_not_ready')
    try:
        semantic_observation(value)
    except (KeyError, ValueError, TypeError):
        failed.append('container_identity_unavailable')
    return failed


def inspect_candidate(admission, jobs, *, active=False):
    """Pure inspection: never instantiates a backend or edits an incident."""
    state = admission.snapshot()
    owners = state['owners']
    failed = []
    def need(ok, code):
        if not ok:
            failed.append(code)
    need(not active, 'supervision_active')
    need(set(owners) == {'gpu0', 'gpu1'} and owners.get('gpu0') == owners.get('gpu1'),
         'exact_paired_owners_required')
    owner = owners.get('gpu0', {})
    job = next((j for j in jobs if j.get('request_id') == owner.get('job_id')), {})
    need(bool(job), 'affected_job_unavailable')
    need(all(j.get('state') in FINAL for j in jobs if j != job), 'other_nonterminal_jobs')
    boot = admission.boot_reader()
    need(bool(boot) and boot == state.get('verified_boot') == owner.get('boot_id') == job.get('boot_id'),
         'boot_binding_changed')
    policy = state.get('recovery_policy', {})
    need(state.get('ready') is True and state.get('boot_recovery_status') == 'ready',
         'runner_not_ready_for_metadata_recovery')
    need(owner.get('target') == job.get('target') == 'wallpaper' and
         owner.get('phase') == job.get('state') == 'reconciling' and
         job.get('operation') == 'workflow' and job.get('resources') == ['gpu0', 'gpu1'] and
         job.get('recovery_status') == 'operator_required', 'unsupported_case')
    need(all(job.get(k) == owner.get(k) and owner.get(k) for k in ('lease_id', 'request_hash')),
         'job_owner_mismatch')
    need(job.get('request_hash') == digest({k: job.get(k) for k in ('target', 'operation', 'payload')}),
         'request_hash_mismatch')
    life = job.get('wallpaper_lifecycle', {})
    need(set(life) == LIFECYCLE_FIELDS and life.get('phase') == 'prepared',
         'not_proven_never_started')
    original = life.get('original_container', {})
    need(original.get('running') is False and original.get('pid') == 0,
         'original_container_not_stopped')
    expected = {k: owner.get(k) for k in ('job_id', 'lease_id', 'request_hash', 'boot_id', 'target')}
    expected.update(policy_id=policy.get('policy_id'),
                    physical_gpu_uuids=policy.get('policy', {}).get('gpu_uuids'),
                    resources=['gpu0', 'gpu1'])
    need(life.get('identity') == dict(expected, container_id=original.get('id')), 'lifecycle_identity_mismatch')
    incidents = [v for v in state.get('automatic_recovery', {}).values()
                 if v.get('identity', {}).get('lease_id') == owner.get('lease_id')]
    inc = incidents[0] if len(incidents) == 1 else {}
    need(inc.get('identity') == expected and inc.get('phase') == 'operator_required' and
         inc.get('kind') == 'wallpaper' and inc.get('observed_boot') == boot,
         'exact_incident_required')
    reason = inc.get('error')
    need(reason in REASONS and job.get('recovery_error') == reason and
         job.get('lifecycle_operator_error') == reason, 'unsupported_recovery_reason')
    need(owner.get('lease_id') not in state.get('recovery_dispositions', {}) and
         all(a.get('lease_id') != owner.get('lease_id') for a in state.get('recoveries', [])),
         'recovery_already_recorded')
    need(all(v.get('identity', {}).get('lease_id') != owner.get('lease_id') for v in
             state.get('wallpaper_release_receipts', {}).values()), 'release_receipt_exists')
    try:
        admission._check_physical_identities(state)
        need(all(not p.is_symlink() and lock_identity(p.stat()) == state['lock_identity'][r]
                 for r, p in admission.lock_paths.items()), 'physical_locks_changed')
    except Exception:
        failed.append('physical_locks_changed')
    return dict(failed=failed, state=state, job=job, incident=inc, identity=expected,
                reason=reason, job_id=owner.get('job_id'), case=CASE)
