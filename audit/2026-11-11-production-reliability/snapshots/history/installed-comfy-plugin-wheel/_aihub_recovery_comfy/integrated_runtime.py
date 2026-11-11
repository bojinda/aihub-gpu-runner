"""Explicitly configured runner composition and capability-scoped HTTP routes."""
import hmac
import os
from pathlib import Path
import re

from .core import encode, strict_json, RecoveryRequired
from .backends import HTTPTransport
from .integrated_control import IntegratedRecovery, provision
from .owned_execution import OwnedExecutionAdapter
from .practical_recovery import Refused

ACTOR=re.compile(r'^[A-Za-z0-9_-]{1,64}$')
ALLOWED={'schema','automatic_no_start','destructive','barrier_dir','target_kind','stop_helper','grace_seconds',
         'read_token_file','operate_token_file','ticket_key_file','qualification_process'}


def load_settings(path):
    value=strict_json(Path(path).read_bytes())
    if (not isinstance(value,dict) or set(value)-ALLOWED or value.get('schema')!=1 or
            any(type(value.get(k)) is not bool for k in ('automatic_no_start','destructive'))):
        raise ValueError('invalid_integrated_recovery_configuration')
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
            if method=='GET' and not body:
                if path=='/status':return reply(200,self.control.status())
                if path in ('/check','/check-emergency'):
                    return reply(200,self.control.check('emergency' if path=='/check-emergency' else 'recover',actor))
            if method=='POST' and path in ('/recover','/emergency','/verify'):
                if role!='operate':return reply(403,{'error':'operate_capability_required'})
                data=strict_json(body)
                if path=='/verify':
                    if not isinstance(data,dict) or set(data)!={'operation'} or not ACTOR.fullmatch(data['operation']):raise ValueError()
                    return reply(200,self.control.verify_interrupted(data['operation']))
                if (not isinstance(data,dict) or not {'token','confirmed'}<=set(data) or
                        set(data)-{'token','confirmed','accept_work_loss'}):raise ValueError()
                result=self.control.execute(data['token'],actor,data['confirmed'],data.get('accept_work_loss',False),expected_mode=path[1:])
                return reply(200 if result['result'] in ('recovered_ready','released_readiness_pending') else 409,result)
            return reply(404,{'error':'unsupported_recovery_action'})
        except (Refused,RecoveryRequired,TimeoutError) as exc:
            return reply(409,{'error':'recovery_refused','failed_checks':[str(exc)]})
        except (ValueError,TypeError,KeyError):return reply(400,{'error':'invalid_request'})
        except Exception:return reply(503,{'error':'recovery_state_unavailable','operator_required':True})


def configure_admission(admission):
    path=os.environ.get('AIHUB_RECOVERY_CONFIG')
    if not path:return None
    settings=load_settings(path)
    admission.integrated_settings=settings
    admission.automatic_no_start_enabled=settings['automatic_no_start']
    return settings
