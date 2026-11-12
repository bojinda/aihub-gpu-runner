"""One-click contract, compact authority and real process/crash/lock regressions."""
import concurrent.futures,copy,hashlib,json,os,subprocess,sys,time,unittest
from pathlib import Path
from unittest.mock import patch
from aihub_gpu_runner.core import AtomicStore,NativeLock,RecoveryRequired
from aihub_gpu_runner.estop import Estop,compact_history,compact_state,digest
from tests import test_estop as fixture

class OneClick(unittest.TestCase):
    def setUp(self):
        self.f=fixture.EstopTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.control=self.f.control;self.store=self.f.store;self.a=self.f.a

    def test_two_direct_operations_no_history_scan_or_unrelated_gate_read(self):
        self.f.owned();before=self.a.snapshot();oldpath=self.a.lock_paths['gpu1']
        read=self.store._read_raw;names=[]
        def observed(name,default=None):
            names.append(str(name));self.assertNotEqual(str(name),'recovery-control.json')
            return read(name,default)
        with patch.object(self.store,'jobs',side_effect=AssertionError('history scan forbidden')),patch.object(self.store,'_read_raw',side_effect=observed):
            r=self.control.execute('admin');size=self.store.path('ownership.json').stat().st_size
            second=self.control.execute('admin');size2=self.store.path('ownership.json').stat().st_size
        self.assertEqual(r['outcome'],'success');self.assertEqual(r['released_leases'],1)
        self.assertEqual(second['outcome'],'success');self.assertEqual(second['released_leases'],0)
        self.assertLessEqual(abs(size2-size),20)
        state=self.a.snapshot();self.assertEqual(state['admission_generation']['sequence'],2)
        self.assertEqual(state['estop_revocations'],{})
        self.assertNotIn('estop_attempts',state);self.assertNotIn('estop_intents',state)
        self.assertLess(len(json.dumps(state['estop_last'])),180)
        self.assertFalse(NativeLock(oldpath,create=False).acquire(0))
        fresh=self.a.try_acquire('fresh','fresh',('gpu1',),'fixture');self.assertIsNotNone(fresh);fresh.close()

    def test_terminal_owned_result_needs_only_compact_revocation_metadata(self):
        self.f.owned(phase='success');raw=self.store.path('jobs/job.json').read_bytes()
        self.control.execute('admin');s=self.a.snapshot();record=s['estop_revocations']['job']
        self.assertNotIn('job_before',record);self.assertLess(len(json.dumps(record)),300)
        self.assertEqual(self.store.job('job')['state'],'failed')
        self.assertEqual(self.store.path('jobs/job.json').read_bytes(),raw)

    def test_unavailable_owner_job_is_not_a_workload_proof_prerequisite(self):
        self.f.owned();self.store.path('jobs/job.json').write_bytes(b'invalid-json')
        result=self.control.execute('admin')
        self.assertEqual(result['outcome'],'success');self.assertEqual(result['released_leases'],1)
        self.assertEqual(self.a.snapshot()['owners'],{})
        with self.assertRaises(RecoveryRequired):self.store.put_job(dict(request_id='job',request_hash='payload',state='waiting'))

    def test_updated_raw_writer_cannot_retag_old_job_or_restore_old_owner(self):
        self.f.owned();old=self.store.job('job');owners=self.a.snapshot()['owners'];self.control.execute('admin')
        old.update(admission_generation=self.a.snapshot()['admission_generation']['id'],state='success')
        with self.assertRaisesRegex(RecoveryRequired,'revoked'):AtomicStore(self.f.root).put_job(old)
        s=self.a.snapshot();s['owners']=owners
        for o in s['owners'].values():o['admission_generation']=s['admission_generation']['id']
        with self.assertRaisesRegex(RecoveryRequired,'revoked_owner'):self.store.write('ownership.json',s)

    def test_settled_historical_success_reads_survive_but_late_writes_do_not(self):
        lease=self.f.owned(phase='success')
        self.store.write('results/job.json',{'value':'accepted','artifact_available':False})
        lease.release({'completion_verified':True,'cleanup_verified':True});lease.close()
        self.control.execute('admin')
        self.assertEqual(self.store.job('job')['state'],'success')
        self.assertEqual(self.store.read('results/job.json')['value'],'accepted')
        with self.assertRaisesRegex(RecoveryRequired,'stale_job_generation'):
            AtomicStore(self.f.root).write('results/job.json',{'value':'late'})

    def test_two_simultaneous_admin_clicks_each_rotate_no_stale_ticket_restriction(self):
        self.f.owned()
        with concurrent.futures.ThreadPoolExecutor(2) as pool:results=list(pool.map(self.control.execute,('one','two')))
        self.assertEqual([r['outcome'] for r in results],['success','success'])
        self.assertEqual(sum(r['released_leases'] for r in results),1)
        self.assertEqual(self.a.snapshot()['admission_generation']['sequence'],2)

    def test_precommit_failure_and_postcommit_uncertainty_never_repeat_effect(self):
        for phase in ('gpu0_prepared','gpu1_prepared','namespace_durable','before_commit','authority_committed'):
            with self.subTest(phase=phase):
                old=self.a.snapshot()['admission_generation']['id']
                def fault(p):
                    if p==phase:raise OSError('synthetic failure')
                self.control.fault=fault;r=self.control.execute('admin')
                self.assertEqual(r['outcome'],'unknown' if phase=='authority_committed' else 'failure')
                self.assertEqual(self.a.snapshot()['admission_generation']['id']==old,phase!='authority_committed')
                raw=self.store.path('ownership.json').read_bytes();self.control.status()
                self.assertEqual(self.store.path('ownership.json').read_bytes(),raw)
        self.control.fault=lambda p:None

    def test_real_process_exit_before_and_after_atomic_commit(self):
        for phase in ('namespace_durable','authority_committed'):
            old=self.a.snapshot()['admission_generation']['id']
            p=subprocess.Popen([sys.executable,str(Path(fixture.__file__).resolve()),'--crash-writer',str(self.f.root),phase])
            self.assertEqual(p.wait(10),9)
            self.assertEqual(self.a.snapshot()['admission_generation']['id']==old,phase!='authority_committed')
            self.assertEqual(self.control.status()['runner_ownership'],'FREE')

    def test_rename_after_effect_unknown_and_optional_status_is_read_only(self):
        replace=os.replace
        def uncertain(src,dst):
            replace(src,dst)
            if str(dst).endswith('ownership.json'):raise OSError('flush acknowledgment lost')
        with patch('os.replace',side_effect=uncertain):r=self.control.execute('admin')
        self.assertEqual(r['outcome'],'unknown')
        self.assertEqual(self.a.snapshot()['admission_generation']['sequence'],1)
        self.assertEqual(self.control.status()['last']['outcome'],'success')

    def test_compaction_requires_exact_backup_and_preserves_every_non_estop_field(self):
        self.f.runner.close();s=self.a.snapshot();s.pop('estop_format');s.pop('estop_last')
        original={'request_id':'legacy','request_hash':'original','state':'reconciling','resources':['gpu1'],'target':'fixture'}
        # Disposable original bytes; this test does not read captured production data.
        self.store.path('jobs/legacy.json').write_bytes(json.dumps(original,sort_keys=True,separators=(',',':')).encode())
        s.update(estop_attempts={'old':{'committed_at':1,'revoked_leases':1,'maintenance_before':{'private':'x'*15_652_917}}},
          estop_intents={'unused':{}},estop_revocations={'legacy':dict(operation='old',generation='old',lease_id='lease',job_sha256=digest(original),job_before=original)})
        # Setup the legacy fixture as raw disposable installation bytes, not a production writer.
        self.store.path('ownership.json').write_bytes(json.dumps(s,sort_keys=True,separators=(',',':')).encode())
        raw=self.store.path('ownership.json').read_bytes();backup=self.f.root/'rollback.json';backup.write_bytes(raw)
        wanted=hashlib.sha256(raw).hexdigest()
        with self.assertRaises(RecoveryRequired):compact_history(self.a,backup,'0'*64,confirmed_stopped=True)
        result=compact_history(self.a,backup,wanted,confirmed_stopped=True)
        self.assertLess(result['after_bytes'],10_000)
        now=self.a.snapshot();ignore={'estop_attempts','estop_intents','estop_revocations','estop_format','estop_last','authority_revision'}
        self.assertEqual({k:v for k,v in s.items() if k not in ignore},{k:v for k,v in now.items() if k not in ignore})
        self.assertEqual(backup.read_bytes(),raw);self.assertEqual(self.store.job('legacy')['state'],'failed')
        with self.assertRaisesRegex(RecoveryRequired,'backup_or_state_changed'):compact_history(self.a,backup,hashlib.sha256(self.store.path('ownership.json').read_bytes()).hexdigest(),confirmed_stopped=True)
