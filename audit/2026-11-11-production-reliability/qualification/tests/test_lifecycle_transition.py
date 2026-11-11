"""Exact checkpoint migration, native locks, and scope-bound policy protection."""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from aihub_gpu_runner.config import admission_from_config, load_config
from aihub_gpu_runner.core import RecoveryRequired, encode
from aihub_gpu_runner.scope_transition import transition_wallpaper_on_demand
from tests import test_conditioning_transition as f


class LifecycleTransitionTests(unittest.TestCase):
    def setUp(self):
        f.ConditioningTransitionTests.setUp(self)
        value = json.loads(self.new.read_bytes())
        value['targets']['wallpaper'].update(lifecycle='on_demand', startup_timeout=60, shutdown_timeout=60)
        self.new.write_bytes(encode(value))
        self.evidence = self.make_evidence(self.old, self.new, 'synthetic-on-demand-forward')
    tearDown = f.prior.WallpaperTransitionTests.tearDown
    def make_evidence(self, old, new, approval):
        value = f.ConditioningTransitionTests.make_evidence(self, old, new, approval)
        value.pop('operator_approved_wallpaper_conditioning_transition')
        value.update(operator_approved_wallpaper_on_demand_transition=True, wallpaper_stopped=True)
        return value
    def test_atomic_checkpoint_transition_repeat_and_guarded_inverse_preserve_history_and_configs(self):
        original = self.old.read_bytes(), self.new.read_bytes()
        with patch('subprocess.run', side_effect=AssertionError('no backend actions')):
            result = transition_wallpaper_on_demand(self.old, self.new, self.evidence)
            self.assertTrue(result['journal_preserved'])
            self.assertTrue(transition_wallpaper_on_demand(self.old, self.new, self.evidence)['already_recorded'])
        _, targets, gate = admission_from_config(self.new)
        self.assertEqual(targets['wallpaper'].lifecycle, 'on_demand')
        self.assertEqual(gate.snapshot()['recoveries'], [{'approval_ref': 'earlier-recovery'}])
        evidence = self.make_evidence(self.new, self.old, 'synthetic-on-demand-reverse')
        evidence['rollback_of'] = self.evidence['approval_ref']
        transition_wallpaper_on_demand(self.new, self.old, evidence, rollback=True)
        _, _, restored = admission_from_config(self.old)
        self.assertEqual(restored.snapshot()['scope'], self.evidence['old_scope'])
        self.assertEqual(len(restored.snapshot()['scope_transitions']), 2)
        self.assertEqual(original, (self.old.read_bytes(), self.new.read_bytes()))
    def test_installed_policy_requires_separate_migration_not_erasure_or_scope_change(self):
        state = self.gate.snapshot()
        state['recovery_policy'] = {'sentinel': 'preserve'}
        self.gate.store.write('ownership.json', state)
        before = self.gate.store.path('ownership.json').read_bytes()
        with self.assertRaisesRegex(RecoveryRequired, 'separate_recovery_policy_migration'):
            transition_wallpaper_on_demand(self.old, self.new, self.evidence)
        self.assertEqual(before, self.gate.store.path('ownership.json').read_bytes())
    def test_other_settings_and_missing_deadlines_cannot_be_smuggled_into_transition(self):
        original = json.loads(self.new.read_bytes())
        for mutation in ('execution_timeout', 'startup_timeout'):
            value = copy.deepcopy(original)
            if mutation == 'startup_timeout':
                del value['targets']['wallpaper'][mutation]
            else:
                value['targets']['wallpaper'][mutation] += 1
            self.new.write_bytes(encode(value))
            with self.assertRaises(ValueError):
                transition_wallpaper_on_demand(self.old, self.new, self.evidence)
        self.new.write_bytes(encode(original))
    def test_retained_owner_or_active_supervisor_blocks_migration(self):
        lease = self.gate.try_acquire('held', 'hash', ('gpu0',), 'wallpaper')
        lease.close()
        with self.assertRaisesRegex(RecoveryRequired, 'resolved_idle_journal'):
            transition_wallpaper_on_demand(self.old, self.new, self.evidence)
