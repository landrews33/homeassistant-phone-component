"""
Component to interface with various media players.

For more details about this component, please refer to the documentation at
https://home-assistant.io/components/media_player/
"""
import asyncio
from datetime import timedelta
import functools as ft
import hashlib
import logging
import os
from random import SystemRandom

from aiohttp import web
import async_timeout
import voluptuous as vol

from homeassistant.config import load_yaml_config_file
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA  # noqa
from homeassistant.helpers.deprecation import deprecated_substitute
from homeassistant.components.http import HomeAssistantView, KEY_AUTHENTICATED
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.util.async import run_coroutine_threadsafe
from homeassistant.const import (
   ATTR_ENTITY_ID, STATE_UNKNOWN)

_LOGGER = logging.getLogger(__name__)
_RND = SystemRandom()

DOMAIN = 'phone'
SCAN_INTERVAL = timedelta(seconds=10)

ENTITY_ID_FORMAT = DOMAIN + '.{}'

ATTR_PHONE_DESTINATION = "destination_number"
ATTR_PHONE_CALLERID = "callerid"
ATTR_PHONE_CALLERNAME = "callername"

CONTENT_TYPE_HEADER = 'Content-Type'

SERVICE_CALL = 'call_number'

SUPPORT_CALL = 1
SUPPORT_DIALEVENT = 2
SUPPORT_VM = 4
SUPPORT_CALL_END = 8
SUPPORT_CALL_FAIL = 16
SUPPORT_SMS = 32

# Service call validation schemas
PHONE_SCHEMA = vol.Schema({
    ATTR_ENTITY_ID: cv.entity_ids,
})

PHONE_CALL_SCHEMA = PHONE_SCHEMA.extend({

    vol.Required(ATTR_PHONE_DESTINATION): cv.string,
})

SERVICE_TO_METHOD = {
    SERVICE_CALL: {
       'method': 'async_call',
	'schema': PHONE_CALL_SCHEMA},
}

ATTR_TO_PROPERTY = [
    ATTR_PHONE_CALLERID,
]


def is_ringing(hass, entity_id=None):
    """
    Return true if specified media player entity_id is on.

    Check all media player if no entity_id specified.
    """
    entity_ids = [entity_id] if entity_id else hass.states.entity_ids(DOMAIN)
    return any(not hass.states.is_state(entity_id, STATE_OFF)
               for entity_id in entity_ids)


def call(hass, dest, entity_id=None):
    """Make a call using a phone"""
    data = {ATTR_PHONE_DESTINATION: dest}

    if entity_id:
        data[ATTR_ENTITY_ID] = entity_id

    hass.services.call(DOMAIN, SERVICE_CALL, data)

@asyncio.coroutine
def async_setup(hass, config):
    """Track states and offer events for media_players."""
    component = EntityComponent(
        logging.getLogger(__name__), DOMAIN, hass, SCAN_INTERVAL)

    """hass.http.register_view(MediaPlayerImageView(component.entities))"""

    yield from component.async_setup(config)

    descriptions = yield from hass.loop.run_in_executor(
        None, load_yaml_config_file, os.path.join(
            os.path.dirname(__file__), 'services.yaml'))

    @asyncio.coroutine
    def async_service_handler(service):
        """Map services to methods on MediaPlayerDevice."""
        method = SERVICE_TO_METHOD.get(service.service)
        if not method:
            return

        params = {}
        if service.service == SERVICE_CALL:
            params['dest'] = service.data.get(ATTR_PHONE_DESTINATION)
        target_phones = component.async_extract_from_service(service)

        update_tasks = []
        for phone in target_phones:
            yield from getattr(phone, method['method'])(**params)

        for phone in target_phones:
            if not phone.should_poll:
                continue

            update_coro = phone.async_update_ha_state(True)
            if hasattr(player, 'async_update'):
                update_tasks.append(update_coro)
            else:
                yield from update_coro

        if update_tasks:
            yield from asyncio.wait(update_tasks, loop=hass.loop)

    for service in SERVICE_TO_METHOD:
        schema = SERVICE_TO_METHOD[service].get(
            'schema', PHONE_SCHEMA)
        hass.services.async_register(
            DOMAIN, service, async_service_handler,
            descriptions.get(service), schema=schema)

    return True


class PhoneDevice(Entity):
    """ABC for phone devices."""

    _access_token = None
    _callerid = None
    _callername = None

    # pylint: disable=no-self-use
    # Implement these for your media player
    @property
    def state(self):
        """State of the player."""
        return STATE_UNKNOWN

    @property
    def access_token(self):
        """Access token for this media player."""
        if self._access_token is None:
            self._access_token = hashlib.sha256(
                _RND.getrandbits(256).to_bytes(32, 'little')).hexdigest()
        return self._access_token

    @property
    def caller_id(self):
        """Caller ID when phone is ringing or off-hook"""
        return None

    @property
    def caller_name(self):
        """Caller Name when phone is ringing or off-hook"""
        return None

    @property
    def supported_features(self):
        """Flag phone features that are supported."""
        return 0

    def call(self):
        """Turn the media player on."""
        raise NotImplementedError()

    def async_turn_on(self):
        """Turn the media player on.

        This method must be run in the event loop and returns a coroutine.
        """
        return self.hass.loop.run_in_executor(
            None, self.turn_on)

    # No need to overwrite these.
    @property
    def support_call(self):
        """Boolean if play is supported."""
        return bool(self.supported_features & SUPPORT_CALL)

    @property
    def support_dialevent(self):
        """Boolean if pause is supported."""
        return bool(self.supported_features & SUPPORT_DIALEVENT)

    @property
    def support_vm(self):
        """Boolean if stop is supported."""
        return bool(self.supported_features & SUPPORT_VM)

    @property
    def support_call_end(self):
        """Boolean if seek is supported."""
        return bool(self.supported_features & SUPPORT_CALL_END)

    @property
    def support_call_fail(self):
        """Boolean if setting volume is supported."""
        return bool(self.supported_features & SUPPORT_CALL_FAIL)

    @property
    def support_sms(self):
        """Boolean if muting volume is supported."""
        return bool(self.supported_features & SUPPORT_SMS)

    @property
    def state_attributes(self):
        """Return the state attributes."""

        state_attr = {
            attr: getattr(self, attr) for attr
            in ATTR_TO_PROPERTY if getattr(self, attr) is not None
        }

        return state_attr

