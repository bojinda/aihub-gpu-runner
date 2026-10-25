"""Aggregate semantics and authenticated, strictly read-only endpoint."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aihub_gpu_runner.api import Application
from aihub_gpu_runner.core import AtomicStore, strict_json
from aihub_gpu_runner.recovery_summary import summarize_recovery

NOW = datetime(2026, 10, 24, 16, tzinfo=timezone.utc)
B0 = '00000000-0000-4000-8000-000000000000'
B1 = '11111111-1111-4111-8111-111111111111'
B2 = '22222222-2222-4222-8222-222222222222'


def wall(lease='lease-a', days=1, phase='released'):
    return {'id': 'incident-' + lease, 'kind': 'wallpaper', 'phase': phase,
            'identity': {'job_id': 'job-' + lease, 'lease_id': lease,
                         'request_hash': 'private-hash', 'target': 'wallpaper', 'boot_id': B0},
            'observed_boot': B0, 'created_at': (NOW - timedelta(days=days)).isoformat()}


def boot_record(boot=B1, previous=B0, days=1, phase='ready'):
    return {'identity': {'boot_id': boot, 'previous_verified_boot': previous},
            'phase': phase, 'created_at': (NOW - timedelta(days=days)).isoformat()}


class SummaryTests(unittest.TestCase):
    def summary(self, state, jobs=()):
        return summarize_recovery(state, jobs, at=NOW)

    def test_rolling_seven_day_boundary_is_inclusive_and_ignores_future_dates(self):
        entries = [wall('boundary', 7), wall('recent', 0), wall('old', 8), wall('future', -1)]
        near = wall('just-too-old')
        near['created_at'] = (NOW - timedelta(days=7, microseconds=1)).isoformat()
        entries.append(near)
        self.assertEqual(self.summary({'automatic_recovery': {e['id']: e for e in entries}})['incidents_7d'], 2)

    def test_one_reboot_with_multiple_gpu_leases_and_all_three_sources_counts_once(self):
        entries = [wall('gpu0'), wall('gpu1'), wall('dual')]
        for entry in entries:
            entry.update(kind='previous_boot', observed_boot=B1)
        audits = [dict(incident_id=e['id'], identity=e['identity'], observed_boot=B1,
                       recovered_at=NOW.isoformat()) for e in entries]
        state = {'automatic_recovery': {e['id']: e for e in entries}, 'recoveries': audits,
                 'boot_startups': {B1: boot_record()}, 'verified_boot': B1}
        result = self.summary(state)
        self.assertEqual(result['incidents_7d'], 1)
        self.assertFalse(result['operator_required'])

    def test_reboot_and_separate_wallpaper_container_recovery_count_two(self):
        entry = wall()
        audit = {'identity': entry['identity'], 'incident_id': entry['id'],
                 'observed_boot': B0, 'recovered_at': NOW.isoformat()}
        state = {'boot_startups': {B1: boot_record()}, 'automatic_recovery': {entry['id']: entry},
                 'recoveries': [audit, copy.deepcopy(audit)], 'verified_boot': B1}
        self.assertEqual(self.summary(state)['incidents_7d'], 2)

    def test_repeated_reconciliation_uses_first_date_not_recent_retry_or_release(self):
        entry = wall(days=8)
        entry['released_at'] = NOW.isoformat()
        state = {'automatic_recovery': {entry['id']: entry}, 'recoveries': [{
            'incident_id': entry['id'], 'identity': entry['identity'], 'recovered_at': NOW.isoformat()}]}
        self.assertEqual(self.summary(state)['incidents_7d'], 0)

    def test_old_provisional_wallpaper_record_does_not_backdate_a_new_reboot(self):
        entry = wall(days=9)
        audit = {'identity': entry['identity'], 'observed_boot': B1, 'recovered_at': NOW.isoformat()}
        state = {'automatic_recovery': {entry['id']: entry}, 'recoveries': [audit],
                 'boot_startups': {B1: boot_record()}, 'verified_boot': B1}
        self.assertEqual(self.summary(state)['incidents_7d'], 1)
        self.assertFalse(self.summary(state)['operator_required'])

    def test_unresolved_incidents_warn_even_when_undated_or_outside_window(self):
        for phase in ('observing', 'waiting_execution_deadline', 'stopping', 'starting', 'operator_required'):
            entry = wall(days=9, phase=phase)
            entry.pop('created_at')
            result = self.summary({'automatic_recovery': {entry['id']: entry}})
            self.assertEqual(result['incidents_7d'], 0)
            self.assertTrue(result['operator_required'])

    def test_explicit_current_operator_statuses_and_owned_jobs_warn(self):
        for key in ('boot_recovery_status', 'recovery_worker_status'):
            self.assertTrue(self.summary({key: 'operator_required'})['operator_required'])
        self.assertTrue(self.summary({}, [{'recovery_status': 'operator_required'}])['operator_required'])
        self.assertTrue(self.summary({'boot_startups': {B1: boot_record(phase='starting')},
                                     'verified_boot': B0})['operator_required'])

    def test_retained_supervisor_uncertainty_without_new_status_field_warns(self):
        state = {'verified_boot': B0, 'owners': {
            'gpu1': {'job_id': 'owned-ollama', 'lease_id': 'ollama-lease', 'target': 'ollama'}}}
        jobs = [{'request_id': 'owned-ollama', 'state': 'reconciling',
                 'error_code': 'supervisor_restart_unresolved_owner'}]
        result = self.summary(state, jobs)
        self.assertTrue(result['operator_required'])
        self.assertEqual(result['incidents_7d'], 0)

    def test_healthy_running_and_cleanup_jobs_do_not_warn(self):
        state = {'verified_boot': B0, 'owners': {'gpu1': {'job_id': 'healthy'}}}
        for phase in ('running', 'cleanup_pending'):
            with self.subTest(phase=phase):
                result = self.summary(state, [{'request_id': 'healthy', 'state': phase,
                                               'error_code': None}])
                self.assertFalse(result['operator_required'])
                self.assertEqual(result['incidents_7d'], 0)

    def test_one_reboot_partial_release_keeps_other_lease_warning_without_job_flag(self):
        released, retained = wall('gpu0'), wall('gpu1', phase='operator_required')
        for entry in (released, retained):
            entry.update(kind='previous_boot', observed_boot=B1)
        retained['identity']['target'] = 'ollama'
        audit = lambda entry: {'incident_id': entry['id'], 'identity': entry['identity'],
                               'observed_boot': B1, 'recovered_at': NOW.isoformat()}
        state = {'automatic_recovery': {e['id']: e for e in (released, retained)},
                 'recoveries': [audit(released)], 'verified_boot': B0,
                 'owners': {'gpu1': retained['identity']}}
        # The incident was persisted, but the redundant job status write was interrupted.
        jobs = [{'request_id': retained['identity']['job_id'], 'state': 'failed',
                 'error_code': 'guest_reboot_interrupted'}]
        result = self.summary(state, jobs)
        self.assertEqual(result['incidents_7d'], 1)
        self.assertTrue(result['operator_required'])
        state['recoveries'].append(audit(retained))
        self.assertFalse(self.summary(state, jobs)['operator_required'])
        self.assertEqual(self.summary(state, jobs)['incidents_7d'], 1)

    def test_release_without_incident_id_matches_only_its_job_and_lease(self):
        entry = wall(phase='operator_required')
        old_identity = dict(entry['identity'], lease_id='old-lease')
        state = {'automatic_recovery': {entry['id']: entry}, 'recoveries': [
            {'identity': old_identity, 'recovered_at': NOW.isoformat()}]}
        self.assertTrue(self.summary(state)['operator_required'])
        state['recoveries'].append({'identity': entry['identity'], 'recovered_at': NOW.isoformat()})
        self.assertFalse(self.summary(state)['operator_required'])

    def test_incident_only_resolution_does_not_use_shared_boot_or_missing_identity(self):
        entries = [{'id': name, 'kind': 'previous_boot', 'phase': 'operator_required',
                    'identity': {'boot_id': B0}, 'observed_boot': B1,
                    'created_at': NOW.isoformat()} for name in ('first', 'second')]
        audit = lambda incident: {'incident_id': incident, 'recovered_at': NOW.isoformat()}
        state = {'automatic_recovery': {e['id']: e for e in entries},
                 'recoveries': [audit('first'), {'recovered_at': NOW.isoformat()}]}
        self.assertTrue(self.summary(state)['operator_required'])
        self.assertEqual(self.summary(state)['incidents_7d'], 1)
        state['recoveries'].append(audit('second'))
        self.assertFalse(self.summary(state)['operator_required'])

    def test_verified_new_boot_supersedes_historical_boot_failure(self):
        state = {'boot_startups': {B1: boot_record(days=3, phase='operator_required'),
                                  B2: boot_record(B2, B1)}, 'verified_boot': B2,
                 'boot_readiness': {'observed_at': NOW.isoformat()}, 'boot_recovery_status': 'ready'}
        self.assertEqual(self.summary(state)['incidents_7d'], 2)
        self.assertFalse(self.summary(state)['operator_required'])

    def test_policy_authorization_healthy_jobs_and_routine_unloads_are_zero(self):
        result = self.summary({'recovery_policy': {'authorized_at': NOW.isoformat()}, 'verified_boot': B0,
                               'bootstrap_evidence': {}, 'owners': {'gpu0': {'phase': 'cleanup_pending'}}})
        self.assertEqual(result['incidents_7d'], 0)
        self.assertFalse(result['operator_required'])

    def test_legacy_dates_require_timezone_and_stable_identity(self):
        good = dict(wall()['identity'], recovered_at=NOW.isoformat())
        for bad_date in (None, '', '2026-10-24', '2026-10-24T16:00:00', 'invented', 123):
            bad = dict(good, lease_id='bad', recovered_at=bad_date)
            result = self.summary({'recoveries': [good, bad, {'recovered_at': NOW.isoformat()}, None]})
            self.assertEqual(result['incidents_7d'], 1)
        bogus = wall()
        bogus.update(kind='previous_boot', observed_boot='unverified')
        self.assertEqual(self.summary({'automatic_recovery': {'bogus': bogus}})['incidents_7d'], 0)


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = AtomicStore(self.tmp.name)
        entry = wall(phase='operator_required')
        entry['created_at'] = datetime.now(timezone.utc).isoformat()
        entry['prompt'] = 'private-prompt'
        entry['container'] = 'private-container'
        entry['identity']['physical_gpu_uuids'] = {'gpu0': 'private-uuid'}
        self.store.write('ownership.json', {'automatic_recovery': {entry['id']: entry},
            'owners': {'gpu0': {'job_id': 'owned'}}, 'secret_token': 'private-token'})
        self.store.put_job({'request_id': 'owned', 'recovery_status': 'operator_required', 'payload': 'private-payload'})
        self.store.put_job({'request_id': 'historical', 'recovery_status': 'operator_required'})
        self.runner = SimpleNamespace(store=self.store, submit=Mock(side_effect=AssertionError('inference')),
                                      recovery=Mock(side_effect=AssertionError('recovery')))
        self.app = Application(self.runner, 'offline-token-0000000000000000000000000')
        self.headers = {'Authorization': 'Bearer ' + self.app.token}

    def test_existing_authentication_is_required_before_reading_state(self):
        with patch.object(self.store, 'read', side_effect=AssertionError('unauthenticated read')):
            for headers in ({}, {'Authorization': 'Bearer wrong'}):
                self.assertEqual(self.app.handle('GET', '/recovery-summary', headers)[0], 401)

    def test_response_is_minimized_read_only_and_cannot_cause_gpu_or_backend_work(self):
        before = {p.relative_to(self.store.root): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.store.root.rglob('*') if p.is_file()}
        with patch.object(self.store, 'write', side_effect=AssertionError('write forbidden')), \
                patch('subprocess.run', side_effect=AssertionError('GPU/control forbidden')), \
                patch('subprocess.check_output', side_effect=AssertionError('GPU probe forbidden')), \
                patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('HTTP forbidden')), \
                patch('aihub_gpu_runner.core.NativeLock.acquire', side_effect=AssertionError('lock mutation forbidden')):
            for _ in range(3):
                code, media, data = self.app.handle('GET', '/recovery-summary', self.headers)
                self.assertEqual((code, media), (200, 'application/json'))
                result = strict_json(data)
                self.assertEqual(set(result), {'schema_version', 'incidents_7d', 'operator_required', 'snapshot_at'})
                self.assertEqual(result['incidents_7d'], 1)
                self.assertTrue(result['operator_required'])
                self.assertNotIn(b'private', data)
                self.assertNotIn(b'job-', data)
            self.assertEqual(self.app.handle('POST', '/recovery-summary', self.headers)[0], 404)
        after = {p.relative_to(self.store.root): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in self.store.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.runner.submit.assert_not_called()
        self.runner.recovery.assert_not_called()

    def test_historical_unowned_job_status_is_not_current_operator_attention(self):
        self.store.write('ownership.json', {'owners': {}, 'recoveries': []})
        result = strict_json(self.app.handle('GET', '/recovery-summary', self.headers)[2])
        self.assertFalse(result['operator_required'])
        self.assertEqual(result['incidents_7d'], 0)

    def test_legacy_owner_warning_and_healthy_controls_through_endpoint(self):
        self.store.write('ownership.json', {'owners': {'gpu1': {'job_id': 'owned'}}})
        for state, error, expected in (
                ('reconciling', 'supervisor_restart_unresolved_owner', True),
                ('running', None, False), ('cleanup_pending', None, False),
                ('reconciling', 'unrelated_error', False),
                ('failed', 'supervisor_restart_unresolved_owner', False)):
            with self.subTest(state=state, error=error):
                self.store.put_job({'request_id': 'owned', 'state': state, 'error_code': error})
                result = strict_json(self.app.handle('GET', '/recovery-summary', self.headers)[2])
                self.assertEqual(result['operator_required'], expected)
                self.assertEqual(result['incidents_7d'], 0)
        self.store.put_job({'request_id': 'owned', 'state': 'reconciling',
                            'error_code': 'supervisor_restart_unresolved_owner'})
        self.store.write('ownership.json', {'owners': {}})
        self.assertFalse(strict_json(self.app.handle(
            'GET', '/recovery-summary', self.headers)[2])['operator_required'])

    def test_endpoint_keeps_partial_reboot_warning_without_redundant_job_status(self):
        entries = [wall('released'), wall('retained', phase='operator_required')]
        for entry in entries:
            entry.update(kind='previous_boot', observed_boot=B1)
            entry['created_at'] = datetime.now(timezone.utc).isoformat()
        released, retained = entries
        self.store.put_job({'request_id': retained['identity']['job_id'], 'state': 'failed',
                            'error_code': 'guest_reboot_interrupted'})
        self.store.write('ownership.json', {
            'automatic_recovery': {e['id']: e for e in entries},
            'owners': {'gpu1': retained['identity']},
            'recoveries': [{'incident_id': released['id'], 'identity': released['identity'],
                            'observed_boot': B1, 'recovered_at': datetime.now(timezone.utc).isoformat()}]})
        with patch.object(self.store, 'write', side_effect=AssertionError('summary must be read-only')):
            result = strict_json(self.app.handle('GET', '/recovery-summary', self.headers)[2])
        self.assertEqual(result['incidents_7d'], 1)
        self.assertTrue(result['operator_required'])


if __name__ == '__main__':
    unittest.main()
