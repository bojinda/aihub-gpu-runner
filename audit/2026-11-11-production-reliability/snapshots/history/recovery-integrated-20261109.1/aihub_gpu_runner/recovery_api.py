"""Separate capability-limited API; mount behind mutually authenticated TLS.

No listener is added to the normal job API. HA keeps credentials server-side.
No operation accepts a job, lease, evidence, command, URL, threshold or path.
"""
import hmac
import re
import ssl
from .core import encode, strict_json
from .recovery_control import Blocked

ACTOR = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')


class RecoveryApplication:
    max_request_bytes = 16384

    def __init__(self, control, read_token, operate_token):
        if (any(not isinstance(v, str) or len(v) < 43 for v in (read_token, operate_token)) or
                read_token == operate_token):
            raise ValueError('distinct_high_entropy_credentials_required')
        self.control, self.read_token, self.operate_token = control, read_token, operate_token

    def role(self, headers):
        auth = next((v for k, v in headers.items() if k.lower() == 'authorization'), '')
        if not isinstance(auth, str):
            return None
        for role, token in (('operate', self.operate_token), ('read', self.read_token)):
            if hmac.compare_digest(auth.encode(), ('Bearer '+token).encode()):
                return role
        return None

    def authenticated(self, headers):
        return self.role(headers) is not None

    def handle(self, method, path, headers, body=b''):
        role = self.role(headers)
        def reply(code, value):
            return code, 'application/json', encode(value)
        if not role:
            return reply(401, {'error': 'unauthorized'})
        if len(body) > self.max_request_bytes:
            return reply(413, {'error': 'request_too_large'})
        actor = next((v for k, v in headers.items() if k.lower() == 'x-ha-user'), '')
        if not isinstance(actor, str) or not ACTOR.fullmatch(actor):
            return reply(400, {'error': 'operator_identity_required'})
        # HA is the authenticated identity bridge. X-HA-User is never trusted
        # from a browser or unauthenticated reverse-proxy header.
        try:
            if method == 'GET' and not body:
                if path == '/status':
                    return reply(200, self.control.status())
                if path in ('/check', '/check-abort'):
                    check = self.control.check if path == '/check' else self.control.check_abort
                    return reply(200, check(actor))
            if method == 'POST' and path in ('/recover', '/abort', '/verify'):
                if role != 'operate':
                    return reply(403, {'error': 'operation_capability_required'})
                value = strict_json(body)
                if path == '/verify':
                    if not isinstance(value, dict) or set(value) != {'operation'} or not ACTOR.fullmatch(value['operation']):
                        raise ValueError()
                    return reply(200, self.control.reconcile(value['operation']))
                if not isinstance(value, dict) or set(value) != {'token', 'confirmed'}:
                    raise ValueError()
                operation = self.control.recover if path == '/recover' else self.control.abort
                result = operation(value['token'], actor, value['confirmed'])
                return reply(200 if result['result'] in ('succeeded', 'aborted') else 409, result)
            return reply(404, {'error': 'not_found'})
        except Blocked as exc:
            return reply(409, {'error': 'blocked', 'failed_checks': exc.checks})
        except (ValueError, TypeError, UnicodeError):
            return reply(400, {'error': 'invalid_request'})
        except TimeoutError:
            return reply(409, {'error': 'control_busy'})
        except Exception:
            return reply(503, {'error': 'state_or_backend_unavailable', 'operator_required': True})


def create_application(runner, *, read_token, operate_token, ticket_key, job_token, observer=None):
    """Explicit integration seam; never enabled by an existing configuration."""
    from .recovery_control import RecoveryControl
    from .recovery_observer import UnavailableObserver
    if not runner.admission.control_required or job_token in (read_token, operate_token):
        raise ValueError('mandatory_gate_and_distinct_capabilities_required')
    control = RecoveryControl(runner, observer or UnavailableObserver(), ticket_key)
    return RecoveryApplication(control, read_token, operate_token)


def serve_secure(application, *, server_certificate, server_key, ha_client_ca,
                 host='127.0.0.1', port=9443):
    """Only HA certificates signed by the dedicated recovery-client CA connect.

    Explicitly invoked by a future runner composition after qualification; no
    background process, service installation or enabling CLI in this candidate.
    """
    from .api import serve
    if not isinstance(application, RecoveryApplication):
        raise ValueError('recovery_application_required')
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cafile=ha_client_ca)
    ctx.load_cert_chain(server_certificate, server_key)
    serve(application, host, port, tls_context=ctx)
