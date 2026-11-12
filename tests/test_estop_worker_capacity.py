"""Worker-capacity component tests, not E-stop authority acceptance.

Generation commits and queue-only leases below are synthetic. Native old lock
holders, actual Runner executors, threads, future cancellation and stores are real.
"""
import concurrent.futures,contextlib,json,os,tempfile,threading,time,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from aihub_gpu_runner.core import Admission,AtomicStore,NativeLock,RecoveryRequired,Runner

def eventually(test):
    end=time.monotonic()+5
    while time.monotonic()<end:
        if test():return
        time.sleep(.005)
    raise AssertionError('fixture_handshake_timeout')

class QueueOnlyLease:
    """Synthetic not-yet-running queue slot; no claimed GPU/authority."""
    def __init__(self):self.owner={'target':'fixture'};self.closed=0
    def close(self):self.closed+=1

class WorkerCapacity(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='estop-capacity-');self.addCleanup(self.tmp.cleanup)
        self.store=AtomicStore(Path(self.tmp.name)/'state')
        self.paths={r:Path(self.tmp.name)/(r+'.lock') for r in ('gpu0','gpu1')}
        for p in self.paths.values():p.touch()
        self.a=Admission(self.store,self.paths,'fixture')
        self.a.bootstrap({'approval_ref':'synthetic','bootstrap_id':self.a.snapshot()['bootstrap_id'],'backend_quiescent':True,'cleanup_verified':True})
        self.runner=Runner(self.a,{}, {},workers=2,poll_interval=.005)
        self.gates=[]
        def close():
            for g in self.gates:g.set()
            self.runner.close()
        self.addCleanup(close)
    def generation(self):
        g=uuid.uuid4().hex
        with self.runner.job_mutex.held(),self.a.mutex.held():
            state=self.a.snapshot();state['admission_generation']={'id':g}
            state['owners']={};self.store.write('ownership.json',state)
        return g
    def submit(self,name,lease):
        visible=concurrent.futures.Future()
        with self.runner.job_mutex.held():
            self.runner.futures[name]=visible
            task=self.runner._submit_owned_locked(name,lease,visible)
        return task,visible
    def old_work(self):
        gate=threading.Event();self.gates.append(gate)
        entered={n:threading.Event() for n in ('old0','old1')};ended=[]
        def work(name,lease):
            if name in entered:
                entered[name].set();gate.wait();ended.append(name)
        self.runner._work=work
        bindings=[]
        for i,r in enumerate(('gpu0','gpu1')):
            lease=self.a.try_acquire('old'+str(i),'payload',(r,),'fixture')
            bindings.append(self.submit('old'+str(i),lease))
        for e in entered.values():self.assertTrue(e.wait(3))
        return gate,bindings,ended
    def test_full_old_pool_new_capacity_and_original_locks_survive(self):
        gate,old,ended=self.old_work()
        queued=QueueOnlyLease();task,visible=self.submit('queued-old',queued)
        old_pool=self.runner.pool;g=self.generation()
        self.assertTrue(self.runner.rotate_worker_capacity(g))
        self.assertIsNot(self.runner.pool,old_pool)
        self.assertTrue(task.cancelled());self.assertTrue(visible.cancelled());self.assertEqual(queued.closed,1)
        for r in self.paths:
            probe=NativeLock(self.paths[r],create=False);self.assertFalse(probe.acquire(0))
        self.assertEqual(ended,[])
        for _,f in old:self.assertTrue(f.running());self.assertFalse(f.cancelled())
        fresh,public=self.submit('fresh',QueueOnlyLease());fresh.result(timeout=3)
        self.assertTrue(public.done());self.assertFalse(gate.is_set())
        self.assertEqual(len(self.runner._retired_executors),1)
        gate.set()
        for f,_ in old:f.result(timeout=3)
        eventually(lambda:not self.runner._retired_executors)
    def test_same_generation_idempotent_and_uncommitted_generation_rejected(self):
        original=self.runner.pool
        with self.assertRaisesRegex(RecoveryRequired,'not_committed'):self.runner.rotate_worker_capacity(uuid.uuid4().hex)
        self.assertIs(self.runner.pool,original)
        g=self.generation();self.assertTrue(self.runner.rotate_worker_capacity(g))
        current=self.runner.pool;self.assertFalse(self.runner.rotate_worker_capacity(g));self.assertIs(self.runner.pool,current)
    def test_late_worker_raw_writes_and_update_do_not_touch_new_state(self):
        gate=threading.Event();entered=threading.Event();self.gates.append(gate);errors=[]
        old_state=self.a.snapshot()
        self.store.put_job({'request_id':'old','state':'running'})
        def work(name,lease):
            entered.set();gate.wait()
            for call in (lambda:self.runner._update('old',state='success'),
                         lambda:self.store.put_job({'request_id':'old','state':'success'}),
                         lambda:self.store.write('ownership.json',old_state),
                         lambda:self.store.write_bytes('results/old.bin',b'late')):
                try:call()
                except RecoveryRequired as exc:errors.append(str(exc))
        self.runner._work=work
        task,old=self.submit('old',QueueOnlyLease());self.assertTrue(entered.wait(3))
        g=self.generation();self.runner.rotate_worker_capacity(g)
        current=self.store.path('ownership.json').read_bytes();gate.set();task.result(timeout=3)
        self.assertEqual(errors,['worker_generation_revoked']*4)
        self.assertEqual(self.store.path('ownership.json').read_bytes(),current)
        self.assertEqual(self.store.job('old')['state'],'running')
        self.assertFalse(self.store.path('results/old.bin').exists())
    def test_scheduler_stale_finish_cannot_remove_current_future_or_pending(self):
        g=self.generation();self.runner.rotate_worker_capacity(g)
        self.runner.closed=True;self.runner.wake.set();self.runner.scheduler.join(2)
        self.assertFalse(self.runner.scheduler.is_alive());self.runner.closed=False
        marker=concurrent.futures.Future()
        with self.runner.job_mutex.held():
            self.runner.futures['same-id']=marker;self.runner.pending['same-id']=time.monotonic()+100
        self.runner._finish_pending('same-id',generation='legacy')
        with self.runner.job_mutex.held():
            self.assertIs(self.runner.futures['same-id'],marker)
            self.assertIn('same-id',self.runner.pending)
            self.runner.pending.clear();self.runner.futures.clear()
    def test_retirement_callbacks_run_outside_jobs_mutex(self):
        gate,old,_=self.old_work();queued=QueueOnlyLease();task,future=self.submit('queued',queued)
        seen=[]
        def callback(_):
            with self.runner.job_mutex.held(.5):seen.append(True)
        future.add_done_callback(callback)
        self.runner.rotate_worker_capacity(self.generation())
        self.assertTrue(task.cancelled());self.assertEqual(seen,[True]);gate.set()
    def test_repeated_rotations_preserve_each_live_old_group(self):
        gate,old,_=self.old_work()
        for _ in range(3):self.runner.rotate_worker_capacity(self.generation())
        eventually(lambda:len(self.runner._retired_executors)==1)
        self.assertFalse(gate.is_set());self.assertTrue(all(f.running() for _,f in old))
        fresh,_=self.submit('newest',QueueOnlyLease());fresh.result(timeout=3)
        gate.set()
    def test_old_recovery_notify_is_suppressed(self):
        gate,old,_=self.old_work();seen=[]
        class Recovery:
            def notify(self):seen.append('notify')
            def close(self):pass
        self.runner.recovery=Recovery()
        self.runner.rotate_worker_capacity(self.generation());gate.set()
        for f,_ in old:f.result(timeout=3)
        self.assertEqual(seen,[])
    def test_same_generation_normal_write_still_works(self):
        def work(name,lease):self.store.put_job({'request_id':name,'state':'success'})
        self.runner._work=work
        task,future=self.submit('ordinary',QueueOnlyLease());task.result(timeout=3)
        self.assertEqual(self.store.job('ordinary')['state'],'success');self.assertIsNone(future.result())

if __name__=='__main__':unittest.main()
