"""Explicitly configured runner composition and capability-scoped HTTP routes."""
import hmac
import logging,traceback,uuid
import os
from pathlib import Path
import re

from .core import encode, strict_json, RecoveryRequired
from .backends import HTTPTransport
from .integrated_control import IntegratedRecovery, provision
from .owned_execution import OwnedExecutionAdapter
from .practical_recovery import Refused
from .recovery_control import Blocked

ACTOR=re.compile(r'^[A-Za-z0-9_-]{1,64}$')
ALLOWED={'schema','automatic_no_start','destructive','barrier_dir','target_kind','stop_helper','grace_seconds',
         'read_token_file','operate_token_file','ticket_key_file','qualification_process','idle_only_manual'}


def load_settings(path):
    value=strict_json(Path(path).read_bytes())
    if (not isinstance(value,dict) or set(value)-ALLOWED or value.get('schema')!=1 or
            any(type(value.get(k)) is not bool for k in ('automatic_no_start','destructive'))):
        raise ValueError('invalid_integrated_recovery_configuration')
    if type(value.get('idle_only_manual',False)) is not bool:raise ValueError('invalid_idle_manual_configuration')
    for key in ('read_token_file','operate_token_file','ticket_key_file'):
        if not Path(value[key]).is_absolute():raise ValueError('absolute_secret_file_required')
    if value['destructive']:
        if value.get('target_kind','docker') not in ('docker','process'):raise ValueError('invalid_target_kind')
        if value.get('target_kind')=='process' and value.get('qualification_process') is not True:
            raise ValueError('process_target_only_for_disposable_qualification')
        if not Path(value['barrier_dir']).is_absolute():raise ValueError('absolute_barrier_path_required')
        if value.get('target_kind','docker')=='docker' and value.get('stop_helper')!='/usr/local/libexec/aihub-owned-stop':
            raise ValueError('fixed_privileged_helper_required')
    return value


class BoundedOwnedTransport(HTTPTransport):
    def _request(self,method,route,payload=None,params=None,timeout=5):
        return super()._request(method,route,payload,params,min(timeout,5))


class IntegratedApplication:
    def __init__(self,runner,settings,job_token):
        self.runner=runner;self.settings=settings
        self.read_token=Path(settings['read_token_file']).read_text().strip()
        self.operate_token=Path(settings['operate_token_file']).read_text().strip()
        key=Path(settings['ticket_key_file']).read_bytes()
        if (len(key)<32 or any(len(t)<43 for t in (self.read_token,self.operate_token)) or
                len({self.read_token,self.operate_token,job_token})!=3):raise ValueError('distinct_recovery_credentials_required')
        provision(runner.admission)
        adapter=OwnedExecutionAdapter(runner,settings) if settings['destructive'] else None
        self.control=IntegratedRecovery(runner,adapter,key,settings)
        self.idle=None
        if settings.get('idle_only_manual') is True:
            from .idle_recovery import IdleRecovery
            self.idle=IdleRecovery(runner,runner.recovery.observer,key)
        if adapter:
            runner.recovery.integrated_adapter=adapter
            backend=runner.backends['wallpaper']
            backend.transport=BoundedOwnedTransport(runner.targets['wallpaper'].base_url)
            adapter.restore_normal_receipts()

    def authenticated(self,headers):
        return self.role(headers) is not None

    def role(self,headers):
        auth=next((v for k,v in headers.items() if k.lower()=='authorization'),'')
        for role,token in (('read',self.read_token),('operate',self.operate_token)):
            if isinstance(auth,str) and hmac.compare_digest(auth.encode(),('Bearer '+token).encode()):return role

    def handle(self,method,path,headers,body=b''):
        def reply(code,obj):return code,'application/json',encode(obj)
        role=self.role(headers)
        if role is None:return reply(401,{'error':'unauthorized'})
        actor=next((v for k,v in headers.items() if k.lower()=='x-ha-user'),'')
        if not isinstance(actor,str) or not ACTOR.fullmatch(actor):return reply(400,{'error':'operator_identity_required'})
        if len(body)>16384:return reply(413,{'error':'request_too_large'})
        try:
            errors=self.configuration_errors()
            if errors and ((method=='GET' and path in ('/check','/check-emergency')) or
                           (method=='POST' and path in ('/recover','/emergency'))):
                return reply(200 if method=='GET' else 409,dict(eligible=False,classification='configuration_error',
                    error='recovery_configuration_error',failed_checks=errors,operator_required=True))
            if method=='GET' and not body:
                if path=='/status':return reply(200,self.status())
                if path in ('/check','/check-emergency'):
                    if path=='/check' and self.idle and self.idle_target():return reply(200,self.idle.check(actor))
                    return reply(200,self.control.check('emergency' if path=='/check-emergency' else 'recover',actor))
            if method=='POST' and path in ('/recover','/emergency','/verify'):
                if role!='operate':return reply(403,{'error':'operate_capability_required'})
                data=strict_json(body)
                if path=='/verify':
                    if not isinstance(data,dict) or set(data)!={'operation'} or not ACTOR.fullmatch(data['operation']):raise ValueError()
                    if self.idle:
                        from .maintenance import read_gate
                        from .idle_recovery import CASE
                        if read_gate(self.runner.store,True)['attempts'].get(data['operation'],{}).get('case')==CASE:
                            return reply(200,self.idle.reconcile(data['operation']))
                    return reply(200,self.control.verify_interrupted(data['operation']))
                if (not isinstance(data,dict) or not {'token','confirmed'}<=set(data) or
                        set(data)-{'token','confirmed','accept_work_loss'}):raise ValueError()
                if self.idle and path=='/recover' and isinstance(data['token'],str) and data['token'].startswith('idle1.'):
                    result=self.idle.recover(data['token'],actor,data['confirmed'])
                else:result=self.control.execute(data['token'],actor,data['confirmed'],data.get('accept_work_loss',False),expected_mode=path[1:])
                return reply(200 if result['result'] in ('recovered_ready','released_readiness_pending') else 409,result)
            return reply(404,{'error':'unsupported_recovery_action'})
        except Blocked as exc:
            return reply(409,{'error':'recovery_refused','failed_checks':exc.checks})
        except (Refused,RecoveryRequired,TimeoutError) as exc:
            return reply(409,{'error':'recovery_refused','failed_checks':[str(exc)]})
        except (ValueError,TypeError,KeyError):return reply(400,{'error':'invalid_request'})
        except Exception as exc:
            reference=uuid.uuid4().hex
            # Bounded server evidence, with no request, credentials or exception
            # message. HA gets an actionable reference instead of a silent 503.
            frames=traceback.extract_tb(exc.__traceback__)[-12:]
            logging.getLogger(__name__).error('recovery_check_internal_error reference=%s type=%s frames=%s',
                reference,type(exc).__name__,[(Path(f.filename).name,f.name,f.lineno) for f in frames])
            return reply(503,{'error':'recovery_check_internal_error','operator_required':True,
                'failed_checks':['recovery_service_internal_error'],'diagnostic_reference':reference})

    def idle_target(self):
        from .maintenance import read_gate
        from .idle_recovery import CASE
        gate=read_gate(self.runner.store,True)
        if gate['attempts'].get(gate['active'],{}).get('case')==CASE:return True
        owners=self.runner.admission.snapshot()['owners']
        return any(o.get('target')=='ollama' for o in owners.values())

    def configuration_errors(self):
        # Report a broken recovery installation without failing ordinary
        # admission or disabling the existing native automatic policy.
        return [] if self.runner.admission.control_required else ['mandatory_admission_gate_not_configured']

    def status(self):
        value=self.control.status()
        errors=self.configuration_errors()
        value.update(configuration_errors=errors,recovery_operational=not errors,
                     recovery_control_required=self.runner.admission.control_required)
        if errors:value['capabilities']=[]
        if not self.idle:return value
        idle_errors=self.idle.configuration_errors()
        value['configuration_errors']=errors+idle_errors
        value['recovery_operational']=not value['configuration_errors']
        from .maintenance import read_gate
        from .recovery_control import Blocked
        from .idle_recovery import CASE
        if not errors and not idle_errors:value['capabilities']=list(dict.fromkeys(value['capabilities']+['manual_idle_recovery_v1']))
        incidents=[]
        for owner in {o['lease_id']:o for o in value['owners'].values()}.values():
            job=self.runner.store.job(owner['job_id']);reasons=[]
            classification='existing_wallpaper_recovery' if owner['target']=='wallpaper' else 'unsupported_target'
            if owner['target']=='ollama':
                classification='manual_idle_check_required'
                try:self.idle.candidate()
                except Blocked as exc:reasons=exc.checks;classification='blocked'
                except Exception:reasons=['state_unavailable'];classification='blocked'
                if idle_errors:reasons=idle_errors;classification='configuration_error'
            future=self.runner.futures.get(owner['job_id'])
            supervised=bool(future and not future.done())
            if supervised:classification='active_supervision'
            incidents.append(dict(job_id=owner['job_id'],lease_id=owner['lease_id'],request_hash=owner['request_hash'],
                boot_id=owner['boot_id'],resources=job.get('resources'),phase=job.get('phase') or job.get('wallpaper_lifecycle',{}).get('phase'),
                classification=classification,operator_required=not supervised,failed_checks=reasons,error_code=job.get('error_code')))
        value['incidents']=incidents
        if incidents and all(i['classification']=='active_supervision' for i in incidents):value['operator_required']=False
        gate=read_gate(self.runner.store,True);attempt=gate['attempts'].get(gate['active'] or gate.get('last_operation'))
        if attempt and attempt.get('case')==CASE and (gate['active'] or not value.get('attempt') or
                value['attempt']['id'] in attempt['before_state'].get('practical_control',{}).get('attempts',{})):
            value['attempt']={k:attempt.get(k) for k in ('id','phase','job_id','checks','authority_intent')}
            if gate['active']:value.update(operator_required=True,maintenance='closed')
        return value


def configure_admission(admission):
    path=os.environ.get('AIHUB_RECOVERY_CONFIG')
    if not path:return None
    settings=load_settings(path)
    admission.integrated_settings=settings
    admission.automatic_no_start_enabled=settings['automatic_no_start']
    return settings
