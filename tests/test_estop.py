"""E1 invariants. Real journal/flock/processes; no GPU/backend execution."""
import concurrent.futures,copy,importlib.util,json,os,subprocess,sys,tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
if os.environ.get('AIHUB_REQUIRE_INSTALLED_RUNNER')!='1':sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aihub_gpu_runner.core import Admission,AtomicStore,NativeLock,RecoveryRequired,Runner,encode,guest_boot_id
from aihub_gpu_runner.estop import Estop,activate_generations

def wait_for(test):
    end=time.monotonic()+8
    while time.monotonic()<end:
        if test():return
        time.sleep(.01)
    raise AssertionError('disposable_handshake_timeout')

def host_child(root,resource):
    from aihub_gpu_runner.host_stage import HostStage
    from aihub_gpu_runner.dual_gpu import switch_binding
    root=Path(root);assert root.name.startswith('estop-v2-')
    s=AtomicStore(root);a=Admission(s,{r:root/'physical-locks'/(r+'.lock') for r in ('gpu0','gpu1')},'synthetic',True)
    resources=('gpu0','gpu1') if resource=='gpu0+gpu1' else (resource,)
    t=SimpleNamespace(name='fixture-ollama',kind='ollama',resources=resources,mode_switch={'activation_approval_ref':'fixture'} if len(resources)==2 else None)
    class Switch:
        def activate(self):return dict(backend_ready=True,placement_verified=True,mode='dual',physical_gpus=['gpu0','gpu1'],switch_binding=switch_binding(t.mode_switch))
    stage=HostStage(a,resource,'host','payload',ollama_target=t,host_policy={'idle_memory_mb':475},probe=lambda:0,switch_controller=Switch())
    program="from pathlib import Path;import sys,time;p=Path(sys.argv[1]);(p/'child-ready').touch();\nwhile not (p/'finish').exists():time.sleep(.01)"
    try:stage.run([sys.executable,'-c',program,str(root)],dict(os.environ,AIHUB_GPU_DUAL_APPROVAL='fixture'))
    except RecoveryRequired as exc:(root/'host-result').write_text(str(exc))

def stale_writer(root):
    root=Path(root);assert root.name.startswith('estop-v2-')
    store=AtomicStore(root);cached=store.read('ownership.json');(root/'cached').touch()
    wait_for(lambda:(root/'write-now').exists())
    try:
        with NativeLock(root/'admission.lock').held():store.write('ownership.json',cached)
    except RecoveryRequired as exc:(root/'writer-result').write_text(str(exc))

def crash_writer(root,phase):
    root=Path(root);assert root.name.startswith('estop-v2-')
    store=AtomicStore(root);a=Admission(store,{r:root/'physical-locks'/(r+'.lock') for r in ('gpu0','gpu1')},'synthetic',True)
    ticket=(root/'ticket').read_text()
    control=Estop(SimpleNamespace(admission=a,store=store),b'fixture-key'*4,fault=lambda p:os._exit(9) if p==phase else None)
    control.execute(ticket,'admin',True)

def hold_coordination(root,name):
    root=Path(root);assert root.name.startswith('estop-v2-') and name in ('jobs.lock','admission.lock')
    with NativeLock(root/name).held():
        (root/'holder-ready').touch();wait_for(lambda:(root/'release-holder').exists())

class EstopTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='estop-v2-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=AtomicStore(self.root)
        self.paths={r:self.root/'physical-locks'/(r+'.lock') for r in ('gpu0','gpu1')}
        for p in self.paths.values():p.parent.mkdir(exist_ok=True);p.touch()
        self.a=Admission(self.store,self.paths,'synthetic',True)
        self.a.bootstrap(dict(approval_ref='fixture',bootstrap_id=self.a.snapshot()['bootstrap_id'],backend_quiescent=True,cleanup_verified=True))
        activate_generations(self.a,confirmed_coordinated_shutdown=True)
        self.runner=Runner(self.a,{}, {},workers=2,poll_interval=.005);self.addCleanup(self.runner.close)
        self.control=Estop(self.runner,b'fixture-key'*4)
    def owned(self,resources=('gpu1',),name='job',phase='running'):
        self.store.put_job(dict(request_id=name,request_hash='payload',resources=list(resources),state=phase,target='fixture'))
        lease=self.a.try_acquire(name,'payload',resources,'fixture');self.assertIsNotNone(lease);self.addCleanup(lease.close)
        job=self.store.job(name);job['lease_id']=lease.owner['lease_id'];self.store.put_job(job)
        return lease
    def execute(self):
        c=self.control.check('admin');return self.control.execute(c['token'],'admin',True)
    def test_gpu0_gpu1_and_pair_release_without_unlocking_old_fd(self):
        for resources in (('gpu0',),('gpu1',),('gpu0','gpu1')):
            lease=self.owned(resources,name='job'+str(len(self.a.snapshot()['estop_attempts'])))
            paths=dict(self.a.lock_paths);before={r:p.stat().st_ino for r,p in paths.items()}
            result=self.execute();self.assertTrue(result['verified']);self.assertEqual(result['backend_operations'],0)
            for r in resources:
                probe=NativeLock(paths[r],create=False);self.assertFalse(probe.acquire(0));self.assertEqual(paths[r].stat().st_ino,before[r])
            fresh=self.a.try_acquire('fresh'+str(len(self.a.snapshot()['estop_attempts'])),'p',resources,'fixture')
            self.assertIsNotNone(fresh);fresh.release({'completion_verified':True,'cleanup_verified':True});fresh.close();lease.close()
    def test_owner_free_always_rotates_even_with_unrecorded_current_lock(self):
        p=self.a.lock_paths['gpu1'];lock=NativeLock(p,create=False);self.assertTrue(lock.acquire(0));self.addCleanup(lock.close)
        g=self.a.snapshot()['admission_generation']['id'];r=self.execute()
        self.assertEqual(r['revoked_leases'],0);self.assertNotEqual(r['new_generation'],g)
        self.assertFalse(NativeLock(p,create=False).acquire(0));fresh=self.a.try_acquire('fresh','p',('gpu1',),'fixture');self.assertIsNotNone(fresh);fresh.close()
    def test_raw_updated_writes_and_result_reads_cannot_restore_revoked_success(self):
        self.owned();old=self.a.snapshot();job=self.store.job('job')
        self.store.write('results/job.json',{'value':'old-output','artifact_available':True});self.store.write_bytes('results/job.bin',b'old')
        original=self.store.path('jobs/job.json').read_bytes();self.execute()
        with self.assertRaisesRegex(RecoveryRequired,'stale_authority'):self.store.write('ownership.json',old)
        fresh_store=AtomicStore(self.root)
        with self.assertRaisesRegex(RecoveryRequired,'stale_authority'):fresh_store.write_bytes('ownership.json',encode(old))
        with self.assertRaisesRegex(RecoveryRequired,'stale_authority'):fresh_store.write(Path('jobs')/'..'/'ownership.json',old)
        job['state']='success'
        with self.assertRaisesRegex(RecoveryRequired,'revoked'):fresh_store.put_job(job)
        with self.assertRaisesRegex(RecoveryRequired,'revoked'):fresh_store.write_bytes('results/job.bin',b'late')
        self.assertEqual(self.store.path('jobs/job.json').read_bytes(),original)
        self.assertEqual(self.runner.status('job')['state'],'failed');self.assertIsNone(self.runner.result('job')['value'])
        self.assertFalse(self.store.read('results/job.json')['artifact_available'])
        with self.assertRaises(KeyError):self.runner.artifact('job')
        self.assertEqual(self.store.path('results/job.bin').read_bytes(),b'old')
        with self.assertRaisesRegex(RecoveryRequired,'revoked'):self.a.try_acquire('job','payload',('gpu1',),'fixture')
        current=self.a.snapshot();current['owners']=old['owners']
        with self.assertRaisesRegex(RecoveryRequired,'revoked_owner'):self.store.write('ownership.json',current)
        for owner in current['owners'].values():owner['admission_generation']=self.a.snapshot()['admission_generation']['id']
        with self.assertRaisesRegex(RecoveryRequired,'revoked_owner'):self.store.write('ownership.json',current)
        from aihub_gpu_runner.api import Application
        with self.a.mutex.held():
            current=self.a.snapshot();current['ready']=False;self.store.write('ownership.json',current)
        self.runner.futures['job']=concurrent.futures.Future()
        status=Application(self.runner,'fixture-token-disposable-long-0123456789')._status(self.runner.status('job'))
        self.assertTrue(status['resources_released']);self.assertFalse(status['supervision_active']);self.assertTrue(status['old_work_may_continue'])
    def test_waiting_and_retained_terminal_owners_are_abandoned_without_replay(self):
        self.owned(phase='success')
        self.store.put_job(dict(request_id='waiting',request_hash='w',state='waiting',resources=['gpu0'],target='fixture'))
        r=self.execute();self.assertEqual(r['revoked_requests'],2)
        self.assertTrue(all(j['authority_revoked'] and j['state']=='failed' for j in self.store.jobs()))
    def test_stale_confirmation_and_duplicate_post_single_audit(self):
        c=self.control.check('admin');self.owned()
        with self.assertRaisesRegex(RecoveryRequired,'stale_estop_binding'):self.control.execute(c['token'],'admin',True)
        c=self.control.check('admin');r=self.control.execute(c['token'],'admin',True)
        with self.assertRaisesRegex(RecoveryRequired,'duplicate'):self.control.execute(c['token'],'admin',True)
        self.assertEqual(self.control.verify(r['operation'])['new_generation'],r['new_generation'])
        self.assertEqual(len(self.a.snapshot()['estop_attempts']),1)
    def test_concurrent_distinct_confirmations_cannot_commit_same_binding_twice(self):
        cs=[self.control.check('admin') for _ in range(2)]
        def run(c):
            try:return self.control.execute(c['token'],'admin',True)['result']
            except RecoveryRequired:return 'stale'
        with concurrent.futures.ThreadPoolExecutor(2) as pool:results=list(pool.map(run,cs))
        self.assertEqual(sorted(results),['ownership_relinquished','stale']);self.assertEqual(len(self.a.snapshot()['estop_attempts']),1)
    def test_failure_phases_have_exact_verify_and_no_duplicate_mutation(self):
        for phase in ('intent_persisted','gpu0_prepared','gpu1_prepared','namespace_durable','before_commit','authority_committed'):
            c=self.control.check('admin');old=self.a.snapshot()['admission_generation']['id']
            def fault(p):
                if p==phase:raise OSError('synthetic failure')
            self.control.fault=fault
            r=self.control.execute(c['token'],'admin',True);self.assertEqual(r['result'],'uncertain')
            v=self.control.verify(c['operation'])
            self.assertEqual(v['result'],'ownership_relinquished' if phase=='authority_committed' else 'not_committed')
            self.assertEqual(self.a.snapshot()['admission_generation']['id']==old,phase!='authority_committed')
            before=self.store.path('ownership.json').read_bytes();self.control.verify(c['operation']);self.assertEqual(self.store.path('ownership.json').read_bytes(),before)
            with self.assertRaisesRegex(RecoveryRequired,'duplicate'):self.control.execute(c['token'],'admin',True)
        self.control.fault=lambda p:None
    def test_rename_after_effect_error_is_verified_not_replayed(self):
        real=os.replace;writes=[]
        def replace(src,dst):
            real(src,dst)
            if str(dst).endswith('ownership.json'):
                writes.append(1)
                if len(writes)==2:raise OSError('lost directory flush acknowledgement')
        c=self.control.check('admin')
        with patch('os.replace',side_effect=replace):r=self.control.execute(c['token'],'admin',True)
        self.assertEqual(r['result'],'uncertain');self.assertEqual(self.control.verify(c['operation'])['result'],'ownership_relinquished')
        self.assertEqual(len(self.a.snapshot()['estop_attempts']),1)
    def test_existing_recovery_fences_are_historical_not_new_generation_blocks(self):
        with self.a.mutex.held():
            s=self.a.snapshot();s['practical_control']=dict(admission='closed',active='old-op',attempts={'old-op':{'status':'uncertain'}});self.store.write('ownership.json',s)
        self.store.write('recovery-control.json',dict(schema=1,mode='closed',active='old-op',attempts={'old-op':{}},permits={'old':{}},epoch=1,admission_generation=self.a.snapshot()['admission_generation']['id']))
        self.assertTrue(self.a.maintenance_blocked());self.execute();self.assertFalse(self.a.maintenance_blocked())
        self.assertEqual(self.a.snapshot()['practical_control']['attempts']['old-op']['status'],'uncertain')
        self.assertEqual(self.store._read_raw('recovery-control.json')['mode'],'closed')
    def test_changed_boot_actor_and_confirmation_are_refused(self):
        c=self.control.check('admin')
        with self.assertRaises(RecoveryRequired):self.control.execute(c['token'],'other',True)
        with self.assertRaises(RecoveryRequired):self.control.execute(c['token'],'admin',False)
        original=self.a.boot_reader;self.a.boot_reader=lambda:'different'
        with self.assertRaises(RecoveryRequired):self.control.execute(c['token'],'admin',True)
        self.a.boot_reader=original
    def test_real_busy_mutex_is_named_bounded_and_releases_prefix(self):
        for name in ('jobs.lock','admission.lock'):
            for f in ('holder-ready','release-holder'):
                if (self.root/f).exists():(self.root/f).unlink()
            p=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--hold-coordination',str(self.root),name])
            try:
                wait_for(lambda:(self.root/'holder-ready').exists());before=self.store.path('ownership.json').read_bytes();start=time.monotonic()
                with self.assertRaisesRegex(RecoveryRequired,'estop_jobs_busy' if name=='jobs.lock' else 'estop_admission_busy'):self.control.check('admin')
                self.assertLess(time.monotonic()-start,4.5);self.assertEqual(self.store.path('ownership.json').read_bytes(),before)
            finally:(self.root/'release-holder').touch();self.assertEqual(p.wait(10),0)
            probe=NativeLock(self.root/'jobs.lock');self.assertTrue(probe.acquire(0));probe.close()
    def test_expired_or_modified_ticket_and_verify_do_not_mutate(self):
        self.control.clock=lambda:0;c=self.control.check('admin');self.control.clock=lambda:61
        before=self.store.path('ownership.json').read_bytes()
        with self.assertRaises(RecoveryRequired):self.control.execute(c['token'],'admin',True)
        with self.assertRaises(RecoveryRequired):self.control.execute(c['token']+'x','admin',True)
        self.assertTrue(self.control.verify('0'*32)['verified_no_effect'])
        self.assertEqual(self.store.path('ownership.json').read_bytes(),before)
    def test_phase_progress_does_not_invalidate_exact_lease_approval(self):
        lease=self.owned();c=self.control.check('admin');lease.mark('cleanup_pending')
        result=self.control.execute(c['token'],'admin',True);self.assertEqual(result['revoked_leases'],1)
    def test_activation_refuses_live_supervisor_host_or_original_resource_lock(self):
        for name in ('supervisor.lock','host-fixture.lock','physical-locks/gpu0.lock'):
            with tempfile.TemporaryDirectory(prefix='estop-activation-') as tmp:
                store=AtomicStore(tmp);paths={r:Path(tmp)/'physical-locks'/(r+'.lock') for r in ('gpu0','gpu1')}
                for p in paths.values():p.parent.mkdir(exist_ok=True);p.touch()
                a=Admission(store,paths,'fixture',True);a.bootstrap(dict(approval_ref='fixture',bootstrap_id=a.snapshot()['bootstrap_id'],backend_quiescent=True,cleanup_verified=True))
                lock=NativeLock(store.path(name));self.assertTrue(lock.acquire(0));before=store.path('ownership.json').read_bytes()
                try:
                    with self.assertRaises(TimeoutError):activate_generations(a,confirmed_coordinated_shutdown=True)
                    self.assertEqual(store.path('ownership.json').read_bytes(),before)
                finally:lock.close()
    def test_current_sdk_cached_writer_is_fenced_in_independent_process(self):
        p=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--stale-writer',str(self.root)])
        try:
            wait_for(lambda:(self.root/'cached').exists());self.execute()
            committed=self.store.path('ownership.json').read_bytes();(self.root/'write-now').touch();self.assertEqual(p.wait(10),0)
            self.assertIn('stale_authority_revision',(self.root/'writer-result').read_text())
            self.assertEqual(self.store.path('ownership.json').read_bytes(),committed)
        finally:
            (self.root/'write-now').touch()
            if p.poll() is None:p.wait(10)
    def test_actual_process_exit_before_and_after_commit_is_verified(self):
        for phase in ('namespace_durable','authority_committed'):
            c=self.control.check('admin');(self.root/'ticket').write_text(c['token'])
            p=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--crash-writer',str(self.root),phase])
            self.assertEqual(p.wait(10),9)
            result=self.control.verify(c['operation'])
            self.assertEqual(result['result'],'not_committed' if phase=='namespace_durable' else 'ownership_relinquished')
    def test_real_runner_fresh_work_finishes_while_revoked_work_keeps_running(self):
        from aihub_gpu_runner.backends import Target,Execution
        self.runner.close();entered=threading.Event();finish=threading.Event();calls=[]
        class Backend:
            def validate(self,*args):pass
            def execute(self,operation,payload,deadline,progress):
                calls.append(payload['prompt'])
                if payload['prompt']=='old':entered.set();finish.wait(8)
                return Execution({'response':payload['prompt']},{})
            def cleanup(self,*args):return {'completion_verified':True,'cleanup_verified':True}
        target=Target('ollama','ollama',('gpu1',),'http://127.0.0.1:1',models=('fixture',))
        self.runner=Runner(self.a,{'ollama':target},{'ollama':Backend()},workers=2,poll_interval=.005)
        self.addCleanup(self.runner.close);self.addCleanup(finish.set);self.control=Estop(self.runner,b'fixture-key'*4)
        self.runner.submit('old','ollama','generate',{'model':'fixture','prompt':'old'});self.assertTrue(entered.wait(3))
        old=self.runner.future('old');paths=dict(self.a.lock_paths);self.execute()
        self.assertTrue(old.running());self.assertFalse(finish.is_set());self.assertFalse(NativeLock(paths['gpu1'],create=False).acquire(0))
        self.runner.submit('fresh','ollama','generate',{'model':'fixture','prompt':'fresh'})
        self.runner.future('fresh').result(3);self.assertEqual(self.runner.result('fresh')['value']['response'],'fresh')
        self.assertTrue(old.running());self.assertEqual(self.runner.result('old')['state'],'failed')
        finish.set();wait_for(old.done);self.assertEqual(calls,['old','fresh'])
    def test_helper_and_music_generation_trust_preserves_original_binding(self):
        from aihub_gpu_runner.owned_stop_helper import compatibility
        with self.a.mutex.held():
            s=self.a.snapshot();s['verified_boot']=guest_boot_id();s['recovery_policy']={'policy':{'scope':'synthetic','wallpaper':'fixture-container'}};self.store.write('ownership.json',s)
        config=dict(state_dir=str(self.root),scope='synthetic',container_id='fixture-container',lock_paths={r:str(p) for r,p in self.paths.items()})
        compatibility(config);self.execute();self.assertEqual(compatibility(config)['schema'],2)
        module=Path(__file__).resolve().parents[1]/'components/music-cleanup/aihub_music_cleanup.py'
        spec=importlib.util.spec_from_file_location('music_fixture',module);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        cfg={'scope':'synthetic','gpu0_identity':self.a.snapshot()['admission_generation']['origin']['lock_identity']['gpu0']}
        path,_=m.cleanup_lock(self.root,cfg,self.a.snapshot());fd=os.open(path,os.O_RDONLY)
        try:
            m.check_owner(self.root,cfg,fd);self.execute()
            with self.assertRaises(m.Skip):m.check_owner(self.root,cfg,fd)
        finally:os.close(fd)
    def test_actual_music_hook_uses_current_lock_and_keeps_budget_and_saved_output(self):
        module=Path(__file__).resolve().parents[1]/'components/music-cleanup/aihub_music_cleanup.py'
        spec=importlib.util.spec_from_file_location('music_native_fixture',module);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        self.execute();config=dict(enabled=True,service='comfyui-music',budget_seconds=5,idle_ceiling_mib=475,
            scope='synthetic',gpu0_identity=self.a.snapshot()['admission_generation']['origin']['lock_identity']['gpu0'])
        cp=self.root/'music-config.json';cp.write_text(json.dumps(config));out=self.root/'audio';out.mkdir();(out/'saved.mp3').write_bytes(b'fixture-saved-audio')
        queue=SimpleNamespace(mutex=threading.Lock(),queue=[],currently_running={},flags={});calls=[]
        executor=SimpleNamespace(success=True,history_result={'outputs':{'save':{'audio':[{'filename':'saved.mp3','type':'output'}]}}},reset=lambda:calls.append('reset'))
        model=SimpleNamespace(get_torch_device=lambda:SimpleNamespace(type='cpu'),unload_all_models=lambda:calls.append('unload'),soft_empty_cache=lambda:calls.append('cache'))
        item=(0,'fixture-prompt',{'save':{'class_type':'SaveAudioMP3'}},{'client_id':'fixture-ui','extra_pnginfo':{'workflow':{}}})
        result=m.cleanup(queue,executor,item,out,model,config_path=cp,state_root=self.root)
        self.assertEqual(result['outcome'],'completed',result);self.assertEqual(calls,['unload','reset','cache']);self.assertTrue(result['within_budget'])
        self.assertEqual((out/'saved.mp3').read_bytes(),b'fixture-saved-audio')
        config['budget_seconds']=6;cp.write_text(json.dumps(config));result=m.cleanup(queue,executor,item,out,model,config_path=cp,state_root=self.root)
        self.assertEqual(result['reason'],'invalid_budget');self.assertEqual(len(calls),3)
    def test_real_running_hoststage_gpu0_survives_estop(self):self._host_case('gpu0')
    def test_real_running_hoststage_gpu1_survives_estop(self):self._host_case('gpu1')
    def test_real_running_hoststage_pair_survives_estop(self):self._host_case('gpu0+gpu1')
    def _host_case(self,resource):
        p=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--host-child',str(self.root),resource],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            wait_for(lambda:(self.root/'child-ready').exists() or p.poll() is not None)
            self.assertIsNone(p.poll());oldpaths=dict(self.a.lock_paths);self.execute();self.assertIsNone(p.poll())
            resources=('gpu0','gpu1') if resource=='gpu0+gpu1' else (resource,)
            for r in resources:self.assertFalse(NativeLock(oldpaths[r],create=False).acquire(0))
            lease=self.a.try_acquire('fresh','p',resources,'fixture');self.assertIsNotNone(lease);lease.close()
            (self.root/'finish').touch();out,err=p.communicate(timeout=10);self.assertEqual(p.returncode,0,(out+err).decode())
            self.assertEqual(self.store.job('host')['state'],'failed')
        finally:
            (self.root/'finish').touch()
            if p.poll() is None:p.communicate(timeout=10)

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--host-child':host_child(*sys.argv[2:])
    elif len(sys.argv)>1 and sys.argv[1]=='--stale-writer':stale_writer(sys.argv[2])
    elif len(sys.argv)>1 and sys.argv[1]=='--crash-writer':crash_writer(*sys.argv[2:])
    elif len(sys.argv)>1 and sys.argv[1]=='--hold-coordination':hold_coordination(*sys.argv[2:])
    else:unittest.main()
