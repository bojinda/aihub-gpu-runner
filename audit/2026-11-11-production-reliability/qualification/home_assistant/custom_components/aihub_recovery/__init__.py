"""Isolated HA integration; does not register or alter acknowledgment actions."""
import probatio as vol
from homeassistant.components import websocket_api
from homeassistant.helpers import config_validation as cv
from .bridge import Bridge, dispatch

DOMAIN = 'aihub_recovery'
CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema({
    vol.Required('url'): cv.string,
    vol.Required('read_token'): cv.string,
    vol.Required('operate_token'): cv.string,
    vol.Optional('transport',default='legacy'): vol.In(['legacy','media']),
    vol.Optional('allow_private_http',default=False): cv.boolean,
    vol.Optional('ca_file'): cv.string,
    vol.Optional('client_cert'): cv.string,
    vol.Optional('client_key'): cv.string,
})}, extra=vol.ALLOW_EXTRA)


@websocket_api.websocket_command({
    'type': 'aihub_recovery/control',
    vol.Required('action'): str,
    vol.Optional('token'): str,
    vol.Optional('confirmed'): bool,
    vol.Optional('accept_work_loss'): bool,
    vol.Optional('operation'): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def command(hass, connection, msg):
    await dispatch(hass, connection, msg)


async def async_setup(hass, config):
    if DOMAIN not in config:
        return True
    hass.data[DOMAIN] = await hass.async_add_executor_job(Bridge, config[DOMAIN])
    websocket_api.async_register_command(hass, command)
    return True
