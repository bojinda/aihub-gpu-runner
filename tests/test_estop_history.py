"""Desired outcomes for bulk history reads and the unchanged E-stop deadline."""
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

if os.environ.get('AIHUB_REQUIRE_INSTALLED_RUNNER')!='1':
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aihub_gpu_runner.core import NativeLock, RecoveryRequired
from tests.estop_history_fixture import history_fixture


class EstopHistoryTests(unittest.TestCase):
    def test_production_sized_history_retained_lease_and_three_no_effect_intents(self):
        with history_fixture() as f:
            state=f.admission.snapshot(); intents=state['estop_intents'].copy()
            files={p:p.read_bytes() for p in (f.root/'jobs').glob('*.json')}
            prepared={p:(p.stat().st_ino,p.read_bytes()) for p in (f.root/'generations').rglob('*.lock')}
            old_path=f.admission.lock_paths['gpu1']; counts=[]; original=f.store._read_raw
            def read(name,default=None):
                if name=='ownership.json':counts.append(1)
                return original(name,default)
            with patch.object(f.store,'_read_raw',side_effect=read):
                check=f.control.check('admin')
                self.assertLessEqual(len(counts),3)  # not one parse per historical job
                counts.clear(); begin=time.monotonic()
                result=f.control.execute(check['token'],'admin',True)
                elapsed=time.monotonic()-begin
                self.assertLess(len(counts),20)
            self.assertEqual(result['result'],'ownership_relinquished')
            self.assertLess(elapsed,3)
            self.assertEqual((result['revoked_leases'],result['revoked_requests']),(1,2))
            self.assertEqual(result['backend_operations'],0); self.assertFalse(result['replayed'])
            committed=f.store.path('ownership.json').read_bytes(); state=f.admission.snapshot()
            self.assertEqual(state['admission_generation']['sequence'],1)
            self.assertEqual(state['owners'],{}); self.assertEqual(len(state['estop_attempts']),1)
            for op,intent in intents.items():
                self.assertEqual(state['estop_intents'][op],intent)
                self.assertTrue(f.control.verify(op)['verified_no_effect'])
            self.assertEqual(f.store.path('ownership.json').read_bytes(),committed)
            self.assertEqual({p:(p.stat().st_ino,p.read_bytes()) for p in prepared},prepared)
            self.assertEqual({p:p.read_bytes() for p in files},files)
            probe=NativeLock(old_path,create=False)
            try:self.assertFalse(probe.acquire(0))  # original native lease still alive
            finally:probe.close()
            fresh=f.admission.try_acquire('fresh','fresh-payload',('gpu1',),'fixture')
            self.assertIsNotNone(fresh); fresh.close()
            with self.assertRaisesRegex(RecoveryRequired,'revoked'):
                f.store.put_job(dict(f.store.job('retained'),state='success'))
            self.assertEqual(f.control.verify(check['operation'])['result'],'ownership_relinquished')
            with self.assertRaisesRegex(RecoveryRequired,'duplicate'):
                f.control.execute(check['token'],'admin',True)

    def test_bulk_read_masks_revocation_committed_during_record_collection(self):
        with history_fixture(count=4,authority_bytes=0) as f:
            original=f.store._read_raw; injected=[]
            check=f.control.check('admin')
            def read(name,default=None):
                value=original(name,default)
                if name=='jobs/retained.json' and not injected:
                    injected.append(True)
                    f.control.execute(check['token'],'admin',True)
                return value
            with patch.object(f.store,'_read_raw',side_effect=read):
                jobs={j['request_id']:j for j in f.store.jobs()}
            self.assertTrue(jobs['retained']['authority_revoked'])
            self.assertEqual(jobs['retained']['state'],'failed')
            self.assertTrue(jobs['waiting']['authority_revoked'])

    def test_no_authority_cache_and_same_individual_and_bulk_views(self):
        with history_fixture(count=4,authority_bytes=0) as f:
            before={j['request_id']:j for j in f.store.jobs()}
            self.assertFalse(before['retained'].get('authority_revoked'))
            ticket=f.control.check('admin'); f.control.execute(ticket['token'],'admin',True)
            for job in f.store.jobs():self.assertEqual(job,f.store.job(job['request_id']))
            self.assertTrue(f.store.job('retained')['authority_revoked'])

    def test_invalid_and_missing_records_still_fail_closed(self):
        with history_fixture(count=4,authority_bytes=0) as f:
            path=f.store.path('jobs/retained.json'); path.write_bytes(b'{invalid')
            with self.assertRaisesRegex(RecoveryRequired,'stored_record_invalid'):f.store.jobs()
            path.write_bytes(b'null')
            with self.assertRaises(KeyError):f.store.jobs()

    def test_expired_transaction_still_prepares_but_never_commits(self):
        with history_fixture(count=4,authority_bytes=0) as f:
            ticket=f.control.check('admin'); clock=time.monotonic
            start=clock(); expired=[]
            f.control.fault=lambda phase:expired.append(True) if phase=='before_commit' else None
            # Model a slow durable preparation without slowing the suite or
            # changing wall-clock signed approval/expiry semantics.
            with patch('aihub_gpu_runner.estop.time.monotonic',side_effect=lambda:start+4 if expired else clock()):
                with self.assertRaisesRegex(RecoveryRequired,'estop_commit_deadline'):
                    f.control.execute(ticket['token'],'admin',True)
            state=f.admission.snapshot()
            self.assertEqual(state['admission_generation']['sequence'],0)
            self.assertEqual(len(state['estop_attempts']),0)
            self.assertEqual(len(state['estop_intents']),4)
            self.assertTrue(f.control.verify(ticket['operation'])['verified_no_effect'])


if __name__=='__main__':unittest.main()
