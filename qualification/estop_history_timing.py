"""CPU-only compact authority / historical-size latency. No production inputs."""
import json,os,sys,time
from pathlib import Path
from unittest.mock import patch
import aihub_gpu_runner
sys.path.append(str(Path(__file__).resolve().parents[1]))
from tests.estop_history_fixture import history_fixture
from aihub_gpu_runner.core import NativeLock

rows=[]
for count in (4,5000):
 with history_fixture(count=count) as f:
  times=[];sizes=[];reads=[];original=f.store._read_raw
  def read(name,default=None):reads.append(str(name));return original(name,default)
  before={p:p.read_bytes() for p in (f.root/'jobs').glob('*.json')}
  with patch.object(f.store,'jobs',side_effect=AssertionError('historical scan forbidden')),patch.object(f.store,'_read_raw',side_effect=read):
   results=[]
   for _ in range(2):
    start=time.perf_counter();results.append(f.control.execute('admin'));times.append(time.perf_counter()-start)
    sizes.append(f.store.path('ownership.json').stat().st_size)
  assert all(r['outcome']=='success' for r in results)
  assert [r['released_leases'] for r in results]==[1,0]
  assert abs(sizes[1]-sizes[0])<=20
  assert 'recovery-control.json' not in reads
  assert {p:p.read_bytes() for p in before}==before
  fresh=f.admission.try_acquire('fresh','fresh',('gpu1',),'fixture');assert fresh is not None
  fresh.release({'completion_verified':True,'cleanup_verified':True});fresh.close()
  rows.append(dict(job_count=count,compaction=f.compaction,compaction_seconds=f.compaction_seconds,
                   execute_seconds=times,authority_sizes=sizes,job_reads=sum(n.startswith('jobs/') for n in reads),
                   authority_reads=reads.count('ownership.json'),immediate_admission=True,production_actions=0))
print(json.dumps({'origin':aihub_gpu_runner.__file__,'scenarios':rows},indent=2))
