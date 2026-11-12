"""Synthetic production-sized CPU fixture; contains no captured journal bytes."""
import contextlib
import copy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import uuid

from aihub_gpu_runner.core import Admission, AtomicStore, encode
from aihub_gpu_runner.estop import Estop, activate_generations


@contextlib.contextmanager
def history_fixture(count=1798, authority_bytes=1463844):
    with tempfile.TemporaryDirectory(prefix='estop-history-') as directory:
        root = Path(directory)
        store = AtomicStore(root)
        paths = {r:root/'physical-locks'/(r+'.lock') for r in ('gpu0','gpu1')}
        for path in paths.values():
            path.parent.mkdir(exist_ok=True)
            path.touch()
        admission = Admission(store, paths, 'synthetic', True)
        admission.bootstrap(dict(approval_ref='fixture', bootstrap_id=admission.snapshot()['bootstrap_id'],
                                 backend_quiescent=True, cleanup_verified=True))
        activate_generations(admission, confirmed_coordinated_shutdown=True)
        generation = admission.snapshot()['admission_generation']['id']
        for number in range(count-2):
            store.put_job(dict(request_id=f'history-{number:06}', request_hash='synthetic',
                               state='success', resources=['gpu1'], target='fixture',
                               saved_output='synthetic-output-'+'x'*4150))
        store.put_job(dict(request_id='retained', request_hash='synthetic-retained', state='reconciling',
                           phase='execution_complete', resources=['gpu1'], target='fixture'))
        lease = admission.try_acquire('retained', 'synthetic-retained', ('gpu1',), 'fixture')
        assert lease is not None
        try:
            job = store.job('retained'); job['lease_id'] = lease.owner['lease_id']; store.put_job(job)
            store.put_job(dict(request_id='waiting', request_hash='synthetic-waiting', state='waiting',
                               resources=['gpu1'], target='fixture'))
            state = admission.snapshot()
            # Three previous no-effect attempts and their prepared lock files stay present.
            for number in range(3):
                op = f'{number:032x}'; new = uuid.uuid4().hex
                prepared = root/'generations'/new; prepared.mkdir(parents=True)
                for resource in ('gpu0','gpu1'):(prepared/(resource+'.lock')).touch(mode=0o600)
                state['estop_intents'][op] = dict(actor='fixture', binding='synthetic',
                                                  new_generation=new, created_at=number)
            row = dict(action='synthetic-history', observations=[
                dict(pid=i, birth=i*100, executable='/synthetic/fixture', cgroup='synthetic',
                     namespace='synthetic', identity='x'*40) for i in range(8)])
            state['qualification_history'] = [copy.deepcopy(row) for _ in range(800)] if authority_bytes else []
            state['qualification_padding'] = ''
            missing = authority_bytes-len(encode(state))
            if missing > 0:state['qualification_padding'] = 'x'*missing
            store.write('ownership.json',state)
            class Capacity:
                def __init__(self):
                    self.admission=admission; self.store=store; self.wake=threading.Event()
                    self.rotations=[]; self._worker_generation=generation
                def _rotate_worker_capacity_locked(self,g):
                    self._worker_generation=g; self.rotations.append(g)
                def _retire_executor(self,group):raise AssertionError('fixture_has_no_executor')
            runner = Capacity()
            yield SimpleNamespace(root=root, store=store, admission=admission, lease=lease,
                                  control=Estop(runner,b'synthetic-key'*4), runner=runner)
        finally:
            lease.close()
