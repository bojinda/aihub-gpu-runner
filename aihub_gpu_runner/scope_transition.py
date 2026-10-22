"""Explicit idle-only local scope transition; preserve ownership/history and locks."""
import hashlib
import os
from pathlib import Path
import sys
from .config import load_config
from .core import Admission, AtomicStore, NativeLock, RecoveryRequired, FINAL, encode, lock_identity, strict_json, timestamp


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
                lock = NativeLock(path, create=False)
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


WALLPAPER_ATTESTATIONS = (
    'operator_approved_wallpaper_workflow_transition', 'callers_excluded',
    'supervisor_stopped', 'automatic_wallpaper_paused', 'backend_quiescent', 'cleanup_verified',
)


def _wallpaper_delta(old, new, old_targets, new_targets, rollback):
    full, reduced = (new_targets, old_targets) if rollback else (old_targets, new_targets)
    if ('wallpaper' not in full or 'wallpaper' not in reduced or
            full['wallpaper'].kind != 'comfy' or reduced['wallpaper'].kind != 'comfy'):
        raise ValueError('wallpaper_comfy_target_required')
    before, after = full['wallpaper'].workflow, reduced['wallpaper'].workflow
    if (before.get('95', {}).get('class_type') != 'LayerUtility: PurgeVRAM' or
            encode({n: v for n, v in before.items() if n != '95'}) != encode(after)):
        raise ValueError('only_wallpaper_purge95_removal_permitted')
    def references_95(value):
        if isinstance(value, list):
            return (len(value) == 2 and value[0] == '95' and type(value[1]) is int or
                    any(references_95(v) for v in value))
        return isinstance(value, dict) and any(references_95(v) for v in value.values())
    if any(references_95(node.get('inputs', {})) for node in after.values()):
        raise ValueError('removed_purge95_still_referenced')
    old_wallpaper, new_wallpaper = old['targets']['wallpaper'], new['targets']['wallpaper']
    keys = ('workflow_template', 'workflow_sha256')
    if any(not isinstance(w.get(k), str) or not w[k] for w in (old_wallpaper, new_wallpaper) for k in keys):
        raise ValueError('hash_bound_wallpaper_templates_required')
    expected = dict(old, targets=dict(old['targets'], wallpaper=dict(old_wallpaper,
                    **{k: new_wallpaper[k] for k in keys})))
    if encode(expected) != encode(new):
        raise ValueError('only_wallpaper_template_binding_may_change')


def transition_wallpaper_workflow(old_config, new_config, evidence, *, rollback=False):
    """Idle-only removal of node 95; commit journal, never activate config or call a backend."""
    if not sys.platform.startswith('linux'):
        raise RecoveryRequired('wallpaper_transition_requires_linux')
    configs = tuple(Path(p) for p in (old_config, new_config))
    frozen = tuple(p.read_bytes() for p in configs)
    old, old_targets, state_dir, locks, old_scope = load_config(old_config)
    new, new_targets, new_state, new_locks, new_scope = load_config(new_config)
    _wallpaper_delta(old, new, old_targets, new_targets, rollback)
    if (state_dir != new_state or
            {r: p.resolve() for r, p in locks.items()} != {r: p.resolve() for r, p in new_locks.items()} or
            old_scope == new_scope):
        raise ValueError('wallpaper_transition_storage_or_scope_changed')
    digest = lambda data: hashlib.sha256(data).hexdigest()
    bindings = dict(old_scope=old_scope, new_scope=new_scope,
                    old_config_sha256=digest(frozen[0]), new_config_sha256=digest(frozen[1]),
                    old_workflow_sha256=old['targets']['wallpaper']['workflow_sha256'],
                    new_workflow_sha256=new['targets']['wallpaper']['workflow_sha256'])
    if (not isinstance(evidence, dict) or
            any(not isinstance(evidence.get(k), str) or not evidence[k].strip() for k in
                ('approval_ref', 'exclusion_evidence_ref', 'backend_evidence_ref', 'ownership_sha256')) or
            any(evidence.get(k) is not True for k in WALLPAPER_ATTESTATIONS) or
            any(evidence.get(k) != v for k, v in bindings.items()) or
            (not rollback and 'rollback_of' in evidence)):
        raise ValueError('wallpaper_transition_exact_operator_evidence_required')
    if not (state_dir / 'ownership.json').is_file():
        raise RecoveryRequired('existing_ownership_required_no_bootstrap')
    store, held = AtomicStore(state_dir), []
    try:
        for name in ('maintenance.lock', 'supervisor.lock', 'jobs.lock', 'admission.lock'):
            lock = NativeLock(store.path(name))
            if not lock.acquire(0):
                raise RecoveryRequired('wallpaper_transition_maintenance_or_supervisor_busy')
            held.append(lock)
        state_bytes = store.path('ownership.json').read_bytes()
        state = strict_json(state_bytes)
        gate = Admission.__new__(Admission)
        gate.store, gate.scope, gate.lock_paths = store, state.get('scope') if isinstance(state, dict) else None, locks
        gate._validate(state)
        if gate.scope not in (old_scope, new_scope):
            raise RecoveryRequired('wallpaper_transition_baseline_changed')
        if not state['ready'] or state['owners'] or any(j.get('state') not in FINAL for j in store.jobs()):
            raise RecoveryRequired('wallpaper_transition_requires_resolved_idle_journal')
        gate._check_physical_identities(state)
        for resource, path in sorted(locks.items()):
            lock = NativeLock(path, create=False)
            if not lock.acquire(0):
                raise RecoveryRequired('wallpaper_transition_resource_busy')
            held.append(lock)
            if lock_identity(os.fstat(lock.fd)) != state['lock_identity'][resource]:
                raise RecoveryRequired('physical_lock_identity_changed_during_acquisition')
        # Freeze both file bindings and resolved workflow contents, then recheck pathname identities.
        if (tuple(p.read_bytes() for p in configs) != frozen or
                load_config(old_config)[-1] != old_scope or load_config(new_config)[-1] != new_scope):
            raise RecoveryRequired('wallpaper_transition_inputs_changed')
        gate._check_physical_identities(state)
        history = state.get('scope_transitions', [])
        if not isinstance(history, list):
            raise RecoveryRequired('wallpaper_transition_history_invalid')
        evidence_hash = digest(encode(evidence))
        kind = 'wallpaper_remove_purge95'
        if gate.scope == new_scope:
            if (not history or history[-1].get('kind') != kind or
                    history[-1].get('rollback') is not rollback or
                    history[-1].get('evidence_sha256') != evidence_hash or
                    any(history[-1].get(k) != v for k, v in bindings.items())):
                raise RecoveryRequired('wallpaper_transition_outcome_requires_review')
            # Complete a possibly interrupted directory fsync; never append twice.
            for path, flags in ((store.path('ownership.json'), os.O_RDONLY),
                                (state_dir, os.O_RDONLY | os.O_DIRECTORY)):
                fd = os.open(path, flags)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            return {'scope': new_scope, 'already_recorded': True, 'journal_preserved': True}
        if digest(state_bytes) != evidence['ownership_sha256']:
            raise RecoveryRequired('wallpaper_transition_journal_evidence_changed')
        if any(row.get('approval_ref') == evidence['approval_ref'] for row in history):
            raise ValueError('wallpaper_transition_fresh_approval_required')
        if rollback and (not history or history[-1].get('kind') != kind or
                history[-1].get('rollback') is not False or
                history[-1].get('old_scope') != new_scope or history[-1].get('new_scope') != old_scope or
                evidence.get('rollback_of') != history[-1].get('approval_ref')):
            raise ValueError('exact_previous_wallpaper_transition_rollback_required')
        state['scope_transitions'] = history + [dict(evidence, kind=kind, rollback=rollback,
            evidence_sha256=evidence_hash, transitioned_at=timestamp(), provenance='local_operator_attestation')]
        state['scope'] = new_scope
        store.write('ownership.json', state)
        return {'scope': new_scope, 'already_recorded': False, 'journal_preserved': True}
    finally:
        for lock in reversed(held):
            lock.close()
