"""Music worker-only cleanup. No HTTP, timers, threads, queue flags or runner writes.

Called after native task_done/output registration, on the prompt worker thread.
Linux only. Canonical runner state must be a DIRECTORY bind, never a bind of
ownership.json (the runner replaces that file atomically).
"""
import gc
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import time

CONFIG = Path('/run/aihub-music-cleanup/config.json')
PROTOCOL_VERSION = 2
STATE = Path('/run/aihub-music-runner')
MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 16


class Skip(Exception):
    pass


def read_json(path, limit=16384):
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise Skip('json_size')
    return json.loads(raw)


def check_time(deadline):
    if time.monotonic() >= deadline:
        raise Skip('budget_exhausted')


"""Strict kernel-backed translation; no controls, subprocesses or GPU imports."""
import hashlib
import json
import re
from pathlib import Path


def require(test, reason):
    if not test:raise ValueError('music_identity_'+reason)


def process(root, pid):
    p=Path(root)/str(pid)
    status=dict(line.split(':',1) for line in (p/'status').read_text().splitlines())
    ns=(p/'ns/pid').stat();cg=(p/'ns/cgroup').stat();exe=(p/'exe').stat()
    return dict(pid=pid,nspid=[int(v) for v in status['NSpid'].split()],
        birth=(p/'stat').read_text().rsplit(')',1)[1].split()[19],
        cgroup=(p/'cgroup').read_text(),pidns=[ns.st_dev,ns.st_ino],cgroupns=[cg.st_dev,cg.st_ino],exe=[exe.st_dev,exe.st_ino])


def rows(raw):
    result=[]
    for line in raw.splitlines():
        if not line.strip():continue
        parts=[v.strip() for v in line.split(',')]
        require(len(parts)==3 and parts[0].startswith('GPU-') and parts[1].isdecimal() and
                parts[2].isdecimal(),'driver_row')
        result.append(dict(gpu_uuid=parts[0],pid=int(parts[1]),memory_mib=int(parts[2])))
    return result


def bind(meta, host, init, inner, inner_init, host_rows, container_rows, gpu_uuid, boot):
    require(meta['State']['Running'] is True and init['pid']==meta['State']['Pid'],'container_generation')
    require(meta['HostConfig']['CgroupnsMode']=='private','private_cgroup_namespace_required')
    require(re.fullmatch('[a-f0-9]{64}',meta['Id']) is not None,'container_id')
    require(len(host['nspid'])==len(init['nspid'])==2 and host['nspid']==[host['pid'],inner['pid']] and
            init['nspid']==[init['pid'],1] and inner['nspid']==[inner['pid']] and inner_init['nspid']==[1], 'namespace_mapping')
    require(host['birth']==inner['birth'] and init['birth']==inner_init['birth'],'process_birth')
    require(host['pidns']==init['pidns']==inner['pidns']==inner_init['pidns'],'namespace_identity')
    require(host['cgroupns']==init['cgroupns']==inner['cgroupns']==inner_init['cgroupns'],'cgroup_namespace_identity')
    require(host['exe']==inner['exe'] and init['exe']==inner_init['exe'],'executable_identity')
    require(host['cgroup']==init['cgroup'] and inner['cgroup']==inner_init['cgroup'],'cgroup_membership')
    require(inner['cgroup']=='0::/\n' and host['cgroup']!=inner['cgroup'],'actual_inside_cgroup_view_required')
    require(re.search(r'(?:/docker-|/)'+meta['Id']+r'(?:\.scope)?(?:/|\n|$)',host['cgroup']) is not None,'container_membership')
    require(len(host_rows)==len(container_rows)==1,'unexpected_gpu_process')
    h,c=host_rows[0],container_rows[0]
    require(h['gpu_uuid']==c['gpu_uuid']==gpu_uuid and h['pid']==host['pid'] and c['pid']==inner['pid'],'gpu_process_mapping')
    return dict(schema=1,container_id=meta['Id'],container_started_at=meta['State']['StartedAt'],boot=boot,
        gpu_uuid=gpu_uuid,host=host,init=init,inner=inner,inner_init=inner_init)


def verify_inside(binding, current, init, boot, accepted_container_id, gpu_rows):
    # The RO attestation is created from BOTH live kernel procfs views by the
    # attended host authority. Current self/init tuples must remain identical.
    require(binding['schema']==1 and binding['boot']==boot and binding['container_id']==accepted_container_id,'accepted_generation')
    require(current==binding['inner'] and init==binding['inner_init'],'process_reuse_or_restart')
    require(len(gpu_rows)==1 and gpu_rows[0]['gpu_uuid']==binding['gpu_uuid'] and
            gpu_rows[0]['pid']==current['pid'],'unexpected_gpu_process')
    return dict(tested=True,host_pid=binding['host']['pid'],namespace_pid=current['pid'],
                process_birth=current['birth'],memory_mib=gpu_rows[0]['memory_mib'])


def memory_sample(config, model_management, deadline):
    if model_management.get_torch_device().type == 'cpu':
        return {'tested': False, 'reason': 'cpu_only'}
    try:
        telemetry = config['telemetry']
        binding = telemetry['identity_translation']
        require(binding['host']['pid'] == telemetry['host_pid'] and
                binding['host']['birth'] == telemetry['process_birth'] and
                binding['gpu_uuid'] == telemetry['gpu0_uuid'], 'configuration_binding')
        current = process('/proc', os.getpid())
        init = process('/proc', 1)
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        state = read_json(STATE / 'ownership.json', 8 * 1024 * 1024)
        accepted = state['recovery_policy']['policy']['services']['music']['id']
        require(state['recovery_policy']['policy']['services']['music']['idle_memory_mb']['gpu0'] == 475 and
                config.get('idle_ceiling_mib') == 475, 'accepted_idle_ceiling')
        check_time(deadline)
        completed = subprocess.run(['/usr/bin/nvidia-smi',
            '--query-compute-apps=gpu_uuid,pid,used_gpu_memory', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=min(1, max(.001, deadline-time.monotonic())), check=True)
        value = verify_inside(binding, current, init, boot, accepted, rows(completed.stdout))
        require(current == process('/proc', os.getpid()) and init == process('/proc', 1), 'measurement_identity_race')
        return value
    except (ValueError, KeyError, OSError):
        raise Skip('music_telemetry_identity_unverified') from None


def saved_audio(history, output_root, deadline):
    """Only native saved output descriptors; verify every emitted audio file.

    SaveAudio completes synchronously. Sync and hash the closed regular file,
    retaining its path/stat/hash as evidence; no audio is rewritten or deleted.
    """
    root = Path(output_root).resolve(strict=True)
    found = []
    total = 0
    outputs = history.get('outputs', {})
    if not isinstance(outputs, dict):
        raise Skip('outputs_invalid')
    for node_id, node_output in outputs.items():
        if not isinstance(node_output, dict):
            raise Skip('output_invalid')
        if 'audio' not in node_output:
            continue
        if not isinstance(node_output['audio'], list) or not node_output['audio']:
            raise Skip('audio_invalid')
        for desc in node_output['audio']:
            check_time(deadline)
            if len(found) >= MAX_FILES or not isinstance(desc, dict) or desc.get('type') != 'output':
                raise Skip('not_persisted_audio')
            filename, subfolder = desc.get('filename'), desc.get('subfolder', '')
            if not isinstance(filename, str) or not filename or not isinstance(subfolder, str):
                raise Skip('audio_path_invalid')
            rel = PurePosixPath(subfolder) / filename
            if rel.is_absolute() or '..' in rel.parts or '\\' in str(rel):
                raise Skip('audio_path_invalid')
            path = root.joinpath(*rel.parts)
            if path.resolve(strict=True) != path or not path.is_relative_to(root):
                raise Skip('audio_symlink_or_escape')
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                before = os.fstat(fd)
                if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
                    raise Skip('audio_not_regular_or_empty')
                total += before.st_size
                if total > MAX_BYTES:
                    raise Skip('audio_size')
                os.fsync(fd)
                digest = hashlib.sha256()
                remaining = before.st_size
                while remaining:
                    check_time(deadline)
                    chunk = os.read(fd, min(1024 * 1024, remaining))
                    if not chunk:
                        raise Skip('audio_short_read')
                    digest.update(chunk)
                    remaining -= len(chunk)
                after = os.fstat(fd)
                signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                if signature(before) != signature(after) or signature(path.stat()) != signature(after):
                    raise Skip('audio_changed')
                # Persist directory entry as well as contents, on Linux.
                dirfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dirfd)
                finally:
                    os.close(dirfd)
                found.append(dict(path=str(path), bytes=after.st_size, sha256=digest.hexdigest()))
            finally:
                os.close(fd)
    if not found:
        raise Skip('no_saved_audio')
    return found


def ready_for_cleanup(state, state_root, config):
    if state.get('ready') is True:
        return True
    try:
        permit = read_json(state_root / 'music-cleanup-permit.json')
        policy = state['recovery_policy']
        control = state['practical_control']
        return (state.get('ready') is False and state.get('owners') == {} and
                control.get('admission') == 'closed' and control.get('active') is None and
                permit.get('schema') == 1 and permit.get('kind') == 'music_cleanup_validation_v1' and
                permit.get('enabled') is True and
                isinstance(config.get('maintenance_operation'), str) and
                permit.get('operation') == config['maintenance_operation'] and
                permit.get('scope') == state['scope'] == config['scope'] and
                permit.get('boot') == state['verified_boot'] and
                permit.get('policy_id') == policy['policy_id'] and
                permit.get('music_binding') == policy['policy']['services']['music'] and
                permit['music_binding']['idle_memory_mb']['gpu0'] == 475 and
                permit.get('gpu0_identity') == state['lock_identity']['gpu0'] == config['gpu0_identity'] and
                permit.get('telemetry') == config['telemetry'])
    except (OSError, ValueError, KeyError, TypeError, AttributeError, Skip):
        return False


def check_owner(state_root, config, fd):
    s = read_json(state_root / 'ownership.json', 8 * 1024 * 1024)
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    ident = os.fstat(fd)
    path,expected=cleanup_lock(state_root,config,s)
    identity = dict(device=ident.st_dev, inode=ident.st_ino,
                    mode=stat.S_IMODE(ident.st_mode), uid=ident.st_uid, gid=ident.st_gid)
    if (s.get('schema') not in (1,2) or not ready_for_cleanup(s, state_root, config) or s.get('scope') != config['scope']
            or s.get('verified_boot') != boot or not isinstance(s.get('owners'), dict)
            or 'gpu0' in s['owners'] or identity != expected
            or s.get('lock_identity', {}).get('gpu0') != expected):
        raise Skip('runner_owned_unready_or_identity_changed')
    # Original inode must still be the pathname's inode, including after flock.
    current = path.stat()
    if (current.st_dev, current.st_ino) != (ident.st_dev, ident.st_ino):
        raise Skip('physical_lock_replaced')


def cleanup_lock(state_root,config,state):
    original=state_root/'physical-locks/gpu0.lock'
    if state.get('schema')!=2:return original,config['gpu0_identity']
    g=state['admission_generation'];origin=g['origin']
    if origin['lock_identity']['gpu0']!=config['gpu0_identity']:
        raise Skip('original_gpu0_binding_changed')
    st=original.stat()
    identity=dict(device=st.st_dev,inode=st.st_ino,mode=stat.S_IMODE(st.st_mode),uid=st.st_uid,gid=st.st_gid)
    if identity!=config['gpu0_identity'] or not __import__('re').fullmatch('[a-f0-9]{32}',g['id']):
        raise Skip('original_gpu0_binding_changed')
    path=original if g['sequence']==0 else state_root/'generations'/g['id']/'gpu0.lock'
    if path.resolve()!=path or g['lock_identity']!=state['lock_identity']:
        raise Skip('generation_lock_binding_changed')
    return path,state['lock_identity']['gpu0']


def cleanup(q, executor, item, output_root, model_management, *, config_path=CONFIG, state_root=STATE):
    """One attempt, no replay. A later prompt is either already queued (skip),
    or is admitted only after this synchronous worker section has ended.
    """
    start = time.monotonic()
    result = dict(event='music_manual_cleanup', prompt_id=item[1], outcome='skipped')
    try:
        if not config_path.is_file():
            return dict(result, reason='disabled')
        config = read_json(config_path)
        if config.get('enabled') is not True or config.get('service') != 'comfyui-music':
            raise Skip('disabled_or_wrong_service')
        budget = config.get('budget_seconds')
        if not isinstance(budget, (int, float)) or not 0 < budget <= 5:
            raise Skip('invalid_budget')
        deadline = start + budget
        extra = item[3]
        if (executor.success is not True or not isinstance(extra.get('client_id'), str)
                or not extra['client_id'] or not isinstance(extra.get('extra_pnginfo', {}).get('workflow'), dict)
                or 'aihub_owned_submission' in extra):
            raise Skip('not_successful_native_ui_prompt')
        import fcntl
        # Taking q.mutex blocks native put/get/set_flag only for the synchronous
        # cleanup section. No waiting for GPU locks and no HTTP in this section.
        if not q.mutex.acquire(blocking=False):
            raise Skip('queue_lock_busy')
        try:
            if q.queue or q.currently_running or q.flags:
                raise Skip('queue_busy_or_external_flags')
            path,_=cleanup_lock(state_root,config,read_json(state_root/'ownership.json',8*1024*1024))
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise Skip('gpu0_lock_busy') from None
                check_owner(state_root, config, fd)
                for node_id, value in executor.history_result.get('outputs', {}).items():
                    if 'audio' in value and item[2].get(node_id, {}).get('class_type') not in (
                            'SaveAudio', 'SaveAudioMP3', 'SaveAudioOpus', 'SaveAudioAdvanced'):
                        raise Skip('unsupported_audio_saver')
                result['audio'] = saved_audio(executor.history_result, output_root, deadline)
                check_owner(state_root, config, fd)
                check_time(deadline)
                result['memory_before'] = memory_sample(config, model_management, deadline)
                # These are the EXACT native worker /free operations. No flags
                # remain pending, and unload is confined to this CUDA process.
                result['outcome'] = 'attempted'
                native_start = time.monotonic()
                result['pre_native_seconds'] = round(native_start - start, 6)
                result['phase_seconds'] = {}
                try:
                    for name, operation in (('unload_models', model_management.unload_all_models),
                                            ('executor_reset', executor.reset),
                                            ('gc', gc.collect),
                                            ('soft_empty_cache', model_management.soft_empty_cache)):
                        phase_start = time.monotonic()
                        try:
                            check_owner(state_root,config,fd)
                            operation()
                        finally:
                            result['phase_seconds'][name] = round(time.monotonic() - phase_start, 6)
                        check_time(deadline)
                finally:
                    result['native_cleanup_seconds'] = round(time.monotonic() - native_start, 6)
                result['native_cleanup_completed'] = True
                post_native_start = time.monotonic()
                result['memory_after'] = memory_sample(config, model_management, deadline)
                result['post_native_seconds'] = round(time.monotonic() - post_native_start, 6)
                if result['memory_after']['tested']:
                    result['idle_ceiling_mib'] = 475
                    result['idle_ceiling_met'] = result['memory_after']['memory_mib'] <= 475
                result['outcome'] = 'completed'
                result['within_budget'] = time.monotonic() < deadline
            finally:
                # close releases flock even when a native call raises.
                os.close(fd)
        finally:
            q.mutex.release()
    except Skip as exc:
        result['reason'] = str(exc)
    except Exception as exc:
        result.update(outcome='error', reason=type(exc).__name__)
    result['elapsed_seconds'] = round(time.monotonic() - start, 6)
    logging.info('%s', json.dumps(result, sort_keys=True))
    return result
