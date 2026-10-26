"""Config flow for Tomons / Inateck Wallbox integration."""

import logging
from typing import Any, Dict, Optional
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DOMAIN,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_MAC,
    CONF_PRODUCT_MODEL,
    CONF_CHIP_MODEL,
    CONF_BROKER,
    CONF_PORT,
    CONF_MQTT_USER,
    CONF_MQTT_PASSWORD,
)
from .tomons_charger import TomonsCloudClient

_LOGGER = logging.getLogger(__name__)


class TomonsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tomons / Inateck Wallbox."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._email: Optional[str] = None
        self._password: Optional[str] = None
        self._user_info: Optional[Dict[str, Any]] = None
        self._devices: list = []

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the initial step: prompt for Tomons email and password."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            password = user_input[CONF_PASSWORD].strip()

            try:
                client = TomonsCloudClient(email=email, password=password)
                user_info = await self.hass.async_add_executor_job(client.login)
                devices = await self.hass.async_add_executor_job(client.get_devices)

                if not devices:
                    errors["base"] = "no_devices"
                else:
                    self._email = email
                    self._password = password
                    self._user_info = user_info
                    self._devices = devices

                    if len(devices) == 1:
                        return self._create_entry_from_device(devices[0])

                    return await self.async_step_device()

            except Exception as err:
                _LOGGER.error("Authentication failed: %s", err)
                errors["base"] = "invalid_auth"

        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_device(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle selecting a device if multiple wallboxes exist in the account."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            chosen_auth_id = user_input[CONF_DEVICE_ID]
            for dev in self._devices:
                if dev.get("auth_id") == chosen_auth_id:
                    return self._create_entry_from_device(dev)

        device_options = {
            d.get("auth_id", ""): f"{d.get('product_model_code', 'Tomons Wallbox')} ({d.get('mac_address', d.get('auth_id', '')[:8])})"
            for d in self._devices
        }

        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE_ID): vol.In(device_options),
            }
        )

        return self.async_show_form(
            step_id="device",
            data_schema=schema,
            errors=errors,
        )

    def _create_entry_from_device(self, device: Dict[str, Any]) -> FlowResult:
        """Create a config entry from a discovered device record."""
        auth_id = device.get("auth_id", "")
        mac = device.get("mac_address", "")
        product_model = device.get("product_model_code", "Tomons Wallbox")
        chip_model = device.get("chip_model_code", "")

        # Use auth_id as unique ID to prevent duplicates
        self._abort_if_unique_id_configured(updates={CONF_DEVICE_ID: auth_id})

        title = f"{product_model} ({mac if mac else auth_id[:8]})"

        entry_data = {
            CONF_EMAIL: self._email,
            CONF_PASSWORD: self._password,
            CONF_DEVICE_ID: auth_id,
            CONF_DEVICE_NAME: title,
            CONF_MAC: mac,
            CONF_PRODUCT_MODEL: product_model,
            CONF_CHIP_MODEL: chip_model,
            CONF_BROKER: self._user_info.get("mqttBroker", "tomons-app.licheng-tech.com"),
            CONF_PORT: int(self._user_info.get("mqttPort", 1883)),
            CONF_MQTT_USER: self._user_info.get("mqttUsername", ""),
            CONF_MQTT_PASSWORD: self._user_info.get("mqttPassword", ""),
        }

        return self.async_create_entry(title=title, data=entry_data)
