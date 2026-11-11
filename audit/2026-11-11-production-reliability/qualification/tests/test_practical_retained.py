"""Retained JSON inputs only; no historical scripts or live target effects."""
import copy
import hashlib
import json
from pathlib import Path
import unittest

from aihub_gpu_runner.practical_recovery import PracticalRecovery
from tests.test_practical_recovery import PracticalFixture

ROOT=Path(__file__).resolve().parents[1]
PACKET=ROOT.parent/'tmp/wallpaper-incident-20261108'


class RetainedPracticalTests(unittest.TestCase):
    def test_latest_actual_completed_run_is_not_a_prestart_failure(self):
        capture=json.loads((ROOT/'qualification/latest-wallpaper.json').read_bytes())
        job=capture['job']
        self.assertEqual(job['state'],'success')
        self.assertEqual(job['wallpaper_lifecycle']['phase'],'released')
        self.assertFalse(PracticalRecovery.never_started(job))
        self.assertTrue(capture['output_verification']['output_matches_runner'])
        self.assertEqual(capture['artifact']['sha256'],job['wallpaper_lifecycle']['artifact_sha256'])

    @unittest.skipUnless(PACKET.exists(),'retained incident inputs unavailable')
    def test_real_nov8_shape_auto_retires_in_disposable_fixture_at_454_mib(self):
        paths=[PACKET/'ownership.before.json',PACKET/'job.before.json']
        before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        f=PracticalFixture(self)
        state=json.loads(paths[0].read_bytes());job=json.loads(paths[1].read_bytes())
        state['lock_identity']=f.a.snapshot()['lock_identity']
        state['practical_control']=f.a.snapshot()['practical_control']
        f.a.scope=state['scope'];f.boot=job['boot_id'];f.target.obs['boot']=job['boot_id']
        f.store.path(f.store.job_name(f.job['request_id'])).unlink()
        f.store.write('ownership.json',state);f.store.put_job(job);f.job=copy.deepcopy(job)
        f.target.obs.update(music_memory_mib=454,readiness={'ready':False,'reason':'unchanged startup policy'})
        result=f.control.automatic()
        self.assertEqual(result['result'],'released_readiness_pending')
        self.assertEqual(f.a.snapshot()['owners'],{})
        self.assertEqual(f.a.snapshot()['recoveries'][:-1],state['recoveries'])
        self.assertEqual(f.store.job(job['request_id'])['wallpaper_lifecycle'],job['wallpaper_lifecycle'])
        self.assertEqual(f.target.calls,[])
        self.assertEqual(before,{p:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
