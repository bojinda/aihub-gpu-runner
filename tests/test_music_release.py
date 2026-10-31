"""Candidate Music release regressions: fake transports/clock/GPU, real private journals/locks."""
import copy
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from aihub_gpu_runner.backends import ComfyBackend, Target, Execution, CleanupPending, Uncertain
from aihub_gpu_runner.core import Admission, AtomicStore, Runner, NativeLock, RecoveryRequired, Conflict
from aihub_gpu_runner.music_cleanup import MusicReleaseGuard
from aihub_gpu_runner.recovery import RecoveryObserver, RecoveryManager, digest
from tests.test_automatic_recovery import policy, BOOT1, BOOT2, Crash
from tests.test_container_contract import metadata
from tests.test_wallpaper_conditioning import GPU0, GPU1, WORKFLOW


class Clock:
    t = 0.
    def monotonic(self): return self.t
    def sleep(self, n): self.t += n


class Fixture:
    def __init__(self, test):
        self.test = test
        temp = tempfile.TemporaryDirectory(prefix='music-release-offline-')
        test.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.clock = Clock()
        self.boot = BOOT1
        self.store = AtomicStore(self.root/'state')
        self.a = Admission(self.store, {r:self.root/(r+'.lock') for r in ('gpu0','gpu1')},
                           'offline-music', boot_reader=lambda:self.boot)
        self.a.bootstrap({'approval_ref':'OFFLINE','bootstrap_id':self.a.snapshot()['bootstrap_id'],
                          'backend_quiescent':True,'cleanup_verified':True})
        self.music = Target('music','comfy',('gpu0',),'http://offline.invalid',idle_memory_mb=1200,
                            cleanup_samples=3, cleanup_timeout=60)
        self.wall = Target('wallpaper','comfy',('gpu0','gpu1'),'http://offline.invalid',workflow=WORKFLOW,
                           idle_memory_mb=1200,conditioning_idle_memory_mb=64,lifecycle='on_demand')
        self.ollama = Target('ollama','ollama',('gpu1',),'http://offline.invalid',models=('offline',))
        self.targets = {'music':self.music,'wallpaper':self.wall,'ollama':self.ollama}
        self.metas = {s:metadata(s,c) for s,c in [('wallpaper','a'),('music','b'),('ollama','c')]}
        for s,pid in [('music',202),('ollama',303)]: self.metas[s]['State'].update(Running=True,Pid=pid)
        self.p = policy(self.a)
        self.p.update(boot_startup='stopped_idle',samples=3,stopped_idle_memory_mb={'gpu0':512,'gpu1':64},
                      idle_memory_mb={'gpu0':1200,'gpu1':64},observation_timeout=60)
        self.p['services']={s:dict(name=m['Name'][1:],id=m['Id'],image=m['Image'],
            identity_mode='exact' if s=='wallpaper' else 'contract',contract_sha256=RecoveryObserver.contract(m),
            idle_memory_mb={'gpu0':450 if s=='music' else 64,'gpu1':64}) for s,m in self.metas.items()}
        state=self.a.snapshot();state.update(verified_boot=BOOT1,recovery_policy={'policy':self.p,'policy_id':digest(self.p)})
        self.store.write('ownership.json',state)
        self.free=0;self.calls=[];self.commands=[];self.samples=[];self.foreign=False;self.same_until=0
        self.memory=lambda:454 if self.clock.t<1 else 422
        self.extra_gpu1=False;self.extra_pid=None;self.health='0';self.temp=40;self.util=0;self.ack_error=False
        self.births={'202':'2020','303':'3030'}
        self.pids={'music':['202'],'ollama':['303'],'wallpaper':[]}
        self.on_ack=None;self.on_observation=None
        outer=self
        class Transport:
            def json(self,method,route,payload=None,timeout=5):
                outer.calls.append((method,route))
                if method!='GET': raise RuntimeError('unexpected_fake_effect')
                if route=='/queue':
                    entries=([[0,'foreign']] if outer.foreign else [[0,'prompt']] if outer.free and outer.clock.t<outer.same_until else [])
                    return {'queue_running':entries,'queue_pending':[]}
                if route=='/system_stats': return {'system':{}}
                raise RuntimeError('unexpected_fake_route')
            def ack(self,method,route,payload,timeout=5):
                test.assertEqual((method,route,payload),('POST','/free',{'unload_models':True,'free_memory':True}))
                test.assertIn('music_free_intent',outer.store.job('music-job'))
                outer.free+=1
                if outer.on_ack: outer.on_ack()
                if outer.ack_error: raise Uncertain('fake_lost_ack')
        self.transport=Transport()
        self.backend=ComfyBackend(self.music,self.transport,probe=lambda:self.memory()+12,poll_interval=.1)
        self.backend.execute=lambda *args:Execution({'filename':'private.mp3'},{'prompt_id':'prompt'},b'ID3offline','audio/mpeg')
        self.backends={'music':self.backend,'wallpaper':None,'ollama':None}
        self.obs=RecoveryObserver(self.p,self.targets,self.backends,poll_interval=.1)
        self.obs.command=self.command
        self.obs.driver_health=lambda deadline:{'reported_gpu_faults':False,'boot_id':self.boot,'kernel_records_checked':1}
        self.manager=RecoveryManager(self.a,self.targets,self.backends,observer=self.obs)
        self.runner=object.__new__(Runner)
        self.runner.admission=self.a;self.runner.store=self.store;self.runner.targets=self.targets
        self.runner.backends=self.backends;self.runner.recovery=self.manager;self.runner.futures={}
        self.runner.job_mutex=NativeLock(self.store.path('jobs.lock'))
        self.lease=self.a.try_acquire('music-job','hash',('gpu0',),'music');test.addCleanup(self.lease.close)
        self.store.put_job(dict(request_id='music-job',request_hash='hash',target='music',resources=['gpu0'],
            operation='workflow',payload={},state='waiting',error_code=None,boot_id=BOOT1))
        test.addCleanup(patch.stopall)
        patch('time.monotonic',self.clock.monotonic).start();patch('time.sleep',self.clock.sleep).start()
        patch('aihub_gpu_runner.recovery.process_birth',side_effect=lambda pid:self.births[str(pid)]).start()
        patch('aihub_gpu_runner.music_cleanup.process_birth',side_effect=lambda pid:self.births[str(pid)]).start()
        patch.object(socket.socket,'connect',side_effect=RuntimeError('live_network_forbidden')).start()
        patch.object(subprocess,'Popen',side_effect=RuntimeError('live_process_forbidden')).start()

    def command(self,argv,deadline):
        self.commands.append(argv)
        self.test.assertGreater(deadline,self.clock.t)
        if argv[1]=='inspect':
            return json.dumps([next(m for m in self.metas.values() if argv[2] in (m['Id'],m['Name'][1:]))])
        if argv[1]=='top':
            name=next(s for s,m in self.metas.items() if m['Id']==argv[2])
            return 'PID\n'+'\n'.join(self.pids[name])
        if argv[1]=='--query-compute-apps=gpu_uuid,pid':
            return f'{GPU0},202\n'+(f'{GPU1},303\n' if self.extra_gpu1 else '')
        if argv[1].startswith('--query-compute-apps='):
            return f'{GPU0},{self.extra_pid or 202},{self.memory()}\n'+(f'{GPU1},303,4096\n' if self.extra_gpu1 else '')
        if argv[1]=='--id='+GPU0:
            self.samples.append((self.clock.t,self.memory(),copy.deepcopy(self.a.snapshot()['owners'])))
            if self.on_observation:self.on_observation()
            return f'{GPU0},{self.memory()+12},{self.util},{self.temp},{self.health},No'
        if argv[1]=='--id='+GPU1:return f'{GPU1},4097,50,40,0,No'
        raise RuntimeError('unexpected_fake_command')

    def run(self):
        self.runner._work('music-job',self.lease)
        return self.store.job('music-job')
    def held(self):self.test.assertIn('gpu0',self.a.snapshot()['owners'])
    def failed(self):
        self.held();self.test.assertNotEqual(self.store.job('music-job')['state'],'success')
        with self.test.assertRaises(Conflict):self.runner.result('music-job')
    def success(self):
        job=self.store.job('music-job');self.test.assertEqual(job['state'],'success',job.get('error_code'))
        self.test.assertNotIn('gpu0',self.a.snapshot()['owners']);self.test.assertEqual(self.free,1)
        receipt=self.a.snapshot()['music_release_receipts'][self.lease.owner['lease_id']]
        self.test.assertEqual(receipt['proof_sha256'],digest(job['music_release_idle']))
        proof=job['cleanup_proof']['music_release_idle'];self.test.assertEqual(proof['stable_samples'],3)
        self.test.assertTrue(all(o['sample']['service_memory_mb']['music']['gpu0']<=450 for o in proof['observations']))
        self.test.assertEqual(self.runner.artifact('music-job')[0],b'ID3offline')
        return job


class MusicReleaseTests(unittest.TestCase):
    def test_settles_after_existing_free(self):
        f=Fixture(self);f.run();j=f.success()
        self.assertEqual(j['cleanup_proof']['memory_mb'],466)
        self.assertGreaterEqual(f.clock.t,1.2)
        self.assertTrue(all('gpu0' in owners for _,_,owners in f.samples))
        self.assertEqual(j['music_cleanup_context']['cleanup_deadline_monotonic'],60)
        self.assertGreater(len(f.samples),3)

    def test_never_settles(self):
        f=Fixture(self);f.memory=lambda:454;f.run();f.failed()
        self.assertEqual(f.clock.t,60);self.assertEqual(f.free,1)
        self.assertNotIn('music_release_idle',f.store.job('music-job'))

    def test_GPU1_Ollama_active(self):
        f=Fixture(self);other=f.a.try_acquire('ollama-job','other',('gpu1',),'ollama');self.addCleanup(other.close)
        f.extra_gpu1=True
        processes=f.obs.processes
        def no_gpu1_wait(ident,deadline):
            if ident['id']==f.metas['ollama']['Id']:raise RuntimeError('must_not_read_GPU1_births')
            return processes(ident,deadline)
        f.obs.processes=no_gpu1_wait
        f.run();f.success()
        self.assertEqual(f.a.snapshot()['owners']['gpu1']['lease_id'],other.owner['lease_id'])
        self.assertFalse(any('/api/' in route for _,route in f.calls))
        self.assertFalse(any('--id='+GPU1 in args for args in f.commands))

    def test_paired_prestart_unchanged(self):
        f=Fixture(self);f.extra_gpu1=True;f.run();f.success()
        with self.assertRaisesRegex(RecoveryRequired,'baseline_ollama'):
            f.obs.gpu_sample(('gpu0','gpu1'),70,stopped=True)
        f.memory=lambda:454
        with self.assertRaisesRegex(RecoveryRequired,'baseline_music'):
            f.obs.gpu_sample(('gpu0','gpu1'),70,stopped=True)
        self.assertFalse(any('start' in c for c in f.commands))

    def test_foreign_queue(self):
        f=Fixture(self);f.on_ack=lambda:setattr(f,'foreign',True);f.run();f.failed();self.assertEqual(f.free,1)

    def test_same_prompt_teardown(self):
        f=Fixture(self);f.same_until=2;f.run();j=f.success()
        self.assertGreaterEqual(f.clock.t,2.4)
        self.assertTrue(all(o['queue_empty'] for o in j['music_release_idle']['observations']))

    def test_unknown_compute_PID(self):
        f=Fixture(self);f.on_ack=lambda:setattr(f,'extra_pid',999);f.run();f.failed();self.assertEqual(f.free,1)
        self.assertEqual(f.clock.t,0)

    def test_PID_reuse_or_generation_change(self):
        for change in ('birth','started','id','contract','boot','owner','lock','policy','uuid','resource'):
            with self.subTest(change=change):
                f=Fixture(self)
                def drift():
                    if change=='birth':f.births['202']='reused'
                    elif change=='started':f.metas['music']['State']['StartedAt']='new'
                    elif change=='id':f.metas['music']['Id']='e'*64
                    elif change=='contract':f.metas['music']['Config']['Cmd']=['foreign']
                    elif change=='boot':f.boot=BOOT2
                    elif change=='policy':
                        state=f.a.snapshot();state['recovery_policy']['policy']['services']['music']['idle_memory_mb']['gpu0']=999;f.store.write('ownership.json',state)
                    elif change=='uuid':f.obs.p['gpu_uuids']['gpu0']=GPU1
                    elif change=='resource':
                        state=f.a.snapshot();state['owners']['gpu1']=copy.deepcopy(state['owners']['gpu0']);f.store.write('ownership.json',state)
                    elif change=='lock':f.a._check_physical_identities=lambda s:(_ for _ in ()).throw(RecoveryRequired('physical_lock_identity_changed'))
                    else:
                        s=f.a.snapshot();s['owners']['gpu0']['request_hash']='foreign';f.store.write('ownership.json',s)
                f.on_ack=drift;f.run();f.failed();self.assertEqual(f.free,1)
                patch.stopall()

    def test_identity_change_before_release(self):
        for edge in ('proof','result','terminal','clear'):
            with self.subTest(edge=edge):
                f=Fixture(self);f.memory=lambda:422;write=f.store.write;done=[]
                def fail(name,value):
                    write(name,value)
                    hit=(edge=='proof' and name.startswith('jobs/') and 'music_release_idle' in value or
                         edge=='result' and name=='results/music-job.json' or
                         edge=='terminal' and name.startswith('jobs/') and value.get('state')=='success')
                    if hit and not done:done.append(True);f.metas['music']['State']['StartedAt']='new'
                f.store.write=fail
                if edge=='clear':
                    release=f.lease.release
                    def drift_release(proof,**kwargs):
                        f.boot=BOOT2;return release(proof,**kwargs)
                    f.lease.release=drift_release
                f.run();f.failed();self.assertEqual(f.free,1);patch.stopall()

    def test_memory_rebounds_at_release_boundary(self):
        for edge in ('proof','result','terminal','clear'):
            with self.subTest(edge=edge):
                f=Fixture(self);f.memory=lambda:422;write=f.store.write;done=[]
                def rebound(name,value):
                    write(name,value)
                    hit=(edge=='proof' and name.startswith('jobs/') and 'music_release_idle' in value or
                         edge=='result' and name=='results/music-job.json' or
                         edge=='terminal' and name.startswith('jobs/') and value.get('state')=='success')
                    if hit and not done:done.append(True);f.memory=lambda:454
                f.store.write=rebound
                if edge=='clear':
                    release=f.lease.release
                    def at_clear(proof,**kwargs):
                        f.memory=lambda:454;return release(proof,**kwargs)
                    f.lease.release=at_clear
                f.run();f.failed();self.assertEqual(f.free,1)
                self.assertFalse(f.a.snapshot().get('music_release_receipts'))
                patch.stopall()

    def test_GPU_fault_during_memory_breach(self):
        for fault in ('ecc','temp','xid'):
            with self.subTest(fault=fault):
                f=Fixture(self);f.memory=lambda:454
                if fault=='ecc':f.health='1'
                elif fault=='temp':f.temp=99
                else:f.obs.driver_health=lambda d:(_ for _ in ()).throw(RecoveryRequired('kernel_gpu_driver_fault_requires_operator'))
                f.run();f.failed();self.assertEqual(f.clock.t,0);patch.stopall()

    def test_uncertain_free_or_ack_persistence(self):
        for edge in ('io','ack-before','ack-after'):
            with self.subTest(edge=edge):
                f=Fixture(self);f.ack_error=edge=='io';write=f.store.write;failed=[]
                def fail(name,value):
                    if edge!='io' and name.startswith('jobs/') and 'music_free_ack' in value and not failed:
                        failed.append(True)
                        if edge=='ack-after':write(name,value)
                        raise OSError('fake-fsync')
                    write(name,value)
                f.store.write=fail;f.run();f.failed();self.assertEqual(f.free,1)
                self.assertNotIn('music_release_idle',f.store.job('music-job'));patch.stopall()

    def test_proof_persistence_failure(self):
        for edge in ('context','intent','ack','sample','proof','artifact','result','terminal','release'):
            for after in (False,True):
                with self.subTest(edge=edge,after=after):
                    f=Fixture(self);f.memory=lambda:422;write=f.store.write;wb=f.store.write_bytes;failed=[]
                    fields={'context':'music_cleanup_context','intent':'music_free_intent','ack':'music_free_ack',
                            'sample':'music_cleanup_observation','proof':'music_release_idle'}
                    def fail(name,value):
                        hit=(name.startswith('jobs/') and edge in fields and fields[edge] in value or
                             edge=='result' and name=='results/music-job.json' or
                             edge=='terminal' and name.startswith('jobs/') and value.get('state')=='success' or
                             edge=='release' and name=='ownership.json' and 'gpu0' not in value['owners'])
                        if hit and not failed:
                            failed.append(True)
                            if after:write(name,value)
                            raise OSError('fake-fsync')
                        write(name,value)
                    def fail_bytes(name,value):
                        if edge=='artifact' and name.endswith('.bin') and not failed:
                            failed.append(True)
                            if after:wb(name,value)
                            raise OSError('fake-fsync')
                        wb(name,value)
                    f.store.write=fail;f.store.write_bytes=fail_bytes;f.run()
                    self.assertTrue(failed)
                    if edge=='release' and after:
                        # Atomic replacement happened; truthful readback, not a
                        # claim that a failed fsync can undo a durable clear.
                        self.assertNotIn('gpu0',f.a.snapshot()['owners'])
                        self.assertEqual(f.store.job('music-job')['publication_warning'],'ownership_release_sync_unconfirmed')
                    else:f.failed()
                    self.assertLessEqual(f.free,1)
                    if edge in ('context','intent'):self.assertEqual(f.free,0)
                    patch.stopall()

    def test_runner_restart_mid_settling(self):
        for edge in ('intent','ack','sample','proof','result','terminal'):
            with self.subTest(edge=edge):
                f=Fixture(self);f.memory=lambda:422;write=f.store.write;failed=[]
                fields={'intent':'music_free_intent','ack':'music_free_ack','sample':'music_cleanup_observation','proof':'music_release_idle'}
                def crash(name,value):
                    write(name,value)
                    if not failed and (name.startswith('jobs/') and edge in fields and fields[edge] in value or
                        edge=='result' and name=='results/music-job.json' or
                        edge=='terminal' and name.startswith('jobs/') and value.get('state')=='success'):
                        failed.append(True);raise Crash('offline-edge')
                f.store.write=crash
                with self.assertRaises(Crash):f.run()
                f.store.write=write;f.lease.close();oldfree=f.free
                # Same-boot installed recovery explicitly leaves Music to the
                # operator. Startup must not enqueue or replay this owned job.
                with patch.object(f.manager,'start'),patch.object(f.manager,'close'):
                    runner=Runner(f.a,f.targets,f.backends,recovery=f.manager,poll_interval=.01)
                    self.assertFalse(runner.pending);runner.close()
                f.held();self.assertEqual(f.free,oldfree)
                with self.assertRaises(Conflict):f.runner.result('music-job')
                patch.stopall()

    def test_deadline_not_reset(self):
        f=Fixture(self);f.memory=lambda:1300 if f.clock.t<59.8 else 422;f.run();f.failed()
        self.assertEqual(f.free,1);self.assertEqual(f.clock.t,60)
        self.assertEqual(f.store.job('music-job')['music_cleanup_context']['cleanup_deadline_monotonic'],60)

    def test_scope_regression(self):
        from aihub_gpu_runner.music_cleanup import music_guard
        f=Fixture(self)
        for target in (f.wall,f.ollama):
            self.assertIsNone(music_guard(f.a,target,f.lease.owner,f.obs,lambda **k:None))
        state=f.a.snapshot();state.pop('recovery_policy');f.store.write('ownership.json',state)
        self.assertIsNone(music_guard(f.a,f.music,f.lease.owner,None,lambda **k:None))

    def test_final_observation_does_not_hold_shared_journal_mutex(self):
        f=Fixture(self);release=f.lease.release
        def checked(proof,**kwargs):
            observation=kwargs['before_release']
            def outside_mutex():
                with f.a.mutex.held(0):pass
                observation()
            kwargs['before_release']=outside_mutex
            return release(proof,**kwargs)
        f.lease.release=checked;f.run();f.success()

    def test_one_shot_intent_refuses_second_cleanup(self):
        f=Fixture(self);f.ack_error=True;f.run();f.failed();self.assertEqual(f.free,1)
        job=f.store.job('music-job')
        guard=MusicReleaseGuard(f.a,f.music,f.lease.owner,f.obs,lambda **k:f.runner._update('music-job',**k))
        with self.assertRaisesRegex(RecoveryRequired,'no_replay'):guard.prepare(60)
        self.assertEqual(f.free,1)
        self.assertEqual(f.store.job('music-job'),job)

    def test_unknown_new_child_and_health_after_sample(self):
        for change in ('new-child','duplicate-compute','birth-during-sample','late-health','foreign-queue-at-clear'):
            with self.subTest(change=change):
                f=Fixture(self)
                if change=='new-child':
                    def child():f.pids['music'].append('204');f.births['204']='2040'
                    f.on_ack=child
                elif change=='duplicate-compute':
                    command=f.obs.command
                    f.obs.command=lambda argv,d: (f'{GPU0},202,454\n{GPU0},202,454\n' if argv[1]=='--query-compute-apps=gpu_uuid,pid,used_gpu_memory' else command(argv,d))
                elif change=='birth-during-sample':f.on_observation=lambda:f.births.update({'202':'reused'})
                elif change=='late-health':
                    command=f.obs.command
                    def late(argv,d):
                        value=command(argv,d)
                        if argv[1]=='--id='+GPU0:f.clock.t=61
                        return value
                    f.obs.command=late
                else:
                    release=f.lease.release
                    def foreign(proof,**kwargs):
                        f.foreign=True;return release(proof,**kwargs)
                    f.lease.release=foreign
                f.run();f.failed();self.assertEqual(f.free,1);patch.stopall()

    def test_old_reader_rollback(self):
        f=Fixture(self);f.run();f.success();f.lease.close()
        path=Path(__file__).resolve().parents[1]/'qualification'/'baseline-core.py'
        import hashlib
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         json.loads(path.with_suffix('.sha256.json').read_text())['sha256'])
        spec=importlib.util.spec_from_file_location('aihub_gpu_runner.baseline_core',path)
        old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        store=old.AtomicStore(f.root/'state')
        before={str(p.relative_to(f.root)):p.read_bytes() for p in (f.root/'state').rglob('*.json')}
        reader=object.__new__(old.Runner);reader.store=store;reader.futures={};reader.admission=f.a
        self.assertEqual(reader.result('music-job')['state'],'success')
        self.assertEqual(reader.artifact('music-job')[0],b'ID3offline')
        self.assertEqual(store.read('ownership.json')['owners'],{})
        self.assertIn('music_release_idle',store.job('music-job'))
        self.assertEqual(before,{str(p.relative_to(f.root)):p.read_bytes() for p in (f.root/'state').rglob('*.json')})

if __name__=='__main__':unittest.main()
