"""Bound, one-shot metadata recovery. Constructed only inside the runner.

Observation is a trusted read-only adapter, never evidence from an HTTP caller.
No shell, signal, container lifecycle, inference, cleanup or replay interface.
"""
import base64
import contextlib
import copy
import hashlib
import hmac
import secrets
import time
from .core import Conflict, NativeLock, RecoveryRequired, encode, strict_json
from .maintenance import FILE, read_gate
from .recovery_checks import (CASE, digest, inspect_candidate, observation_checks,
                              semantic_observation)

TTL = 60


class Blocked(Exception):
    def __init__(self, checks):
        self.checks = list(dict.fromkeys(checks))
        super().__init__(','.join(self.checks))


class Tickets:
    def __init__(self, key, clock=time.time):
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError('independent_ticket_key_required')
        self.key, self.clock = key, clock
        self.session = secrets.token_hex(16)

    def issue(self, binding, principal, action):
        now = self.clock()
        value = dict(binding=binding, principal=principal, action=action,
                     nonce=secrets.token_hex(24), issued=now, expires=now+TTL,
                     schema=1, session=self.session, steady=time.monotonic())
        raw = encode(value)
        token = base64.urlsafe_b64encode(raw).decode().rstrip('=')
        return token + '.' + hmac.new(self.key, raw, hashlib.sha256).hexdigest(), value

    def verify(self, token, principal, action):
        try:
            if not isinstance(token, str) or len(token) > 8192:
                raise ValueError()
            body, signature = token.split('.')
            raw = base64.b64decode(body + '=' * (-len(body) % 4), altchars=b'-_', validate=True)
            if not hmac.compare_digest(hmac.new(self.key, raw, hashlib.sha256).hexdigest(), signature):
                raise ValueError()
            value = strict_json(raw)
            if (value['schema'] != 1 or value['action'] != action or value['session'] != self.session or
                    not 0 <= time.monotonic()-value['steady'] < TTL or
                    value['principal'] != principal or
                    not 0 <= self.clock()-value['issued'] < TTL or
                    value['expires'] != value['issued']+TTL or self.clock() >= value['expires']):
                raise ValueError()
            return value
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise Blocked(['approval_invalid_expired_or_wrong_operator']) from None


class RecoveryControl:
    def __init__(self, runner, observer, key, *, clock=time.time):
        self.runner, self.a, self.store = runner, runner.admission, runner.store
        self.observer, self.clock = observer, clock
        self.tickets = Tickets(key, clock)
        self.operation_lock = NativeLock(self.store.path('recovery-control.lock'), create=False)

    @contextlib.contextmanager
    def exclusive(self):
        # The runner remains alive and retains supervisor.lock. Automatic
        # recovery shares recovery.lock; local stage admission shares the gate.
        with self.operation_lock.held(0), NativeLock(self.store.path('maintenance.lock'), create=False).held(0), \
                NativeLock(self.store.path('recovery.lock'), create=False).held(0):
            if self.runner.service_lock.fd is None:
                raise Blocked(['supervisor_authority_unavailable'])
            yield

    def active(self):
        return any(not f.done() for f in self.runner.futures.values()) or bool(self.runner.pending)

    def files(self):
        # Preserve every result/artifact byte, not just lifecycle metadata.
        return {str(p.relative_to(self.store.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((self.store.root / 'results').rglob('*')) if p.is_file()}

    def observe(self, policy, fenced=False):
        value = self.observer.observe(fenced=fenced)
        failed = observation_checks(value, self.clock(), policy, fenced=fenced)
        if failed:
            raise Blocked(failed)
        return value

    def snapshot(self, fenced=False):
        with self.runner.job_mutex.held(), self.a.mutex.held():
            gate = read_gate(self.store, True)
            jobs = self.store.jobs()
            candidate = inspect_candidate(self.a, jobs, active=self.active())
            failed = list(candidate['failed'])
            if gate['mode'] != ('closed' if fenced else 'open'):
                failed.append('maintenance_in_progress')
            if gate['permits']:
                failed.append('client_connections_not_drained')
            if not self.a.control_required:
                failed.append('mandatory_admission_gate_not_configured')
            if failed:
                raise Blocked(failed)
            before_files = self.files()
        # Slow physical observations do not hold admission/jobs locks. A second
        # read detects all durable activity, including a connection that opened
        # and closed while the observer was running (epoch never decreases).
        obs = self.observe(candidate['state']['recovery_policy']['policy'], fenced)
        if obs['boot'] != candidate['identity']['boot_id']:
            raise Blocked(['observer_boot_mismatch'])
        if obs['proof']['stopped_container'] != candidate['job']['wallpaper_lifecycle']['original_container']:
            raise Blocked(['stopped_generation_changed'])
        with self.runner.job_mutex.held(), self.a.mutex.held():
            current = read_gate(self.store, True)
            if (current['epoch'] != gate['epoch'] or current['mode'] != gate['mode'] or current['permits'] or
                    self.a.snapshot() != candidate['state'] or self.store.jobs() != jobs or
                    self.files() != before_files or self.active()):
                raise Blocked(['state_changed_during_observation'])
        binding = dict(case=CASE, identity=candidate['identity'], incident=candidate['incident']['id'],
                       state=digest(candidate['state']), jobs=digest(jobs), files=digest(before_files),
                       observed=digest(semantic_observation(obs)), epoch=gate['epoch'])
        return candidate, obs, binding

    def status(self):
        try:
            gate = read_gate(self.store, True)
            state = self.a.snapshot()
            from .recovery_summary import read_recovery_summary
            summary = read_recovery_summary(self.store)
            jobs = {o['job_id'] for o in state['owners'].values()}
            reasons = [self.store.job(j).get('recovery_error') for j in sorted(jobs)]
            current = gate['attempts'].get(gate['active'])
            return dict(available=True, readiness=bool(state['ready'] and gate['mode'] == 'open'),
                        owners=state['owners'], affected_jobs=sorted(jobs), recovery_reasons=reasons,
                        operator_required=bool(summary['operator_required'] or current),
                        maintenance=gate['mode'], attempt=self.public_attempt(current or gate['attempts'].get(gate.get('last_operation'))),
                        observed_at=self.clock(), qualification='offline_candidate')
        except Exception:
            return dict(available=False, readiness=None, owners=None, affected_jobs=None,
                        recovery_reasons=['state_unavailable'], operator_required=True,
                        maintenance='unknown', attempt=None, observed_at=self.clock())

    @staticmethod
    def public_attempt(attempt):
        if not attempt:
            return None
        return {k: attempt.get(k) for k in ('id', 'phase', 'job_id', 'checks', 'authority_intent')}

    def check(self, principal):
        try:
            with self.exclusive():
                candidate, obs, binding = self.snapshot()
                token, ticket = self.tickets.issue(binding, principal, 'recover')
                return dict(eligible=True, failed_checks=[], token=token,
                            expires=ticket['expires'], operation=ticket['nonce'], action='recover', case=CASE,
                            job_id=candidate['job_id'], identity=candidate['identity'],
                            incident=candidate['incident']['id'], reason=candidate['reason'],
                            effect='Permanently fail this never-started job and release its paired lease. No replay.')
        except Blocked as exc:
            return dict(eligible=False, failed_checks=exc.checks)
        except TimeoutError:
            return dict(eligible=False, failed_checks=['control_busy'])
        except Exception:
            return dict(eligible=False, failed_checks=['backend_or_state_unavailable'])

    def save_attempt(self, operation, **fields):
        with self.a.mutex.held():
            gate = read_gate(self.store, True)
            if gate['active'] != operation:
                raise Blocked(['maintenance_owner_changed'])
            attempt = gate['attempts'][operation]
            attempt.update(fields)
            attempt.setdefault('events', []).append(dict(at=self.clock(), **fields))
            self.store.write(FILE, gate)
            return copy.deepcopy(attempt)

    def verify_commit(self, attempt, *, terminal=True):
        """Independent readback; never trusts recover()'s return value."""
        state = self.a.snapshot()
        old = attempt['before_state']
        job = self.store.job(attempt['job_id'])
        original = attempt['before_job']
        identity = attempt['binding']['identity']
        audits = state.get('recoveries', [])
        previous = old.get('recoveries', [])
        if len(audits) != len(previous)+1 or audits[:-1] != previous:
            raise Blocked(['recovery_audit_not_exactly_once'])
        audit = audits[-1]
        if (audit != dict(attempt['evidence'], recovered_at=audit.get('recovered_at')) or
                not audit.get('recovered_at')):
            raise Blocked(['recovery_audit_binding_changed'])
        record = state.get('recovery_dispositions', {}).get(identity['lease_id'])
        expected = dict(owner={k: identity[k] for k in ('job_id', 'request_hash', 'lease_id', 'target', 'boot_id')},
                        resources=['gpu0', 'gpu1'], approval_ref=attempt['id'],
                        job_sha256=digest(original), audit_sha256=digest(audit),
                        error_code='operator_recovered_no_automatic_resubmission')
        if record != expected:
            raise Blocked(['recovery_disposition_unverified'])
        expected_state = copy.deepcopy(old)
        expected_state.update(owners={}, recoveries=audits)
        expected_state.setdefault('recovery_dispositions', {})[identity['lease_id']] = expected
        if state != expected_state:
            raise Blocked(['paired_release_or_immutable_history_changed'])
        retired = dict(original, state='failed', error_code=expected['error_code'], updated_at=job.get('updated_at'))
        if job != retired and (terminal or job != original):
            raise Blocked(['terminal_job_unverified'])
        others = [j for j in self.store.jobs() if j['request_id'] != attempt['job_id']]
        if digest(others) != attempt['other_jobs'] or self.files() != attempt['files']:
            raise Blocked(['unrelated_job_or_artifact_changed'])
        self.a._check_physical_identities(state)
        if self.a.boot_reader() != identity['boot_id'] or self.active():
            raise Blocked(['boot_or_supervision_changed'])
        return state

    def open_after_verification(self, attempt, phase):
        # All backend traffic goes through IngressGate. Its permit lock is the
        # SAME mutex as this durable transition. No temporary firewall changes.
        state = self.a.snapshot()
        obs = self.observe(state['recovery_policy']['policy'], True)
        if digest(semantic_observation(obs)) != attempt['binding']['observed']:
            raise Blocked(['observed_state_changed'])
        with self.runner.job_mutex.held(), self.a.mutex.held():
            gate = read_gate(self.store, True)
            if gate['active'] != attempt['id'] or gate['permits'] or self.active():
                raise Blocked(['restoration_race'])
            if phase == 'succeeded':
                self.verify_commit(attempt)
                if not state['ready']:
                    raise Blocked(['normal_readiness_unverified'])
            else:
                self.verify_unchanged(attempt)
            gate['attempts'][attempt['id']].update(phase=phase, checks=[], verified_at=self.clock(),
                verification=dict(job_terminal=phase == 'succeeded', paired_release=phase == 'succeeded',
                                  audit_verified=phase == 'succeeded', normal_readiness=state['ready'],
                                  client_access='permanent_gateway_gate_open'))
            gate.update(mode='open', active=None, epoch=gate['epoch']+1)
            self.store.write(FILE, gate)
        # Read after fsync. A lost response is reconciled by read-only result.
        verified = read_gate(self.store, True)
        if verified['attempts'][attempt['id']]['phase'] != phase or verified['mode'] != 'open':
            raise Blocked(['restoration_readback_unavailable'])
        return dict(result=phase, operation=attempt['id'], verification=verified['attempts'][attempt['id']]['verification'])

    def recover(self, token, principal, confirmed):
        if confirmed is not True:
            raise Blocked(['explicit_confirmation_required'])
        ticket = self.tickets.verify(token, principal, 'recover')
        operation = ticket['nonce']
        with self.exclusive():
            gate = read_gate(self.store, True)
            if operation in gate['attempts']:
                raise Blocked(['duplicate_approval_consumed'])
            candidate, obs, binding = self.snapshot()
            if binding != ticket['binding']:
                raise Blocked(['approved_state_changed'])
            with self.runner.job_mutex.held(), self.a.mutex.held():
                gate = read_gate(self.store, True)
                # Revalidate after observation: external gateway activity and
                # host launchers may have run during the read-only check.
                if gate['mode'] != 'open' or gate['permits'] or gate['epoch'] != binding['epoch'] or self.active():
                    raise Blocked(['concurrent_submission'])
                if digest(self.a.snapshot()) != binding['state'] or digest(self.store.jobs()) != binding['jobs']:
                    raise Blocked(['approved_state_changed'])
                self.tickets.verify(token, principal, 'recover')
                attempt = dict(id=operation, phase='fenced', job_id=candidate['job_id'],
                    principal=principal, binding=binding, ticket_sha256=hashlib.sha256(token.encode()).hexdigest(),
                    before_state=candidate['state'], before_job=candidate['job'], files=self.files(),
                    other_jobs=digest([j for j in self.store.jobs() if j['request_id'] != candidate['job_id']]),
                    authority_intent=False, checks=[], events=[dict(phase='fenced', at=self.clock())])
                gate['attempts'][operation] = attempt
                gate.update(mode='closed', active=operation, last_operation=operation)
                self.store.write(FILE, gate)
            try:
                candidate, proof_obs, after = self.snapshot(True)
                if after != binding:
                    raise Blocked(['state_changed_after_fence'])
                self.tickets.verify(token, principal, 'recover')
                evidence = dict(candidate['identity'], approval_ref=operation, incident_id=binding['incident'],
                    operator_approved_backend_recovery=True, backend_quiescent=True, cleanup_verified=True,
                    case=CASE, operator=principal, binding_sha256=digest(binding),
                    proof_sha256=digest(proof_obs), proof=proof_obs['proof'])
                attempt = self.save_attempt(operation, phase='authority_intent', authority_intent=True, evidence=evidence)
                # Exactly one call. No exception path or restart retries it.
                self.a.recover(candidate['job_id'], evidence)
                self.verify_commit(attempt, terminal=False)
                self.a.finalize_recoveries(candidate['job_id'])
                self.verify_commit(attempt)
                attempt = self.save_attempt(operation, phase='verified_release')
                return self.open_after_verification(attempt, 'succeeded')
            except Exception as exc:
                checks = exc.checks if isinstance(exc, Blocked) else ['execution_uncertain']
                self.save_attempt(operation, phase='operator_required', checks=checks)
                return dict(result='operator_required', operation=operation, failed_checks=checks,
                            maintenance='closed')

    def reconcile(self, operation):
        """Never repeats authority; only finalizes an already verified commit."""
        with self.exclusive():
            gate = read_gate(self.store, True)
            attempt = gate['attempts'].get(operation)
            if not attempt:
                raise Blocked(['unknown_operation'])
            if attempt['phase'] in ('succeeded', 'aborted'):
                return dict(result=attempt['phase'], operation=operation,
                            verification=attempt['verification'], historical=True)
            if gate['active'] != operation or not attempt['authority_intent']:
                raise Blocked(['no_committed_recovery_to_verify'])
            self.verify_commit(attempt, terminal=False)
            self.a.finalize_recoveries(attempt['job_id'])
            self.verify_commit(attempt)
            return self.open_after_verification(attempt, 'succeeded')

    def verify_unchanged(self, attempt):
        if attempt['authority_intent']:
            raise Blocked(['authority_outcome_requires_verification'])
        if (self.a.snapshot() != attempt['before_state'] or
                self.store.job(attempt['job_id']) != attempt['before_job'] or
                digest(self.store.jobs()) != attempt['binding']['jobs'] or self.files() != attempt['files']):
            raise Blocked(['abort_original_state_changed'])
        self.a._check_physical_identities(attempt['before_state'])
        if self.a.boot_reader() != attempt['binding']['identity']['boot_id']:
            raise Blocked(['boot_binding_changed'])

    def check_abort(self, principal):
        try:
            with self.exclusive():
                gate = read_gate(self.store, True)
                attempt = gate['attempts'][gate['active']]
                self.verify_unchanged(attempt)
                _, obs, binding = self.snapshot(True)
                if binding != attempt['binding']:
                    raise Blocked(['abort_observed_state_changed'])
                token, ticket = self.tickets.issue(dict(binding, operation=attempt['id']), principal, 'abort')
                return dict(eligible=True, token=token, expires=ticket['expires'], action='abort',
                            failed_checks=[], job_id=attempt['job_id'], identity=binding['identity'],
                            incident=binding['incident'], effect='Restore access; retain the failed job and GPU owners.')
        except Blocked as exc:
            return dict(eligible=False, failed_checks=exc.checks)
        except Exception:
            return dict(eligible=False, failed_checks=['abort_state_unavailable'])

    def abort(self, token, principal, confirmed):
        if confirmed is not True:
            raise Blocked(['explicit_confirmation_required'])
        ticket = self.tickets.verify(token, principal, 'abort')
        with self.exclusive():
            gate = read_gate(self.store, True)
            attempt = gate['attempts'].get(ticket['binding']['operation'])
            if not attempt or gate['active'] != attempt['id']:
                raise Blocked(['abort_consumed_or_stale'])
            self.verify_unchanged(attempt)
            _, _, binding = self.snapshot(True)
            if dict(binding, operation=attempt['id']) != ticket['binding']:
                raise Blocked(['abort_state_changed'])
            self.tickets.verify(token, principal, 'abort')
            self.save_attempt(attempt['id'], abort_approval=dict(nonce=ticket['nonce'], principal=principal))
            return self.open_after_verification(attempt, 'aborted')
