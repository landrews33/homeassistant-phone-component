"""
Support for MQTT switches.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/switch.mqtt/
"""
import asyncio
import logging

import voluptuous as vol

from homeassistant.core import callback
from homeassistant.components.mqtt import (
    CONF_STATE_TOPIC, CONF_COMMAND_TOPIC, CONF_QOS, CONF_RETAIN)
from custom_components.phone import (
    SUPPORT_CALL, SUPPORT_DIALEVENT, SUPPORT_VM, SUPPORT_CALL_END, SUPPORT_CALL_FAIL, SUPPORT_SMS, PLATFORM_SCHEMA)
from homeassistant.helpers.entity_component import EntityComponent

from custom_components.phone import PhoneDevice
from homeassistant.const import (
    CONF_NAME, CONF_OPTIMISTIC, CONF_VALUE_TEMPLATE, CONF_PAYLOAD_OFF,
    CONF_PAYLOAD_ON)
import homeassistant.components.mqtt as mqtt
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = ['mqtt']

DOMAIN ='phone'

DEFAULT_NAME = 'MQTT Phone'
DEFAULT_PAYLOAD_IDLE = 'idle'
DEFAULT_PAYLOAD_RINGING = 'ringing'
DEFAULT_PAYLOAD_OFFHOOK = 'offhook'
DEFAULT_OPTIMISTIC = False

CONF_CALLERID_TEMPLATE = 'callerid_template'
CONF_CALLERNAME_TEMPLATE = 'callername_template'
CONF_PAYLOAD_IDLE= 'payload_idle'
CONF_PAYLOAD_RINGING = 'payload_ringing'
CONF_PAYLOAD_OFFHOOK = 'payload_offhook'

SUPPORT_MQTT = SUPPORT_CALL | SUPPORT_DIALEVENT | SUPPORT_CALL_END | SUPPORT_CALL_FAIL
ATTR_CALLERID = 'callerid'

def valid_subscribe_topic(value, invalid_chars='\0'):
    """Validate that we can subscribe using this MQTT topic."""
    value = cv.string(value)
    if all(c not in value for c in invalid_chars):
        return vol.Length(min=1, max=65535)(value)
    raise vol.Invalid('Invalid MQTT topic name')


def valid_publish_topic(value):
    """Validate that we can publish using this MQTT topic."""
    return valid_subscribe_topic(value, invalid_chars='#+\0')


def valid_discovery_topic(value):
    """Validate a discovery topic."""
    return valid_subscribe_topic(value, invalid_chars='#+\0/')


PLATFORM_SCHEMA = mqtt.MQTT_RW_PLATFORM_SCHEMA.extend({
            vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
            vol.Optional('payload_idle', default=DEFAULT_PAYLOAD_IDLE): cv.string,
            vol.Optional('payload_ringing', default=DEFAULT_PAYLOAD_RINGING): cv.string,
            vol.Optional('payload_offhook', default=DEFAULT_PAYLOAD_OFFHOOK): cv.string,
            vol.Optional(CONF_OPTIMISTIC, default=DEFAULT_OPTIMISTIC): cv.boolean,
            vol.Required(CONF_CALLERID_TEMPLATE): cv.template,
            vol.Optional(CONF_CALLERNAME_TEMPLATE): cv.template,
    })


@asyncio.coroutine
def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up the MQTT phone."""
    if discovery_info is not None:
        config = CONFIG_SCHEMA(discovery_info)

    value_template = config.get(CONF_VALUE_TEMPLATE)
    if value_template is not None:
        value_template.hass = hass

    callerid_template = config.get(CONF_CALLERID_TEMPLATE)
    if callerid_template is not None:
        callerid_template.hass = hass

    async_add_devices([MqttPhone(
        config.get(CONF_NAME),
        config.get(CONF_STATE_TOPIC),
        config.get(CONF_COMMAND_TOPIC),
        config.get(CONF_QOS),
        config.get(CONF_RETAIN),
        config.get(CONF_PAYLOAD_IDLE),
        config.get(CONF_PAYLOAD_RINGING),
        config.get(CONF_PAYLOAD_OFFHOOK),
        config.get(CONF_OPTIMISTIC),
        value_template,
        callerid_template,
    )])




class MqttPhone(PhoneDevice):
    """Representation of a phone that can be toggled using MQTT."""

    def __init__(self, name, state_topic, command_topic, qos, retain,
                 payload_idle, payload_ringing, payload_offhook, optimistic, value_template, callerid_template):
        """Initialize the MQTT switch."""
        self._state = 'Idle'
        self._name = name
        self._state_topic = state_topic
        self._command_topic = command_topic
        self._qos = qos
        self._retain = retain
        self._payload_idle = payload_idle
        self._payload_ringing = payload_ringing
        self._payload_offhook = payload_offhook
        self._optimistic = optimistic
        self._template = value_template
        self._callerid_template = callerid_template

    @asyncio.coroutine
    def async_added_to_hass(self):
        """Subscribe mqtt events.

        This method is a coroutine.
        """
        @callback
        def message_received(topic, payload, qos):
            """A new MQTT message has been received."""
            if self._template is not None:
                _LOGGER.info("I have a template and %s", payload)
                self._callerid = self._callerid_template.async_render_with_possible_json_value(payload)
                _LOGGER.info("Callerid template produced %s", payload)
                payload = self._template.async_render_with_possible_json_value(
                    payload)
                _LOGGER.info("Value template produced %s", payload)
            if payload == self._payload_idle:
                _LOGGER.info("Payload is idle")
                self._state = "Idle"
            elif payload == self._payload_ringing:
                self._state = "Ringing"
            elif payload == self._payload_offhook:
                self._state = "Offhook"
                self._callerid = None
                self._callername = None
            self.hass.async_add_job(self.async_update_ha_state())

        if self._state_topic is None:
            # Force into optimistic mode.
            self._optimistic = True
        else:
            yield from mqtt.async_subscribe(
                self.hass, self._state_topic, message_received, self._qos)

    @property
    def should_poll(self):
        """No polling needed."""
        return False

    @property
    def state(self):
        return self._state

    @property
    def name(self):
        """Return the name of the switch."""
        return self._name

    @property
    def device_state_attributes(self):
        """Return the state attributes."""
        return {
            ATTR_CALLERID: self._callerid
        }


    @asyncio.coroutine
    def async_call(self, **kwargs):
        """Turn the device on.

        This method is a coroutine.
        """
        mqtt.async_publish(
            self.hass, self._command_topic, self._payload_on, self._qos,
            self._retain)
        if self._optimistic:
            # Optimistically assume that switch has changed state.
            self._state = True
            self.hass.async_add_job(self.async_update_ha_state())

