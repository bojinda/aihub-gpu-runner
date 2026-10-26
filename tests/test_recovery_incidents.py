"""Exact display acknowledgment inventory; no journal or GPU effects."""
import copy
from datetime import timedelta
import hashlib
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aihub_gpu_runner.api import Application
from aihub_gpu_runner.core import AtomicStore, strict_json
from aihub_gpu_runner.recovery_summary import summarize_recovery
from tests.test_recovery_summary import NOW, B0, B1, wall, boot_record


class IncidentTests(unittest.TestCase):
    def inventory(self, state, at=NOW):
        return summarize_recovery(state, at=at, include_incidents=True)

    def test_stable_ids_and_rolling_expiry_do_not_require_count_subtraction(self):
        state = {'automatic_recovery': {'old': wall('old', 6), 'recent': wall('recent', 1)}}
        first = self.inventory(state)
        acknowledged = {a for i in first['incidents'] for a in i['aliases']}
        state['automatic_recovery']['new'] = wall('new', -1)
        later = self.inventory(state, NOW + timedelta(days=2))
        self.assertEqual(later['incidents_7d'], 2)
        self.assertEqual(sum(not acknowledged.intersection(i['aliases']) for i in later['incidents']), 1)

    def test_later_boot_classification_preserves_resolution_alias(self):
        entry = wall('same')
        state = {'automatic_recovery': {'a': entry}}
        old = self.inventory(state)['incidents'][0]
        audit = dict(entry['identity'], observed_boot=B1, recovered_at=NOW.isoformat())
        state['recoveries'] = [audit]
        state['boot_startups'] = {B1: boot_record()}
        new = self.inventory(state)['incidents'][0]
        self.assertNotEqual(old['id'], new['id'])
        self.assertTrue(set(old['aliases']).intersection(new['aliases']))

    def test_deduplication_warning_and_aggregate_contract_are_unchanged(self):
        entry = wall(phase='operator_required')
        state = {'automatic_recovery': {'a': entry, 'b': copy.deepcopy(entry)}}
        old = summarize_recovery(state, at=NOW)
        inventory = self.inventory(state)
        self.assertEqual({k: inventory[k] for k in old}, old)
        self.assertTrue(inventory['operator_required'])
        self.assertEqual(len(inventory['incidents']), 1)
        self.assertNotIn('private', str(inventory))
        for i in inventory['incidents']:
            self.assertEqual(len(i['id']), 64)
            self.assertIn(i['id'], i['aliases'])

    def test_incident_identity_does_not_depend_on_current_configuration_scope(self):
        state = {'scope': 'old', 'automatic_recovery': {'a': wall()}}
        old = self.inventory(state)
        state['scope'] = 'new'
        self.assertEqual(old, self.inventory(state))

    def test_authenticated_endpoint_has_no_writes_locks_or_backend_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AtomicStore(tmp)
            store.write('ownership.json', {'automatic_recovery': {'a': wall()}})
            runner = SimpleNamespace(store=store, submit=Mock(), recovery=Mock())
            app = Application(runner, 'synthetic-token-' + 'a' * 32)
            before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in store.root.rglob('*') if p.is_file()}
            with patch.object(store, 'write', side_effect=AssertionError('write')), \
                    patch('aihub_gpu_runner.core.NativeLock.acquire', side_effect=AssertionError('lock')), \
                    patch('subprocess.run', side_effect=AssertionError('GPU')), \
                    patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network')):
                self.assertEqual(app.handle('GET', '/recovery-incidents', {})[0], 401)
                code, _, body = app.handle('GET', '/recovery-incidents', {'Authorization': 'Bearer ' + app.token})
                self.assertEqual(code, 200)
                self.assertEqual(len(strict_json(body)['incidents']), 1)
                self.assertEqual(app.handle('POST', '/recovery-incidents', {'Authorization': 'Bearer ' + app.token})[0], 404)
            after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in store.root.rglob('*') if p.is_file()}
            self.assertEqual(before, after)
            runner.submit.assert_not_called(); runner.recovery.assert_not_called()
