"""Read historical JSON only; never import/execute any incident helper.

Authority runs only against TemporaryDirectory and new synthetic approval.
Historical observations are replay inputs, never fresh production evidence.
"""
import copy
import hashlib
import json
import unittest
from pathlib import Path
from tests.recovery_fixture import Fixture
from aihub_gpu_runner.recovery_control import RecoveryControl, Blocked

PACKET=Path(__file__).resolve().parents[2]/'tmp/wallpaper-incident-20261108'


@unittest.skipUnless(PACKET.exists(),'retained local incident evidence unavailable')
class RetainedEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.f=Fixture(self)
        self.inputs=['ownership.before.json','job.before.json','successor-v3/QUALIFICATION.json',
                     'successor-v3/FINAL-READBACK.json']
        self.hashes={name:hashlib.sha256((PACKET/name).read_bytes()).hexdigest() for name in self.inputs}
        self.addCleanup(self.immutable_inputs)
        state=json.loads((PACKET/self.inputs[0]).read_bytes())
        job=json.loads((PACKET/self.inputs[1]).read_bytes())
        # Only local physical paths/inodes change. Historical evidence untouched.
        state['lock_identity']=self.f.a.snapshot()['lock_identity']
        self.f.a.scope=state['scope'];self.f.boot=job['boot_id']
        self.f.store.path(self.f.store.job_name('fixture-wallpaper')).unlink()
        self.f.store.write('ownership.json',state);self.f.store.put_job(job)
        self.f.before=copy.deepcopy(state);self.f.job=copy.deepcopy(job)
        self.f.observation['boot']=job['boot_id']
        self.f.observation['proof']=json.loads((PACKET/self.inputs[2]).read_bytes())['preflight']['proof']
    def immutable_inputs(self):
        self.assertEqual({n:hashlib.sha256((PACKET/n).read_bytes()).hexdigest() for n in self.inputs},self.hashes)
    def test_actual_prestart_shape_passes_only_with_new_fixture_exclusion_and_approval(self):
        result=self.f.run()
        self.assertEqual(result['result'],'succeeded',result)
        final=json.loads((PACKET/self.inputs[3]).read_bytes())
        self.assertEqual(len(self.f.a.snapshot()['recoveries']),final['total_recovery_audits'])
        self.assertEqual(self.f.a.snapshot()['owners'],{})
        self.assertEqual(self.f.store.job(self.f.job['request_id'])['state'],final['job']['state'])
        self.assertEqual(self.f.a.snapshot()['automatic_recovery'],self.f.before['automatic_recovery'])
    def test_retained_cooperative_manual_access_is_not_permanent_exclusion(self):
        self.f.observation['isolation']['coverage']['local_gpu_exec']=False
        self.assertIn('producer_exclusion_incomplete',self.f.control.check('operator1')['failed_checks'])
        self.f.calls.assert_not_called()
    def test_consumed_incident_approval_is_not_a_reusable_ticket(self):
        final=json.loads((PACKET/self.inputs[3]).read_bytes())
        with self.assertRaises(Blocked):self.f.control.recover(final['disposition']['approval_ref'],'operator1',True)
        self.f.calls.assert_not_called()


if __name__=='__main__':unittest.main()
