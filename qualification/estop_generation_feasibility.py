"""E0-only executable experiment. NOT an E-stop implementation or service API.

Uses actual Admission/HostStage plus real Linux flock/processes in a TemporaryDirectory.
The small schema-2 reader below is an experimental adapter, not deployable runtime.
GPU/backend operations are never invoked. CPU worker/switch observations are synthetic.
"""
import argparse,copy,hashlib,json,os,subprocess,sys,tempfile,time,unittest,uuid
from pathlib import Path

from aihub_gpu_runner.core import AtomicStore,Admission,NativeLock,RecoveryRequired,encode,lock_identity,guest_boot_id

class ExperimentalGenerationReader(Admission):
    def _validate(self,state):
        if isinstance(state,dict) and state.get('schema')==2:
            g=state['admission_generation']
            base=self.store.root/'generations'/g['id']
            self.lock_paths={r:base/(r+'.lock') for r in ('gpu0','gpu1')}
            assert all(str(p)==g['paths'][r] for r,p in self.lock_paths.items())
            old=dict(state,schema=1)
            super()._validate(old)
            return state
        return super()._validate(state)

def wait_for(predicate):
    end=time.monotonic()+12
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.01)
    raise AssertionError('disposable_process_handshake_timeout')

def experimental_switch(store,paths,point=None):
    """One-file authority experiment; deliberately has no HTTP/auth/action API."""
    assert store.root.parent.name.startswith('estop-e0-')
    with NativeLock(store.path('jobs.lock')).held(),NativeLock(store.path('admission.lock')).held():
        before=store.read('ownership.json');g=uuid.uuid4().hex
        directory=store.root/'generations'/g;directory.mkdir(parents=True,mode=0o700)
        identities={};current={}
        for r in sorted(paths):
            p=directory/(r+'.lock');fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600)
            try:os.fsync(fd);identities[r]=lock_identity(os.fstat(fd))
            finally:os.close(fd)
            current[r]=str(p)
        for d in (directory,directory.parent,store.root):
            fd=os.open(d,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)
        if point=='prepared':raise RuntimeError('injected_before_authority_commit')
        out=copy.deepcopy(before);old_owners=out['owners'];out['owners']={};out['schema']=2
        out['lock_identity']=identities
        out['admission_generation']={'id':g,'paths':current,'previous':before.get('admission_generation')}
        out.setdefault('estop_revocations',{}).update({o['job_id']:{'owner':o,'error_code':'administrator_estop_no_replay'} for o in old_owners.values()})
        out.setdefault('estop_audits',[]).append({'id':g,'owners':old_owners,'physical_execution':'NOT_INSPECTED_OR_STOPPED'})
        store.write('ownership.json',out)
        if point=='committed':raise RuntimeError('injected_lost_response_after_commit')
        return out

def child(root,kind,resource):
    root=Path(root);assert root.name.startswith('estop-e0-')
    store=AtomicStore(root/'state');paths={r:root/(r+'.lock') for r in ('gpu0','gpu1')}
    a=Admission(store,paths,'synthetic-estop-scope',require_existing_locks=True)
    if kind=='host':
        from types import SimpleNamespace
        from aihub_gpu_runner.host_stage import HostStage
        from aihub_gpu_runner.dual_gpu import switch_binding
        resources=('gpu0','gpu1') if resource=='gpu0+gpu1' else (resource,)
        target=SimpleNamespace(name='fixture-ollama',kind='ollama',resources=resources,mode_switch={'synthetic':True,'activation_approval_ref':'disposable-fixture-only'} if len(resources)==2 else None)
        class FakeSwitch:
            def activate(self):return dict(backend_ready=True,placement_verified=True,mode='dual',physical_gpus=['gpu0','gpu1'],switch_binding=switch_binding(target.mode_switch))
        stage=HostStage(a,resource,'old-job','payload',ollama_target=target,host_policy={'idle_memory_mb':475},probe=lambda:0,switch_controller=FakeSwitch())
        script="from pathlib import Path;import sys,time;p=Path(sys.argv[1]);(p/'ready').write_text('alive');\nwhile not (p/'finish').exists():time.sleep(.01)"
        try:stage.run([sys.executable,'-c',script,str(root)],dict(os.environ,AIHUB_GPU_DUAL_APPROVAL='disposable-fixture-only'),timeout=5)
        except RecoveryRequired as exc:(root/'late-result').write_text(str(exc))
    else:
        resources=('gpu0','gpu1') if resource=='gpu0+gpu1' else (resource,)
        lease=a.try_acquire('old-job','payload',resources,'synthetic-'+resource)
        assert lease
        (root/'ready').write_text('alive')
        wait_for(lambda:(root/'finish').exists())
        try:lease.mark('late-success')
        except RecoveryRequired as exc:(root/'late-result').write_text(str(exc))
        finally:lease.close()

class Feasibility(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='estop-e0-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=AtomicStore(self.root/'state')
        self.paths={r:self.root/(r+'.lock') for r in ('gpu0','gpu1')}
        for p in self.paths.values():p.touch(mode=0o600)
        self.a=Admission(self.store,self.paths,'synthetic-estop-scope',require_existing_locks=True)
        self.a.bootstrap({'approval_ref':'synthetic-fixture-only','bootstrap_id':self.a.snapshot()['bootstrap_id'],'backend_quiescent':True,'cleanup_verified':True})
    def held_case(self,kind,resource):
        p=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--child',str(self.root),kind,resource],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        def finish():
            (self.root/'finish').touch()
            try:p.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill();p.communicate();raise AssertionError('fixture_child_did_not_exit')
        self.addCleanup(finish)
        wait_for(lambda:(self.root/'ready').exists() or p.poll() is not None)
        self.assertIsNone(p.poll(),p.stderr.read().decode() if p.poll() is not None else '')
        old={r:lock_identity(path.stat()) for r,path in self.paths.items()}
        out=experimental_switch(self.store,self.paths)
        self.assertIsNone(p.poll());self.assertEqual(out['owners'],{})
        held=('gpu0','gpu1') if resource=='gpu0+gpu1' else (resource,)
        for r in held:
            lock=NativeLock(self.paths[r],create=False);self.assertFalse(lock.acquire(0))
            self.assertEqual(lock_identity(self.paths[r].stat()),old[r])
        new=ExperimentalGenerationReader(self.store,self.paths,'synthetic-estop-scope',require_existing_locks=True)
        lease=new.try_acquire('new-job','different-payload',held,'synthetic-new')
        self.assertIsNotNone(lease);self.assertIsNone(p.poll())
        print(json.dumps({'case':kind+'-'+resource,'old_holder_pid':p.pid,
              'old_holder_alive':p.poll() is None,'old_lock_identities':{r:old[r] for r in held},
              'new_generation':out['admission_generation']['id'],
              'new_lock_identities':{r:out['lock_identity'][r] for r in held},
              'new_admission_acquired':True,'backend_operations':0}),flush=True)
        with self.assertRaises(RecoveryRequired):self.a.try_acquire('legacy-new','p',held,'legacy')
        (self.root/'finish').touch();stdout,stderr=p.communicate(timeout=15)
        self.assertEqual(p.returncode,0,(stdout+stderr).decode())
        self.assertIn('ownership_invalid_or_scope_changed',(self.root/'late-result').read_text())
        self.assertEqual(new.snapshot()['owners'][held[0]]['job_id'],'new-job')
        lease.close()
    def test_live_gpu0_lock(self):self.held_case('lease','gpu0')
    def test_live_gpu1_lock(self):self.held_case('lease','gpu1')
    def test_live_paired_lock(self):self.held_case('lease','gpu0+gpu1')
    def test_actual_hoststage_gpu0(self):self.held_case('host','gpu0')
    def test_actual_hoststage_gpu1(self):self.held_case('host','gpu1')
    def test_actual_hoststage_paired(self):self.held_case('host','gpu0+gpu1')
    def test_prepared_is_not_authority(self):
        before=self.store.path('ownership.json').read_bytes()
        with self.assertRaisesRegex(RuntimeError,'before_authority'):experimental_switch(self.store,self.paths,'prepared')
        self.assertEqual(self.store.path('ownership.json').read_bytes(),before)
    def test_committed_lost_response_is_readback(self):
        with self.assertRaisesRegex(RuntimeError,'lost_response'):experimental_switch(self.store,self.paths,'committed')
        state=self.store.read('ownership.json');self.assertEqual(state['schema'],2)
        self.assertEqual(len(state['estop_audits']),1)
        current=ExperimentalGenerationReader(self.store,self.paths,'synthetic-estop-scope',require_existing_locks=True)
        lease=current.try_acquire('new-job','p',('gpu1',),'synthetic');self.assertIsNotNone(lease);lease.close()
    def test_legacy_job_writes_need_authoritative_overlay(self):
        lease=self.a.try_acquire('old-job','payload',('gpu1',),'synthetic')
        self.store.put_job({'request_id':'old-job','request_hash':'payload','state':'running','lease_id':lease.owner['lease_id']})
        state=experimental_switch(self.store,self.paths)
        # Actual old AtomicStore accepts this. A raw job file CANNOT be authority.
        self.store.put_job({'request_id':'old-job','request_hash':'payload','state':'success','lease_id':lease.owner['lease_id']})
        self.assertEqual(self.store.job('old-job')['state'],'success')
        self.assertIn('old-job',state['estop_revocations'])
        self.assertEqual(state['estop_revocations']['old-job']['error_code'],'administrator_estop_no_replay')
        lease.close()

    def test_old_executor_capacity_is_not_released_by_journal_switch(self):
        import concurrent.futures,threading
        finish=threading.Event();entered=[threading.Event() for _ in range(4)]
        pool=concurrent.futures.ThreadPoolExecutor(max_workers=4)
        def old_work(i):entered[i].set();finish.wait(5)
        try:
            old=[pool.submit(old_work,i) for i in range(4)]
            for flag in entered:self.assertTrue(flag.wait(2))
            experimental_switch(self.store,self.paths)
            fresh=pool.submit(lambda:'fresh')
            self.assertFalse(fresh.done())
            replacement=concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:self.assertEqual(replacement.submit(lambda:'fresh').result(timeout=2),'fresh')
            finally:replacement.shutdown()
        finally:finish.set();pool.shutdown()

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--child':child(*sys.argv[2:])
    else:unittest.main(verbosity=2)
