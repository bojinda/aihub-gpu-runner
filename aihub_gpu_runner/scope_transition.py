"""Explicit idle-only local scope transition; preserve ownership/history and locks."""
import os
from .config import load_config
from .core import Admission, AtomicStore, NativeLock, RecoveryRequired, FINAL, lock_identity, timestamp


def transition_scope(old_config, new_config, evidence, *, rollback=False):
    old, old_targets, old_state, old_locks, old_scope = load_config(old_config)
    new, new_targets, new_state, new_locks, new_scope = load_config(new_config)
    larger, smaller = (old, new) if rollback else (new, old)
    larger_targets, smaller_targets = (old_targets, new_targets) if rollback else (new_targets, old_targets)
    check = dict(larger, targets={n: v for n, v in larger['targets'].items() if n in smaller['targets']})
    additions = set(larger_targets) - set(smaller_targets)
    if (check != smaller or not additions or any(larger_targets[n].mode_switch is None for n in additions) or
            old_state != new_state or old_locks != new_locks or old_scope == new_scope):
        raise ValueError('only_additive_dual_target_scope_transition_permitted')
    if (not evidence.get('approval_ref') or evidence.get('old_scope') != old_scope or
            evidence.get('new_scope') != new_scope or any(evidence.get(k) is not True for k in
            ('callers_excluded', 'backend_quiescent', 'cleanup_verified', 'gpu1_restored', 'backend_ready',
             'placement_verified'))):
        raise ValueError('scope_transition_evidence_required')
    if not (old_state / 'ownership.json').is_file():
        raise RecoveryRequired('existing_ownership_required_no_bootstrap')
    store = AtomicStore(old_state)
    supervisor = NativeLock(store.path('supervisor.lock'))
    if not supervisor.acquire(0):
        raise RecoveryRequired('scope_transition_supervisor_active')
    locks = []
    try:
        with NativeLock(store.path('jobs.lock')).held(), NativeLock(store.path('admission.lock')).held():
            state = store.read('ownership.json')
            if not isinstance(state, dict):
                raise RecoveryRequired('existing_ownership_required_no_bootstrap')
            current_scope = state.get('scope')
            if current_scope not in (old_scope, new_scope):
                raise RecoveryRequired('scope_transition_baseline_changed')
            # Avoid constructor initialization: never create/reset ownership here.
            gate = Admission.__new__(Admission)
            gate.store, gate.scope, gate.lock_paths = store, current_scope, old_locks
            gate._validate(state)
            if not state['ready'] or state['owners'] or any(j['state'] not in FINAL for j in store.jobs()):
                raise RecoveryRequired('scope_transition_requires_resolved_idle_journal')
            for resource, path in sorted(old_locks.items()):
                if not path.is_file() or lock_identity(path.stat()) != state['lock_identity'][resource]:
                    raise RecoveryRequired('physical_lock_identity_changed')
                lock = NativeLock(path)
                if not lock.acquire(0):
                    raise RecoveryRequired('scope_transition_resource_busy')
                locks.append(lock)
                if lock_identity(os.fstat(lock.fd)) != state['lock_identity'][resource]:
                    raise RecoveryRequired('physical_lock_identity_changed_during_acquisition')
            if current_scope == new_scope:
                previous = state.get('scope_transitions', [])
                if not previous or any(previous[-1].get(k) != evidence[k]
                                       for k in ('old_scope', 'new_scope', 'approval_ref')):
                    raise RecoveryRequired('scope_transition_outcome_requires_review')
                return {'scope': new_scope, 'already_recorded': True, 'journal_preserved': True}
            if rollback:
                previous = state.get('scope_transitions', [])
                if (not previous or previous[-1].get('new_scope') != old_scope or
                        previous[-1].get('old_scope') != new_scope or
                        evidence.get('rollback_of') != previous[-1].get('approval_ref')):
                    raise ValueError('exact_previous_scope_transition_rollback_required')
            state.setdefault('scope_transitions', []).append(dict(evidence, rollback=rollback,
                                                                  transitioned_at=timestamp()))
            state['scope'] = new_scope
            store.write('ownership.json', state)
            return {'scope': new_scope, 'already_recorded': False, 'journal_preserved': True}
    finally:
        for lock in reversed(locks):
            lock.close()
        supervisor.close()
