"""Fixed HTTPS transport. Credentials stay in the HA server, never in Lovelace."""
import json
import ipaddress
import ssl
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('redirect_forbidden')


class Bridge:
    def __init__(self, settings):
        url = urllib.parse.urlsplit(settings['url'])
        media = settings.get('transport') == 'media'
        private_http=False
        if media and settings.get('allow_private_http') is True and url.scheme=='http':
            try:
                address=ipaddress.ip_address(url.hostname)
                private_http=(address.is_loopback or address.version==4 and any(address in ipaddress.ip_network(n)
                    for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')))
            except ValueError:pass
        if (url.scheme != 'https' and not private_http or not url.hostname or url.username or url.password or
                url.path not in (('/gpu-recovery','/media/gpu-recovery') if media else ('','/')) or url.query or url.fragment):
            raise ValueError('fixed_https_origin_required')
        if settings['read_token'] == settings['operate_token'] or any(
                len(settings[k]) < 43 for k in ('read_token', 'operate_token')):
            raise ValueError('distinct_credentials_required')
        self.url, self.settings = settings['url'].rstrip('/'), settings
        ctx = ssl.create_default_context(cafile=settings.get('ca_file'))
        if not media or settings.get('client_cert'):
            ctx.load_cert_chain(settings['client_cert'], settings['client_key'])
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ctx), NoRedirect())

    def request(self, action, actor, payload=None):
        if action not in ('status', 'check', 'check-emergency', 'check-abort', 'recover', 'emergency', 'abort', 'verify','estop-status','estop-check','estop','estop-verify'):
            raise ValueError('action_not_allowed')
        write = action in ('recover', 'emergency', 'abort', 'verify','estop','estop-verify')
        key = 'operate_token' if write else 'read_token'
        req = urllib.request.Request(self.url+'/'+action,
            data=json.dumps(payload).encode() if write else None, method='POST' if write else 'GET',
            headers={'Authorization': 'Bearer '+self.settings[key], 'X-HA-User': actor,
                     'Content-Type': 'application/json', 'Connection': 'close'})
        try:
            response = self.opener.open(req, timeout=95)
        except urllib.error.HTTPError as exc:
            if exc.code not in (400, 401, 403, 409, 503):
                raise ValueError('unexpected_backend_status') from None
            response = exc
        with response:
            if response.headers.get_content_type() != 'application/json':
                raise ValueError('invalid_backend_response')
            data = response.read(131073)
            if len(data) > 131072:
                raise ValueError('backend_response_too_large')
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError('invalid_backend_response')
            return value


async def dispatch(hass, connection, msg):
    """Independently enforce human-admin authorization; no caller actor field."""
    user = connection.user
    if not user or not user.is_admin or not user.is_active or user.system_generated:
        connection.send_error(msg['id'], 'unauthorized', 'An active administrator is required.')
        return
    action = msg.get('action')
    keys = {'id', 'type', 'action'}
    if action in ('recover', 'emergency', 'abort','estop'):
        keys |= {'token', 'confirmed'}
        if action in ('recover','emergency') and 'accept_work_loss' in msg:
            keys.add('accept_work_loss')
    elif action in ('verify','estop-verify'):
        keys |= {'operation'}
    if set(msg) != keys or action not in ('status', 'check', 'check-emergency', 'check-abort', 'recover', 'emergency', 'abort', 'verify','estop-status','estop-check','estop','estop-verify'):
        connection.send_error(msg['id'], 'invalid_request', 'Unsupported recovery request.')
        return
    if action in ('recover', 'emergency', 'abort','estop') and (msg['confirmed'] is not True or
            not isinstance(msg['token'], str) or len(msg['token']) > 8192):
        connection.send_error(msg['id'], 'invalid_request', 'Exact confirmation is required.')
        return
    if ('accept_work_loss' in msg and msg['accept_work_loss'] is not True or
            action == 'emergency' and msg.get('accept_work_loss') is not True):
        connection.send_error(msg['id'], 'invalid_request', 'Explicit work-loss acceptance is required.')
        return
    if action in ('verify','estop-verify') and (not isinstance(msg['operation'], str) or len(msg['operation']) > 64):
        connection.send_error(msg['id'], 'invalid_request', 'Invalid operation.')
        return
    payload = {k: msg[k] for k in keys-{'id', 'type', 'action'}}
    try:
        result = await hass.async_add_executor_job(hass.data['aihub_recovery'].request, action, user.id, payload)
        connection.send_result(msg['id'], result)
    except Exception:
        # No retries; a timed-out POST may already have committed.
        connection.send_error(msg['id'], 'backend_unavailable',
            'Recovery state unavailable. Use Verify interrupted operation; do not repeat Recover.')
