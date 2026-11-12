"""Current-generation bulk views and compact E-stop without historical scans."""
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
    def test_production_sized_history_retained_lease_and_compact_repeat(self):
        with history_fixture() as f:
            files={p:p.read_bytes() for p in (f.root/'jobs').glob('*.json')}
            old=f.admission.lock_paths['gpu1']
            with patch.object(f.store,'jobs',side_effect=AssertionError('E-stop history scan')):
                result=f.control.execute('admin');size=f.store.path('ownership.json').stat().st_size
                second=f.control.execute('admin');size2=f.store.path('ownership.json').stat().st_size
            self.assertEqual(result['released_leases'],1);self.assertEqual(second['released_leases'],0)
            self.assertLessEqual(abs(size2-size),20)
            self.assertEqual(f.admission.snapshot()['owners'],{})
            self.assertEqual({p:p.read_bytes() for p in files},files)
            probe=NativeLock(old,create=False);self.assertFalse(probe.acquire(0));probe.close()
            fresh=f.admission.try_acquire('fresh','fresh',('gpu1',),'fixture');self.assertIsNotNone(fresh);fresh.close()

    def test_bulk_read_masks_revocation_committed_during_record_collection(self):
        with history_fixture(count=4,authority_bytes=0) as f:
            original=f.store._read_raw; injected=[]
            def read(name,default=None):
                value=original(name,default)
                if name=='jobs/retained.json' and not injected:
                    injected.append(True)
                    f.control.execute('admin')
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
            f.control.execute('admin')
            for job in f.store.jobs():self.assertEqual(job,f.store.job(job['request_id']))
            self.assertTrue(f.store.job('retained')['authority_revoked'])
            # Missing originals are not reconstructed from copied payloads.
            f.store.path('jobs/retained.json').write_bytes(b'null')
            with self.assertRaises(KeyError): f.store.job('retained')
            with self.assertRaises(KeyError): f.store.jobs()
