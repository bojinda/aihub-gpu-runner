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

def zombie_identity(pid):
 """Positive kernel exit state, never inferred from ps's unavailable exe column."""
 pid=str(pid)
 if not pid.isdecimal() or int(pid)<=0:raise ValueError('invalid_pid')
 root=Path('/proc',pid)
 def stat():
  raw=(root/'stat').read_text();fields=raw.rsplit(')',1)[1].split()
  if not raw.startswith(pid+' (') or len(fields)<20 or not fields[19].isdecimal():raise ValueError('invalid_stat')
  return dict(state=fields[0],ppid=fields[1],birth=fields[19])
 first=stat()
 if first['state']!='Z':return None
 # The privileged Docker census already binds the PID namespace and EXE
 # columns. Direct /proc namespace/exe links require ptrace permission for
 # root-owned Ollama; do not reinterpret that denial as proof of exit.
 # Readable kernel Z state with an unchanged birth/parent, empty cmdline and
 # unchanged cgroup independently proves the observed terminal transition.
 if (root/'cmdline').read_bytes()!=b'':return None
 group=(root/'cgroup').read_text()
 if stat()!=first:return None
 return dict(first,cgroup=group)

def exiting_probe(pid,metadata,attestation,read_zombie,diagnostic=None):
 if not metadata or metadata!=dict(ppid=attestation['ppid'],pidns=attestation['pidns'],
     exe='-',argv=['['+Path(attestation['exe']).name+']','<defunct>']):return False
 try:observed=read_zombie(pid)
 except (OSError,ValueError,ProcessDisappeared) as exc:
  emit(diagnostic,'exit_transition_kernel_observation_unavailable',pid=pid,exception_type=type(exc).__name__,errno=getattr(exc,'errno',None))
  raise
 matched=observed==dict(state='Z',ppid=attestation['ppid'],birth=attestation['birth'],cgroup=attestation['cgroup'])
 emit(diagnostic,'exit_transition_kernel_identity_verified' if matched else 'exit_transition_kernel_identity_mismatch',
     pid=pid,observed=observed,expected_birth=attestation['birth'],expected_parent=attestation['ppid'])
 return matched

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

def emit(diagnostic,predicate,**fields):
 try:
  if diagnostic is not None:diagnostic(predicate,**fields)
 except Exception:pass

def census(observer,identity,deadline,on_missing,read_birth,diagnostic=None):
 """One enriched census; old/unsupported observers retain the strict old path."""
 argv=[observer.p['docker'],'top',identity['id'],'-eo','pid,ppid,pidns,exe,args']
 try:raw=observer.command(argv,deadline)
 except Exception as exc:
  emit(diagnostic,'enriched_census_unavailable',exception_type=type(exc).__name__)
  return observer.processes(identity,deadline,on_missing=on_missing),{},None
 try:metadata=rows(raw)
 except ValueError:
  emit(diagnostic,'enriched_census_invalid')
  # Existing PID-only fixture/census semantics, without an extra raced census.
  lines=raw.strip().splitlines()
  if not lines or lines[0].strip()!='PID':return observer.processes(identity,deadline,on_missing=on_missing),{},None
  metadata={str(int(line.strip())):None for line in lines[1:]}
 result={};main=str(identity['pid'])
 census_birth=process_birth if all(v is None for v in metadata.values()) else read_birth
 for pid in metadata:
  try:result[pid]=census_birth(pid)
  except ProcessDisappeared as exc:
   emit(diagnostic,'census_process_disappeared',pid=pid,errno=exc.errno,metadata=metadata.get(pid))
   if pid==main or on_missing(pid) is not True:raise
 if main not in result:raise RecoveryRequired('container_main_process_unverified')
 return result,metadata,argv

def snapshot(observer,identity,deadline,on_missing,parent_birth,*,read_birth=process_birth,read_cgroup=None,diagnostic=None,prior_probes=None,read_zombie=zombie_identity):
 processes,metadata,argv=census(observer,identity,deadline,on_missing,read_birth,diagnostic)
 if argv is None:
  emit(diagnostic,'enriched_census_not_available');return processes,{}
 if observer.p['services']['ollama']['image']!=IMAGE:
  emit(diagnostic,'pinned_image_mismatch',image=observer.p['services']['ollama']['image']);return processes,{}
 main=str(identity['pid']);parent=metadata.get(main)
 if not parent:
  emit(diagnostic,'parent_metadata_missing',pid=main);return processes,{}
 if processes[main]!=parent_birth:
  emit(diagnostic,'parent_birth_mismatch',pid=main,birth=processes[main],expected_birth=parent_birth,metadata=parent);return processes,{}
 if parent['exe']!='/usr/bin/ollama':
  emit(diagnostic,'parent_executable_mismatch',pid=main,metadata=parent);return processes,{}
 if parent['argv'] not in (['/bin/ollama','serve'],['/usr/bin/ollama','serve']):
  emit(diagnostic,'parent_argv_mismatch',pid=main,metadata=parent);return processes,{}
 read_cgroup=read_cgroup or (lambda pid:Path('/proc',pid,'cgroup').read_text())
 try:group=read_cgroup(main)
 except OSError as exc:
  emit(diagnostic,'parent_cgroup_unreadable',pid=main,errno=exc.errno);return processes,{}
 if not re.search(r'(?:/docker-|/)'+re.escape(identity['id'])+r'(?:\.scope)?(?:/|\n|$)',group):
  emit(diagnostic,'parent_container_cgroup_mismatch',pid=main,cgroup=group,container_id=identity['id']);return processes,{}
 candidates={}
 for pid,birth in processes.items():
  m=metadata.get(pid)
  if pid==main:continue
  def refused(predicate,**fields):
   emit(diagnostic,predicate,pid=pid,birth=birth,metadata=m,parent_pid=main,parent_birth=parent_birth,parent_pidns=parent['pidns'],**fields)
  if not m:refused('child_metadata_missing');continue
  previous=(prior_probes or {}).get(pid)
  if previous and previous['birth']==birth and previous['parent_birth']==parent_birth and previous['container_id']==identity['id'] and previous['container_started_at']==identity['started_at']:
   try:
    if exiting_probe(pid,m,previous,read_zombie,diagnostic):
     candidates[pid]=previous;continue
   except (OSError,ValueError,ProcessDisappeared) as exc:
    refused('exit_transition_unverified',exception_type=type(exc).__name__);continue
  if m['ppid']!=main:refused('child_not_direct_descendant');continue
  if m['pidns']!=parent['pidns']:refused('child_pid_namespace_mismatch');continue
  if not m['argv']:refused('child_argv_missing');continue
  role=ROLES.get((m['exe'],tuple(m['argv'][1:]))) if m['argv'][0]==m['exe'] else None
  if role is None:refused('exact_probe_role_mismatch' if m['argv'][0]==m['exe'] else 'executable_argv0_mismatch');continue
  try:
   child_group=read_cgroup(pid)
   if child_group!=group:refused('child_cgroup_mismatch',cgroup=child_group,parent_cgroup=group);continue
   child_birth=read_birth(pid)
   if child_birth!=birth:refused('child_birth_mismatch',observed_birth=child_birth);continue
   observed_parent_birth=read_birth(main)
   if observed_parent_birth!=parent_birth:refused('parent_birth_recheck_mismatch',observed_parent_birth=observed_parent_birth);continue
  except (OSError,ProcessDisappeared) as exc:
   refused('first_census_identity_unavailable',exception_type=type(exc).__name__,errno=getattr(exc,'errno',None));continue
  candidates[pid]=dict(pid=pid,birth=birth,ppid=main,parent_birth=parent_birth,pidns=m['pidns'],exe=m['exe'],argv=m['argv'],cgroup=group,container_id=identity['id'],container_started_at=identity['started_at'],role=role)
 if not candidates:return processes,{}
 try:second=rows(observer.command(argv,deadline))
 except Exception as exc:
  for pid,attestation in candidates.items():emit(diagnostic,'second_census_unavailable',pid=pid,first=attestation,exception_type=type(exc).__name__)
  return processes,{}
 verified={}
 for pid,attestation in candidates.items():
  try:
   def rejected(predicate,**fields):emit(diagnostic,predicate,pid=pid,first=attestation,second=second.get(pid),**fields)
   exiting=False
   if second.get(pid)!=dict(ppid=attestation['ppid'],pidns=attestation['pidns'],exe=attestation['exe'],argv=attestation['argv']):
    exiting=exiting_probe(pid,second.get(pid),attestation,read_zombie,diagnostic)
    if not exiting:rejected('second_child_metadata_missing' if pid not in second else 'second_child_metadata_changed');continue
   if second.get(main)!=parent:rejected('second_parent_metadata_changed',second_parent=second.get(main));continue
   child_birth=read_birth(pid)
   if child_birth!=attestation['birth']:rejected('second_child_birth_changed',observed_birth=child_birth);continue
   observed_parent_birth=read_birth(main)
   if observed_parent_birth!=parent_birth:rejected('second_parent_birth_changed',observed_parent_birth=observed_parent_birth);continue
   child_group=read_cgroup(pid)
   if child_group!=group:rejected('second_child_cgroup_changed',observed_cgroup=child_group);continue
   verified[pid]=attestation
   emit(diagnostic,'probe_exit_quarantined' if exiting else 'probe_attested',pid=pid,identity=attestation)
  except (OSError,ValueError,ProcessDisappeared) as exc:
   rejected('second_identity_unavailable',exception_type=type(exc).__name__,errno=getattr(exc,'errno',None))
 return processes,verified
