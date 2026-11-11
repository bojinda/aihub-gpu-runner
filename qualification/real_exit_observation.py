"""CPU-only real child/zombie/reap through production command/parser and /proc reader."""
import hashlib,json,os,subprocess,tempfile,time,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aihub_gpu_runner.recovery import RecoveryObserver,process_birth,ProcessDisappeared
from aihub_gpu_runner.ollama_probe import rows,zombie_identity,exiting_probe

def run(replacement=False):
 with tempfile.TemporaryDirectory(prefix='ollama-exit-proof-') as temp:
  root=Path(temp);src=root/'fixture.c';exe=root/'ollama';exit_file=root/'exit'
  src.write_text('#include <unistd.h>\n#include <stdlib.h>\nint main(void){while(access(getenv("EXIT_FILE"),F_OK))usleep(1000);if(getenv("REPLACE"))execl("/bin/sleep","/bin/sleep","2",(char*)0);return 0;}\n')
  subprocess.run(['gcc','-O2',str(src),'-o',str(exe)],check=True)
  env=dict(os.environ,EXIT_FILE=str(exit_file))
  if replacement:env['REPLACE']='1'
  child=subprocess.Popen([str(exe),'gpu-discover','--lib-dir','/usr/lib/ollama','--lib-dir','/usr/lib/ollama/cuda_v12'],env=env)
  pid=str(child.pid);observer=RecoveryObserver({}, {}, {});deadline=time.monotonic()+10
  command=['/bin/ps','-ww','-p',pid,'-o','pid,ppid,pidns,exe:4096,args']
  try:
   first_raw=observer.command(command,deadline);first=rows(first_raw)[pid];birth=process_birth(pid)
   assert first['exe']==str(exe) and first['argv'][1]=='gpu-discover'
   assert zombie_identity(pid) is None
   exit_file.touch()
   if replacement:
    while True:
     second_raw=observer.command(command,deadline);second=rows(second_raw)[pid]
     if second['exe']!=str(exe):break
     assert time.monotonic()<deadline
     time.sleep(.001)
    assert process_birth(pid)==birth and second['exe'] not in ('-',str(exe))
    attestation=dict(first,birth=birth,cgroup=Path('/proc',pid,'cgroup').read_text())
    assert not exiting_probe(pid,second,attestation,zombie_identity)
    assert zombie_identity(pid) is None
    return dict(first_census=first_raw,second_census=second_raw,birth=birth,actual_exec_replacement_rejected=True,
                production_observer=True,production_parser=True,inference=False,gpu_mutation=False)
   while (zombie:=zombie_identity(pid)) is None:
    assert time.monotonic()<deadline
    time.sleep(.001)
   second_raw=observer.command(command,deadline);second=rows(second_raw)[pid]
   attestation=dict(first,birth=birth,cgroup=Path('/proc',pid,'cgroup').read_text())
   assert exiting_probe(pid,second,attestation,zombie_identity)
   assert not exiting_probe(pid,second,dict(attestation,birth=str(int(birth)+1)),zombie_identity)
   assert process_birth(pid)==birth and second['exe']=='-' and second['argv']==['[ollama]','<defunct>']
   unprivileged=None
   if os.geteuid()==0:
    # Production has a non-root runner observing root-owned Ollama through
    # Docker. Prove direct kernel reads still work with the same separation;
    # the privileged real ps census above supplies namespace/exe metadata.
    code='import json,sys;sys.path.insert(0,'+repr(str(Path(__file__).resolve().parents[1]))+');from aihub_gpu_runner.ollama_probe import zombie_identity;print(json.dumps(zombie_identity('+repr(pid)+')))'
    unprivileged=json.loads(subprocess.check_output(['runuser','-u','nobody','--',sys.executable,'-I','-B','-c',code],text=True))
    assert unprivileged==zombie
   # Positive zombie identity is not positive disappearance. Reaping is
   # deliberate test synchronization, never part of production cleanup.
   child.wait(timeout=5)
   try:process_birth(pid)
   except ProcessDisappeared as missing:assert missing.errno in (2,3)
   else:raise AssertionError('exit_not_confirmed')
   return dict(first_census=first_raw,second_census=second_raw,birth=birth,kernel_zombie=zombie,
     argv_sha256=hashlib.sha256(json.dumps(second['argv'],separators=(',',':')).encode()).hexdigest(),
     positive_disappearance=True,production_observer=True,production_parser=True,production_zombie_reader=True,unprivileged_observer=unprivileged,
     inference=False,gpu_mutation=False,fixture='real compiled CPU process; no Ollama image or inference emulated')
  finally:
   exit_file.touch();child.wait(timeout=5)

if __name__=='__main__':print(json.dumps(dict(exit_transition=run(),executable_replacement=run(True)),indent=2))
