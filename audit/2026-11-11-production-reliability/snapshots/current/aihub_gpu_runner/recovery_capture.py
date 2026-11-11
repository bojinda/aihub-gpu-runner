"""Recovery observations collected now, never reconstructed startup evidence."""
import time
from .core import RecoveryRequired, guest_boot_id, lock_identity
from .native_settlement import bound

PROVENANCE='ready_without_submission_recovery_capture_v1'

def eligible(a,state,job,worker=None):
    owner,_=bound(a,state,job)
    life=job['wallpaper_lifecycle']
    if (job.get('state')!='reconciling' or life.get('phase')!='ready' or
        job.get('execution_anchor') or worker and worker.get('active') or
        any(job.get(k) for k in ('backend_detail','completion_verified','cleanup_verified')) or
        any(k in life for k in ('submission_intent','prompt','result','artifact_sha256'))):
        raise RecoveryRequired('recovery_capture_requires_inactive_ready_without_submission')
    current=life['running_container'];pid=str(current['pid'])
    if (job.get('execution_container')!=current or not current['running'] or
        job.get('execution_processes')!={pid:life.get('original_pids',{}).get(pid)} or
        not life.get('original_pids',{}).get(pid)):
        raise RecoveryRequired('recovery_capture_original_generation_missing')
    return owner,current

def validate(record,job,state):
    life=job['wallpaper_lifecycle'];current=life['running_container'];anchor=record.get('execution_anchor',{})
    if (record.get('provenance')!=PROVENANCE or not isinstance(record.get('collected_at'),(int,float)) or
        record['collected_at']>time.time() or record.get('job_id')!=job['request_id'] or
        record.get('lease_id')!=job['lease_id'] or record.get('request_hash')!=job['request_hash'] or
        record.get('scope')!=state['scope'] or record.get('policy_id')!=state['recovery_policy']['policy_id'] or
        anchor.get('boot')!=guest_boot_id() or anchor.get('pid')!=current['pid'] or
        anchor.get('birth')!=life['original_pids'].get(str(current['pid'])) or
        record.get('container')!=current or record.get('lock_identity')!=state['lock_identity']):
        raise RecoveryRequired('recovery_capture_binding_changed')
    return record

def empty_barrier(s):
    if (s.get('mode')!='open' or 'operation' not in s or s['operation'] is not None or
        any(s.get(k)!={} for k in ('prompts','running','inflight')) or
        s.get('pending')!=[] or s.get('queue_intents',{})!={}):
        raise RecoveryRequired('recovery_capture_foreign_or_uncertain_submission')

def active_record(state,job):
    c=state.get('practical_control',{});attempt=c.get('attempts',{}).get(c.get('active'),{})
    r=attempt.get('observation_before',{}).get('recovery_capture')
    if not r:return None
    if (attempt.get('mode')!='recover' or attempt.get('approval')!='confirmed_exact_action' or attempt.get('lease')!=job['lease_id'] or
        attempt.get('job_before')!=job or c.get('admission')!='closed'):
        raise RecoveryRequired('recovery_capture_attempt_changed')
    return validate(r,job,state)
