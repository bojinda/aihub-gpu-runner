"""Fixed local privileged actuator. No command, signal, PID or path input.

Installed in a root-owned venv; sudo permits this entry point with NO arguments.
Its root-owned config binds the one runner journal, physical locks and container.
"""
import json
import os
from pathlib import Path
import re
import sys
import time

from .core import RecoveryRequired, guest_boot_id, lock_identity, strict_json
from .native_settlement import sha, completed_output_known
from .owned_execution import AnchoredTarget, PidfdActuator, kernel_anchor
from .target_barrier import TargetBarrier


def verify_completed(root,job,expected):
    import hashlib
    life=job['wallpaper_lifecycle']
    result=(root/'results'/(job['request_id']+'.json')).read_bytes()
    artifact=(root/'results'/(job['request_id']+'.bin')).read_bytes()
    hashes={'.json':hashlib.sha256(result).hexdigest(),'.bin':hashlib.sha256(artifact).hexdigest()}
    value=strict_json(result)
    if (hashes!=expected or hashes['.json']!=life.get('result_sha256') or
            hashes['.bin']!=life.get('artifact_sha256') or value!=life.get('result') or
            value.get('artifact_available') is not True or life.get('execution_error')):
        raise RecoveryRequired('helper_completed_output_changed')


def capture(config,job_id):
    from .core import check_id
    from .comfy_plugin import PINNED
    root=Path(config['state_dir']);state=strict_json((root/'ownership.json').read_bytes())
    if (state['scope']!=config['scope'] or state.get('verified_boot')!=guest_boot_id() or
            set(state['owners'])!={'gpu0','gpu1'} or state['owners']['gpu0']!=state['owners']['gpu1']):
        raise RecoveryRequired('capture_complete_same_boot_owner_required')
    owner=state['owners']['gpu0'];job=strict_json((root/'jobs'/(check_id(job_id)+'.json')).read_bytes())
    if (owner['job_id']!=job_id or owner['target']!='wallpaper' or
            any(job.get(k)!=owner[k] for k in ('lease_id','request_hash','boot_id','target')) or
            job['wallpaper_lifecycle']['phase']!='ready'):
        raise RecoveryRequired('capture_managed_ready_execution_required')
    current=job['wallpaper_lifecycle']['running_container']
    if current['id']!=config['container_id'] or current['running'] is not True:raise RecoveryRequired('capture_container_not_allowlisted')
    anchor=kernel_anchor(current['pid'])
    status=Path('/proc',str(anchor['pid']),'status').read_text()
    if config['container_id'] not in anchor['cgroup'] or next(x.split()[-1] for x in status.splitlines() if x.startswith('NSpid:'))!='1':
        raise RecoveryRequired('capture_container_init_unverified')
    barrier=TargetBarrier(config['barrier_dir']);s=barrier.read();g=s['generation']
    if s['mode']!='open' or g['sources']!=PINNED or g['boot']!=guest_boot_id() or g['pidns']!=anchor['pidns']:
        raise RecoveryRequired('capture_current_barrier_unverified')
    # The trusted plugin must be alive inside this exact PID namespace. No
    # docker-top scan of short-lived Health processes is used as an identity.
    matched=False
    from .target_barrier import birth
    for path in Path('/proc').iterdir():
        if not path.name.isdecimal() or birth(path.name)!=g['birth']:continue
        try:
            ids=next(x.split()[1:] for x in (path/'status').read_text().splitlines() if x.startswith('NSpid:'))
            if int(ids[-1])==g['pid'] and os.stat(path/'ns/pid').st_ino==anchor['pidns']:matched=True;break
        except (OSError,StopIteration):continue
    if not matched or kernel_anchor(current['pid'])!=anchor:raise RecoveryRequired('capture_plugin_process_unverified')
    return dict(barrier_generation=g,execution_init_birth=anchor['birth'],execution_anchor=anchor)


def perform(config,operation):
    if not isinstance(operation,str) or not re.fullmatch('[a-f0-9]{32}',operation):
        raise RecoveryRequired('invalid_operation_id')
    root=Path(config['state_dir']).resolve()
    state=strict_json((root/'ownership.json').read_bytes())
    if state['scope']!=config['scope'] or state.get('verified_boot')!=guest_boot_id():
        raise RecoveryRequired('helper_scope_or_boot_changed')
    for resource,path in config['lock_paths'].items():
        if lock_identity(Path(path).stat())!=state['lock_identity'][resource]:
            raise RecoveryRequired('helper_physical_lock_identity_changed')
    if set(state['owners'])!={'gpu0','gpu1'} or state['owners']['gpu0']!=state['owners']['gpu1']:
        raise RecoveryRequired('helper_complete_owner_pair_required')
    owner=state['owners']['gpu0']
    if owner['target']!='wallpaper' or owner['boot_id']!=guest_boot_id():raise RecoveryRequired('helper_owner_mismatch')
    # Job ID came from the native journal, still validate it before path access.
    from .core import check_id
    job=strict_json((root/'jobs'/(check_id(owner['job_id'])+'.json')).read_bytes())
    if any(job.get(k)!=owner[k] for k in ('request_hash','lease_id','boot_id','target')):
        raise RecoveryRequired('helper_job_binding_changed')
    c=state.get('practical_control',{})
    attempt=c.get('attempts',{}).get(operation)
    if attempt:
        if (c.get('active')!=operation or c.get('admission')!='closed' or
                attempt.get('lease')!=owner['lease_id'] or attempt.get('stop_intent') not in ('graceful','force') or
                attempt.get('approval')!='confirmed_exact_action' or
                attempt['mode']=='emergency' and attempt.get('work_loss_accepted') is not True):
            raise RecoveryRequired('helper_exact_approved_intent_required')
        stage=attempt['stop_intent']
        if attempt['mode']!='emergency':
            plan=attempt['plan']
            if plan['terminal']=='success':
                verify_completed(root,job,plan['artifacts'])
                if stage=='force' and not state['recovery_policy']['policy']['allow_force']:
                    raise RecoveryRequired('helper_completion_force_policy_disabled')
            elif (completed_output_known(job) or stage!='graceful' or attempt.get('work_loss_accepted') is not True or not (
                    job.get('owned_execution_failed') is True or
                    isinstance(job.get('execution_deadline'),(int,float)) and job['execution_deadline']<=time.time())):
                raise RecoveryRequired('helper_unfinished_abandonment_not_authorized')
    else:
        normal=state.get('normal_stop_intents',{}).get(operation)
        if (not normal or normal['lease']!=owner['lease_id'] or not job['wallpaper_lifecycle'].get('term_intent') or
                normal.get('mode')!='normal_completed' or normal.get('policy_id')!=state['recovery_policy']['policy_id']):
            raise RecoveryRequired('helper_normal_completion_intent_required')
        verify_completed(root,job,{'.json':normal['result_sha256'],'.bin':normal['artifact_sha256']})
        stage=normal['stage']
        if stage not in ('graceful','force') or stage=='force' and not state['recovery_policy']['policy']['allow_force']:
            raise RecoveryRequired('helper_completion_force_policy_disabled')
    expected=job['wallpaper_lifecycle']['running_container'];anchor=job['execution_anchor']
    if expected['id']!=config['container_id']:raise RecoveryRequired('helper_container_not_allowlisted')
    if anchor['boot']!=guest_boot_id() or anchor['pid']!=expected['pid']:
        raise RecoveryRequired('helper_kernel_binding_changed')
    if not config.get('qualification_process',False):
        if config['container_id'] not in anchor['cgroup']:raise RecoveryRequired('helper_wrong_container_cgroup')
        if kernel_anchor(anchor['pid'])!=anchor:raise RecoveryRequired('helper_kernel_identity_changed')
        status=Path('/proc',str(anchor['pid']),'status').read_text()
        if next(x.split()[-1] for x in status.splitlines() if x.startswith('NSpid:'))!='1':
            raise RecoveryRequired('helper_requires_container_init')
    barrier=TargetBarrier(config['barrier_dir'])
    if barrier.read()['generation']!=job['barrier_generation']:raise RecoveryRequired('helper_target_generation_changed')
    PidfdActuator(barrier,AnchoredTarget(anchor,expected),config.get('grace_seconds',2)).stop(
        expected,anchor['birth'],owner['lease_id'],operation,stage,time.monotonic()+25)
    return {'operation':operation,'original_execution_gone':not AnchoredTarget(anchor,expected).container(time.monotonic()+1)['running']}


def main():
    if len(sys.argv)!=1:raise SystemExit('no arguments permitted')
    path=Path('/etc/aihub/recovery-helper.json')
    stat=path.stat()
    if stat.st_uid!=0 or stat.st_mode & 0o022:raise SystemExit('root_owned_helper_configuration_required')
    config=strict_json(path.read_bytes())
    if config.get('qualification_process'):raise SystemExit('qualification_mode_not_allowed_in_installed_helper')
    value=strict_json(sys.stdin.buffer.read(1025))
    if not isinstance(value,dict):raise SystemExit('exact_operation_required')
    try:
        if set(value)=={'capture_job'}:result=capture(config,value['capture_job'])
        elif set(value)=={'operation'}:result=perform(config,value['operation'])
        else:raise RecoveryRequired('exact_operation_required')
        print(json.dumps(result))
    except RecoveryRequired as exc:
        print(json.dumps({'error':str(exc)}));raise SystemExit(3)
