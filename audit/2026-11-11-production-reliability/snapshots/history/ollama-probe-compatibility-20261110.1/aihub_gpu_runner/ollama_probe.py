"""Kernel/daemon attestation of exact native read-only device probes. No controls."""
import re
import shlex
from pathlib import Path
from .core import RecoveryRequired
from .recovery import ProcessDisappeared,process_birth

IMAGE='sha256:39a0a3f22a5f2882b05c62f6f35761a4ed03811a495d34aa10295f648e1b346c'
ROLES={
 ('/usr/lib/ollama/llama-server',('--list-devices','--offline','--verbose')):'llama_list_devices',
 ('/usr/bin/ollama',('gpu-discover','--lib-dir','/usr/lib/ollama','--lib-dir','/usr/lib/ollama/cuda_v12')):'ollama_gpu_discover',
}

def rows(raw):
 lines=raw.strip().splitlines()
 if not lines or lines[0].split()!=['PID','PPID','PIDNS','EXE','COMMAND']:raise ValueError('metadata_unavailable')
 result={}
 for line in lines[1:]:
  parts=line.split(None,4)
  if len(parts)!=5:raise ValueError('invalid_metadata')
  pid,parent,namespace,exe,args=parts
  if not all(v.isdecimal() and int(v)>0 for v in (pid,parent,namespace)) or pid in result:raise ValueError('invalid_metadata')
  result[pid]=dict(ppid=parent,pidns=namespace,exe=exe,argv=shlex.split(args))
 return result

def census(observer,identity,deadline,on_missing,read_birth):
 """One enriched census; old/unsupported observers retain the strict old path."""
 argv=[observer.p['docker'],'top',identity['id'],'-eo','pid,ppid,pidns,exe,args']
 try:raw=observer.command(argv,deadline)
 except Exception:return observer.processes(identity,deadline,on_missing=on_missing),{},None
 try:metadata=rows(raw)
 except ValueError:
  # Existing PID-only fixture/census semantics, without an extra raced census.
  lines=raw.strip().splitlines()
  if not lines or lines[0].strip()!='PID':return observer.processes(identity,deadline,on_missing=on_missing),{},None
  metadata={str(int(line.strip())):None for line in lines[1:]}
 result={};main=str(identity['pid'])
 census_birth=process_birth if all(v is None for v in metadata.values()) else read_birth
 for pid in metadata:
  try:result[pid]=census_birth(pid)
  except ProcessDisappeared:
   if pid==main or on_missing(pid) is not True:raise
 if main not in result:raise RecoveryRequired('container_main_process_unverified')
 return result,metadata,argv

def snapshot(observer,identity,deadline,on_missing,parent_birth,*,read_birth=process_birth,read_cgroup=None):
 processes,metadata,argv=census(observer,identity,deadline,on_missing,read_birth)
 if argv is None or observer.p['services']['ollama']['image']!=IMAGE:return processes,{}
 main=str(identity['pid']);parent=metadata.get(main)
 if not parent or processes[main]!=parent_birth or parent['exe']!='/usr/bin/ollama' or parent['argv'] not in (['/bin/ollama','serve'],['/usr/bin/ollama','serve']):return processes,{}
 read_cgroup=read_cgroup or (lambda pid:Path('/proc',pid,'cgroup').read_text())
 try:group=read_cgroup(main)
 except OSError:return processes,{}
 if not re.search(r'(?:/docker-|/)'+re.escape(identity['id'])+r'(?:\.scope)?(?:/|\n|$)',group):return processes,{}
 candidates={}
 for pid,birth in processes.items():
  m=metadata.get(pid)
  if pid==main or not m or m['ppid']!=main or m['pidns']!=parent['pidns'] or not m['argv']:continue
  role=ROLES.get((m['exe'],tuple(m['argv'][1:]))) if m['argv'][0]==m['exe'] else None
  if role is None:continue
  try:
   if read_cgroup(pid)!=group or read_birth(pid)!=birth or read_birth(main)!=parent_birth:continue
  except (OSError,ProcessDisappeared):continue
  candidates[pid]=dict(pid=pid,birth=birth,ppid=main,parent_birth=parent_birth,pidns=m['pidns'],exe=m['exe'],argv=m['argv'],cgroup=group,container_id=identity['id'],container_started_at=identity['started_at'],role=role)
 if not candidates:return processes,{}
 try:second=rows(observer.command(argv,deadline))
 except Exception:return processes,{}
 verified={}
 for pid,attestation in candidates.items():
  try:
   if second.get(pid)==metadata[pid] and second.get(main)==parent and read_birth(pid)==attestation['birth'] and read_birth(main)==parent_birth and read_cgroup(pid)==group:
    verified[pid]=attestation
  except (OSError,ProcessDisappeared):pass
 return processes,verified
