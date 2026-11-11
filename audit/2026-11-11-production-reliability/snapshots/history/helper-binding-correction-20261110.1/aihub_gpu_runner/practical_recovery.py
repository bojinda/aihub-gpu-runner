"""Offline successor contract. Not mounted in the runner API or scheduler.

Reuses Admission's journal, physical identities, AtomicStore and NativeLock.
An injected, reviewed target adapter is mandatory; this module supplies no
Docker, HTTP, signal, inference, replay, network-rule or restart implementation.
"""
import copy
import hashlib
import hmac
import json
import time
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone

from .core import NativeLock, RecoveryRequired, encode, lock_identity

PAIR = ('gpu0', 'gpu1')
MODES = ('automatic', 'recover', 'emergency')
OWNER_KEYS = ('job_id', 'request_hash', 'lease_id', 'target', 'boot_id')


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


class Refused(Exception):
    pass


class PracticalRecovery:
    """Executable offline contract, with a durable one-shot operation journal.

    The host adapter must serialize all target *writes*, including direct/manual
    Comfy submissions, with fence() and stop_exact(). Idle WebSockets/GETs have
    no permits. Its fence is durable and operation-scoped, not a firewall rule.
    stop_exact must revalidate generation and exclusive scope at the actuator.
    A Docker container ID alone is deliberately not a supported actuator.
    """
    def __init__(self, admission, adapter, key, clock=time.time, monotonic=time.monotonic):
        if len(key) < 32:
            raise ValueError('strong_server_key_required')
        self.a, self.store, self.adapter, self.key = admission, admission.store, adapter, key
        self.clock, self.monotonic = clock, monotonic
        self.session = uuid.uuid4().hex
        self.mutex = NativeLock(self.store.path('practical-recovery.lock'), create=False)
        self.jobs = NativeLock(self.store.path('jobs.lock'), create=False)
        # Provisioning belongs to a future deployment, never a Check request.

    def provision_fixture(self):
        """Offline tests only. No production caller or configuration hook."""
        with NativeLock(self.mutex.path).held(), self.a.mutex.held():
            state = self.a.snapshot()
            if 'practical_control' in state:
                raise Refused('already_provisioned')
            state['practical_control'] = dict(admission='open', active=None, attempts={})
            self.store.write('ownership.json', state)

    @contextmanager
    def locked(self):
        with self.mutex.held(), self.jobs.held(), self.a.mutex.held():
            yield

    def snapshot(self):
        state = self.a.snapshot()
        self.a._check_physical_identities(state)
        control = state.get('practical_control')
        if (not isinstance(control, dict) or control.get('admission') not in ('open', 'closed') or
                not isinstance(control.get('attempts'), dict)):
            raise Refused('control_unavailable')
        owners = state['owners']
        if set(owners) != set(PAIR):
            raise Refused('complete_paired_owner_required')
        owner = owners['gpu0']
        if owners['gpu1'] != owner or owner.get('target') != 'wallpaper':
            raise Refused('paired_identity_mismatch')
        if state.get('verified_boot') != self.a.boot_reader() or owner.get('boot_id') != self.a.boot_reader():
            raise Refused('boot_changed')
        job = self.store.job(owner['job_id'])
        if (job.get('request_id') != owner['job_id'] or job.get('resources') != list(PAIR) or
                any(job.get(k) != owner[k] for k in OWNER_KEYS if k != 'job_id')):
            raise Refused('job_binding_changed')
        life = job.get('wallpaper_lifecycle', {})
        identity = life.get('identity', {})
        if any(identity.get(k) != owner[k] for k in OWNER_KEYS) or identity.get('resources') != list(PAIR):
            raise Refused('lifecycle_identity_changed')
        incidents = [(k, v) for k, v in state.get('automatic_recovery', {}).items()
                     if all(v.get('identity', {}).get(f) == owner[f] for f in OWNER_KEYS)]
        if len(incidents) != 1:
            raise Refused('exact_incident_required')
        return state, job, incidents[0]

    @staticmethod
    def never_started(job):
        life = job.get('wallpaper_lifecycle', {})
        return (life.get('phase') == 'prepared' and
                not set(life) - {'identity','original_container','phase','created_at','startup_deadline'} and
                not any(k in job for k in ('execution_container','execution_processes',
                                          'execution_deadline','backend_detail')) and
                not job.get('completion_verified'))

    def observe(self, *, never_started=False):
        try:
            obs = self.adapter.observe(timeout=5)
        except Exception:
            if not never_started:
                raise Refused('essential_observer_unavailable') from None
            # Acquired original physical locks + durable write-before-start
            # journal prove this lease launched nothing. Backend telemetry is
            # not needed to retire it; new-work readiness remains UNKNOWN.
            obs = dict(boot=self.a.boot_reader(), observed_at=self.clock(), worker_absent=True,
                       readiness={'ready':None, 'reason':'backend_observer_unavailable'})
        if (obs.get('boot') != self.a.boot_reader() or
                not 0 <= self.clock() - obs.get('observed_at', 0) <= 5 or
                obs.get('worker_absent') is not True):
            raise Refused('fresh_identity_and_inactive_worker_required')
        return obs

    def artifacts(self, job):
        life = job['wallpaper_lifecycle']
        expected = {'.json': life.get('result_sha256'), '.bin': life.get('artifact_sha256')}
        found = {}
        for ext, value in expected.items():
            path = self.store.path('results/' + job['request_id'] + ext)
            found[ext] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        saved = self.store.read('results/'+job['request_id']+'.json') if found['.json'] else None
        complete = (job.get('completion_verified') is True and
                    life.get('phase') in ('terminal','artifact_saved','stopping','stopped','verified') and
                    isinstance(saved,dict) and saved == life.get('result') and saved.get('artifact_available') is True and
                    all(expected[e] and found[e] == expected[e] for e in expected) and
                    life.get('execution_error') is None)
        return complete, found

    def plan(self, mode, state, job, incident, obs):
        if mode not in MODES:
            raise Refused('unsupported_action')
        life = job['wallpaper_lifecycle']
        never = self.never_started(job)
        expected = life.get('running_container') or life.get('original_container', {})
        current = obs.get('container', {})
        if not never and (type(current.get('running')) is not bool or type(current.get('pid')) is not int or
                          current['pid'] < 0 or current['running'] != (current['pid'] > 0)):
            raise Refused('execution_state_unverified')
        if not never and any(not expected.get(k) or current.get(k) != expected[k]
               for k in ('id', 'started_at', 'contract_sha256')):
            raise Refused('execution_generation_changed')
        if not never and current.get('running') is True and (current.get('pid') != expected.get('pid') or
                obs.get('process_identity_verified') is not True):
            raise Refused('process_identity_changed')
        gone = (current.get('running') is False and current.get('pid') == 0 and
                obs.get('original_execution_gone') is True)
        complete, hashes = self.artifacts(job)
        abandoned = (job.get('owned_execution_failed') is True or
                     isinstance(job.get('execution_deadline'), (float, int)) and
                     self.clock() >= job['execution_deadline'])
        if mode == 'automatic' and not never:
            raise Refused('automatic_requires_proven_no_start')
        if mode == 'recover' and not (never or complete or abandoned):
            raise Refused('execution_not_completed_or_known_abandoned')
        loss = not never and not complete
        stop = not (never or gone)
        stage = 'force' if mode == 'emergency' else 'graceful'
        if stop:
            if mode == 'automatic':
                raise Refused('automatic_never_stops_execution')
            if obs.get('stop_capability') != 'reviewed_generation_bound' or obs.get('write_barrier_available') is not True:
                raise Refused('exact_stop_or_target_write_barrier_unavailable')
            prompt = life.get('prompt') or job.get('backend_detail', {}).get('prompt_id')
            if obs.get('queue_available') is True:
                if any(not isinstance(obs.get(k),list) or any(not isinstance(p,str) or not p for p in obs[k])
                       for k in ('queue_running','queue_pending')):
                    raise Refused('target_queue_evidence_invalid')
                foreign = set(obs['queue_running'] + obs['queue_pending']) - {prompt}
                if foreign:
                    raise Refused('unrelated_target_work')
            elif not (mode == 'emergency' and obs.get('exclusive_execution_lease') == job['lease_id']):
                # A fresh kernel/process read + continuously enforced target
                # submission ledger can replace a hung queue API, not a guess.
                raise Refused('execution_exclusivity_unverified')
        active = state['practical_control']['active']
        if active and mode != 'emergency':
            raise Refused('interrupted_operation_verify_required')
        # A nonce is one-shot. A freshly observed, newly confirmed emergency
        # may supersede an uncertain attempt; an incident is not consumed for
        # life. Verify never repeats a signal, and old approvals cannot retry.
        return dict(case='never_started' if never else 'completed' if complete else 'abandoned',
                    terminal='success' if complete else 'failed', stop=stop, stop_stage=stage,
                    loss=loss, artifacts=hashes, parent=active, incident=incident[0])

    def binding(self, state, job, incident, obs, plan):
        # No Health logs, cache bytes, idle WebSockets, poll timestamps or
        # global idle thresholds. Relevant writes and execution progress bind.
        relevant = {k: obs.get(k) for k in ('boot', 'container', 'process_identity_verified',
            'worker_absent', 'original_execution_gone', 'queue_available', 'queue_running',
            'queue_pending', 'write_epoch', 'exclusive_execution_lease', 'execution_progress',
            'stop_capability', 'write_barrier_available')}
        if plan['case']=='never_started':
            relevant = {k:obs[k] for k in ('boot','worker_absent')}
        return digest(dict(owners=state['owners'], boot=state['verified_boot'],
            locks=state['lock_identity'], policy=state.get('recovery_policy'), job=job,
            incident=incident, observation=relevant, plan=plan))

    def check(self, mode, actor):
        try:
            with self.locked(), ExitStack() as stack:
                state, job, incident = self.snapshot()
                self.hold_physical(stack, state)
                obs = self.observe(never_started=self.never_started(job))
                plan = self.plan(mode, state, job, incident, obs)
                ticket = dict(operation=uuid.uuid4().hex, mode=mode, actor=actor,
                    session=self.session, issued=self.clock(), mono=self.monotonic(),
                    expires=self.clock()+60, binding=self.binding(state, job, incident, obs, plan),
                    loss=plan['loss'])
                raw = encode(ticket).hex()
                token = raw + '.' + hmac.new(self.key, raw.encode(), hashlib.sha256).hexdigest()
                effect = ('Preserve the saved output and finish ownership cleanup.' if plan['terminal']=='success'
                          else 'Fail the affected job and release its paired lease. No replay.')
                if plan['loss'] or mode == 'emergency':
                    effect += ' Emergency authority may stop this exact execution; unfinished output may be lost.' if mode=='emergency' else ' Abandon unfinished output from this expired or failed execution.'
                return dict(eligible=True, failed_checks=[], action=mode, token=token,
                    operation=ticket['operation'], expires=ticket['expires'], job_id=job['request_id'],
                    identity=copy.deepcopy(incident[1]['identity']), incident=incident[0],
                    effect=effect, loss_required=plan['loss'] or mode=='emergency', plan=plan)
        except (Refused, RecoveryRequired, OSError, KeyError, TypeError, ValueError, TimeoutError) as exc:
            return dict(eligible=False, failed_checks=[str(exc)], action=mode)

    def decode(self, token, actor):
        try:
            if not isinstance(token, str) or len(token) > 8192:
                raise ValueError()
            raw, mac = token.split('.')
            if not hmac.compare_digest(mac, hmac.new(self.key, raw.encode(), hashlib.sha256).hexdigest()):
                raise ValueError()
            ticket = json.loads(bytes.fromhex(raw))
            if (ticket['actor'] != actor or ticket['session'] != self.session or
                    not 0 <= self.clock()-ticket['issued'] < 60 or
                    not 0 <= self.monotonic()-ticket['mono'] < 60):
                raise ValueError()
            return ticket
        except (ValueError, TypeError, KeyError):
            raise Refused('stale_or_invalid_confirmation') from None

    def automatic(self):
        checked = self.check('automatic', 'runner-policy')
        if not checked['eligible']:
            return checked
        return self.execute(checked['token'], 'runner-policy', True, expected_mode='automatic', _automatic=True)

    def execute(self, token, actor, confirmed, accept_work_loss=False, *, expected_mode='recover', _automatic=False):
        ticket = self.decode(token, actor)
        if (ticket['mode'] != expected_mode or confirmed is not True or ticket['mode']=='automatic' and not _automatic or
                (ticket['loss'] or ticket['mode']=='emergency') and accept_work_loss is not True):
            raise Refused('explicit_confirmation_and_work_loss_acceptance_required')
        with self.locked(), ExitStack() as stack:
            state, job, incident = self.snapshot()
            control = state['practical_control']
            if ticket['operation'] in control['attempts']:
                raise Refused('duplicate_request')
            obs = self.observe(never_started=self.never_started(job))
            plan = self.plan(ticket['mode'], state, job, incident, obs)
            if ticket['binding'] != self.binding(state, job, incident, obs, plan):
                raise Refused('observed_state_changed_check_again')
            self.hold_physical(stack, state)
            self.decode(token, actor)  # validity must still hold at consumption
            attempt = dict(id=ticket['operation'], mode=ticket['mode'], actor=actor,
                approval='automatic_policy' if _automatic else 'confirmed_exact_action',
                work_loss_accepted=accept_work_loss, lease=job['lease_id'], binding=ticket['binding'],
                job_before=job, owners_before=copy.deepcopy(state['owners']), incident=incident,
                observation_before=obs, plan=plan, deadline=self.monotonic()+45,
                status='fenced', events=['admission_closed'])
            if plan['parent']:
                control['attempts'][plan['parent']]['superseded_by'] = attempt['id']
            control['attempts'][attempt['id']] = attempt
            control.update(admission='closed', active=attempt['id'])
            self.store.write('ownership.json', state)  # consumes confirmation and fences atomically
            try:
                if plan['stop']:
                    # Durable target fence + exact write epoch CAS. Never close
                    # or count harmless observation sockets.
                    self.adapter.fence(attempt['id'], obs['write_epoch'], obs['container'], timeout=5)
                    current = self.observe()
                    if self.binding(state, job, incident, current, plan) != ticket['binding']:
                        raise Refused('target_changed_during_fence')
                    if self.monotonic() >= attempt['deadline']:
                        raise Refused('operation_deadline_expired_before_stop')
                    attempt['stop_intent'] = plan['stop_stage']
                    attempt['events'].append('stop_intent')
                    self.store.write('ownership.json', state)
                    self.adapter.stop_exact(current['container'], job['lease_id'], plan['stop_stage'], attempt['id'],
                        deadline=min(attempt['deadline'], self.monotonic()+30))
                self.finish(state, attempt)
            except Exception as exc:
                current = self.a.snapshot()
                a = current['practical_control']['attempts'][attempt['id']]
                a.update(status='operator_required', error=type(exc).__name__ + ':' + str(exc))
                self.store.write('ownership.json', current)
                return dict(result='operator_required', operation=attempt['id'], reason=a['error'])
            return self.receipt(attempt['id'])

    def hold_physical(self, stack, state):
        self.a._check_physical_identities(state)
        for r in PAIR:
            lock = stack.enter_context(NativeLock(self.a.lock_paths[r], create=False).held(timeout=0))
            import os
            if lock_identity(os.fstat(lock.fd)) != state['lock_identity'][r]:
                raise Refused('physical_lock_replaced')

    def gone_proof(self, attempt):
        if attempt['plan']['case']=='never_started':
            if not self.never_started(attempt['job_before']):
                raise Refused('no_start_journal_proof_missing')
            return dict(self.observe(never_started=True), cleanup_basis='durable_no_start_intent_and_original_locks')
        obs = self.observe()
        original = attempt['observation_before']['container']
        current = obs.get('container', {})
        if (any(current.get(k) != original.get(k) for k in ('id','started_at','contract_sha256')) or
                current.get('running') is not False or current.get('pid') != 0 or
                obs.get('original_execution_gone') is not True):
            raise Refused('exact_execution_exit_unverified')
        if attempt['plan']['stop'] and not self.adapter.fence_verified(attempt['id']):
            raise Refused('target_fence_unverified')
        return obs

    def finish(self, state, attempt):
        """Commit once, then independently read the receipt and terminal job.

        Interrupted calls can resume metadata/verification here; no signal is
        sent and no start, replay or other backend effect is possible.
        """
        self.a._check_physical_identities(state)
        proof = (attempt.get('verified_proof') if attempt.get('verified_commit') else self.gone_proof(attempt))
        job = self.store.job(attempt['job_before']['request_id'])
        receipts = state.setdefault('practical_receipts', {})
        if attempt['id'] not in receipts:
            if state['owners'] != attempt['owners_before'] or job != attempt['job_before']:
                raise Refused('authority_binding_changed')
            _, hashes = self.artifacts(job)
            if hashes != attempt['plan']['artifacts']:
                raise Refused('artifact_changed')
            audit = dict(operation=attempt['id'], incident_id=attempt['incident'][0], identity=attempt['incident'][1]['identity'],
                mode=attempt['mode'], provenance=attempt['approval'], actor=attempt['actor'],
                work_loss_accepted=attempt['work_loss_accepted'], no_replay=True,
                before_binding=attempt['binding'], proof=proof, at=self.clock(),
                observed_boot=self.a.boot_reader(),
                recovered_at=datetime.fromtimestamp(self.clock(),timezone.utc).isoformat())
            state.setdefault('recoveries', []).append(audit)
            receipt = dict(operation=attempt['id'], job_id=job['request_id'], lease=job['lease_id'],
                terminal=attempt['plan']['terminal'], audit_sha256=digest(audit),
                job_before_sha256=digest(job), artifacts=hashes, proof=proof)
            receipts[attempt['id']] = receipt
            state['owners'] = {}  # both resources + audit + disposition in ONE atomic write
            attempt['events'].append('paired_release_committed')
            attempt['status'] = 'committed'
            self.store.write('ownership.json', state)
        receipt = receipts[attempt['id']]
        if job.get('practical_recovery_operation') != attempt['id']:
            if digest(job) != receipt['job_before_sha256']:
                raise Refused('job_changed_before_terminal_finalization')
            job.update(state=receipt['terminal'], resources_released=True, supervision_active=False,
                practical_recovery_operation=attempt['id'], recovery_status='verified',
                error_code=None if receipt['terminal']=='success' else
                    ('automatic_prestart_failed_no_replay' if attempt['mode']=='automatic' else
                     'emergency_abandoned_no_replay' if attempt['mode']=='emergency' else 'operator_recovered_no_replay'))
            # Keep the original lifecycle, errors and incident as evidence.
            self.store.put_job(job)
        self.verify_commit(attempt['id'])
        if not attempt.get('verified_commit'):
            proof = self.gone_proof(attempt)
            attempt.update(verified_commit=True, verified_proof=proof)
            self.store.write('ownership.json', state)
        # Only now restore target writers. Global admission stays closed until
        # adapter restoration is independently read back; no firewall changes.
        if attempt['plan']['stop']:
            self.adapter.unfence(attempt['id'])
            if not self.adapter.access_restored(attempt['id']):
                raise Refused('target_access_restoration_unverified')
        fresh = self.observe(never_started=attempt['plan']['case']=='never_started')
        state = self.a.snapshot()
        control = state['practical_control']
        attempt = control['attempts'][attempt['id']]
        attempt.update(status='succeeded', verified_at=self.clock(), readiness=fresh.get('readiness', {'ready':None}))
        attempt['events'].append('access_restored')
        control.update(active=None, admission='open')
        self.store.write('ownership.json', state)

    def verify_commit(self, operation):
        state = self.a.snapshot()
        self.a._check_physical_identities(state)
        receipt = state['practical_receipts'][operation]
        job = self.store.job(receipt['job_id'])
        audits = [a for a in state['recoveries'] if a.get('operation') == operation]
        if (any(o.get('lease_id') == receipt['lease'] for o in state['owners'].values()) or job.get('state') != receipt['terminal'] or
                job.get('practical_recovery_operation') != operation or len(audits) != 1 or
                digest(audits[0]) != receipt['audit_sha256'] or
                self.artifacts(job)[1] != receipt['artifacts']):
            raise Refused('independent_commit_verification_failed')

    def receipt(self, operation):
        state = self.a.snapshot()
        a = state['practical_control']['attempts'][operation]
        if a['status'] != 'succeeded' or state['practical_control']['admission'] != 'open':
            raise Refused('completion_not_verified')
        self.verify_commit(operation)
        try:
            readiness = self.observe(never_started=a['plan']['case']=='never_started').get('readiness', {'ready':None})
        except Refused:
            readiness = {'ready':None,'reason':'current_readiness_unavailable'}
        if self.a.boot_reader() != a['job_before']['boot_id']:
            readiness = {'ready':None,'reason':'boot_changed_since_recovery'}
        elif state['owners']:
            readiness = {'ready':False,'reason':'new_work_owns_resources'}
        ready = readiness.get('ready') is True
        return dict(result='recovered_ready' if ready else 'released_readiness_pending',
                    operation=operation, terminal=a['plan']['terminal'], readiness=readiness,
                    verified_at=a['verified_at'], no_replay=True)

    def verify_interrupted(self, operation):
        with self.locked(), ExitStack() as stack:
            state = self.a.snapshot()
            attempt = state['practical_control']['attempts'][operation]
            if attempt['status']=='succeeded':
                return self.receipt(operation)
            if state['practical_control']['active'] != operation:
                raise Refused('operation_superseded')
            self.hold_physical(stack, state)
            self.finish(state, attempt)  # observe/finalize only; never repeat a stop
            return self.receipt(operation)

    def abort_before_effect(self, operation):
        """Verified rollback for a failed fence/preflight, never an uncertain stop."""
        with self.locked():
            state, job, incident = self.snapshot()
            control = state['practical_control']; a = control['attempts'][operation]
            if (control['active'] != operation or a.get('stop_intent') or
                    job != a['job_before'] or state['owners'] != a['owners_before']):
                raise Refused('abort_cannot_prove_no_effect')
            current = self.observe()
            if self.binding(state, job, incident, current, a['plan']) != a['binding']:
                raise Refused('abort_state_changed')
            self.adapter.unfence(operation)
            if not self.adapter.access_restored(operation):
                raise Refused('abort_access_unverified')
            a.update(status='aborted', verified_at=self.clock())
            control.update(admission='open', active=None)
            self.store.write('ownership.json', state)
            return dict(result='aborted_verified', operation=operation)
