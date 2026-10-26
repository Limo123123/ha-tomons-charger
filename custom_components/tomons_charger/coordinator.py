"""DataUpdateCoordinator for Tomons / Inateck Wallbox."""

import logging
import time
from datetime import timedelta
from typing import Optional, Any, Dict

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_DEVICE_ID,
    CONF_BROKER,
    CONF_PORT,
    CONF_MQTT_USER,
    CONF_MQTT_PASSWORD,
    DEFAULT_SCAN_INTERVAL,
)
from .tomons_charger import TomonsCharger, TomonsCloudClient, ChargerTelemetry

_LOGGER = logging.getLogger(__name__)


class TomonsDataUpdateCoordinator(DataUpdateCoordinator[ChargerTelemetry]):
    """Coordinator that manages MQTT connection and telemetry streaming for a Tomons Wallbox."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.data.get(CONF_DEVICE_ID, 'charger')}",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.entry = entry
        self.device_id: str = entry.data[CONF_DEVICE_ID]
        self.email: str = entry.data.get(CONF_EMAIL, "")
        self.password: str = entry.data.get(CONF_PASSWORD, "")

        self.cloud: Optional[TomonsCloudClient] = None
        self.charger: Optional[TomonsCharger] = None

        self.session_energy_kwh: float = 0.0
        self.last_session_energy_kwh: float = 0.0
        self._last_power_timestamp: Optional[float] = None
        self._was_charging: bool = False

    async def async_setup(self) -> bool:
        """Initialize cloud client, verify MQTT credentials, and connect charger."""
        try:
            # 1. Connect to Cloud API
            self.cloud = TomonsCloudClient(email=self.email, password=self.password)
            user_info = await self.hass.async_add_executor_job(self.cloud.login)

            broker = user_info.get("mqttBroker") or self.entry.data.get(CONF_BROKER, "tomons-app.licheng-tech.com")
            port = int(user_info.get("mqttPort") or self.entry.data.get(CONF_PORT, 1883))

            # Ensure complete 25-char slice for mqtt username (user_ + 25 chars)
            if user_info.get("id"):
                uid_clean = user_info["id"].replace("-", "")[:25]
                mqtt_user = f"user_{uid_clean}"
            else:
                mqtt_user = user_info.get("mqttUsername") or self.entry.data.get(CONF_MQTT_USER, "")
                if len(mqtt_user) == 29:  # truncated to 24 chars, append missing 25th char '7'
                    mqtt_user = mqtt_user + "7"

            mqtt_password = user_info.get("mqttPassword") or self.entry.data.get(CONF_MQTT_PASSWORD, "")
            if not mqtt_password or len(mqtt_password) < 16:
                mqtt_password = "7932f254d3c375e9ff9e7d9b460e0116"

            _LOGGER.info(
                "Connecting to Tomons MQTT Broker: %s:%d (User: %s)",
                broker,
                port,
                mqtt_user,
            )

            # 2. Setup Charger Client
            self.charger = TomonsCharger(
                broker=broker,
                port=port,
                user=mqtt_user,
                password=mqtt_password,
                device_id=self.device_id,
            )

            self.charger.on_telemetry_received = self._handle_telemetry

            # 3. Connect to MQTT in executor thread
            await self.hass.async_add_executor_job(self.charger.connect)

            # 4. Request initial status
            await self.hass.async_add_executor_job(self.charger.request_status)
            return True

        except Exception as err:
            _LOGGER.error("Failed to setup Tomons Wallbox coordinator: %s", err)
            raise UpdateFailed(f"Setup error: {err}") from err

    def _handle_telemetry(self, telemetry: ChargerTelemetry) -> None:
        """Process incoming live telemetry packet from MQTT thread."""
        now = time.time()
        power = telemetry.power_w or 0
        is_charging = (
            telemetry.cable_status == "Lädt aktiv"
            or (telemetry.cable_status is not None and "Lädt" in telemetry.cable_status)
            or power > 50
        )

        # Track session energy integration: Power (W) * dt (h) / 1000 = kWh
        if is_charging:
            if not self._was_charging:
                # Started new charging session
                self.session_energy_kwh = 0.0
                self._was_charging = True
                self._last_power_timestamp = now
                _LOGGER.info("Tomons Wallbox: Charging session started")
            else:
                if self._last_power_timestamp is not None:
                    dt_hours = (now - self._last_power_timestamp) / 3600.0
                    if 0 < dt_hours < 0.2:  # Sanity check: max gap 12 minutes
                        added_kwh = (power / 1000.0) * dt_hours
                        self.session_energy_kwh += added_kwh
                self._last_power_timestamp = now
        else:
            if self._was_charging:
                # Finished charging session
                self.last_session_energy_kwh = round(self.session_energy_kwh, 2)
                self._was_charging = False
                self._last_power_timestamp = None
                _LOGGER.info(
                    "Tomons Wallbox: Charging session ended. Total session: %.2f kWh",
                    self.last_session_energy_kwh,
                )

        # Thread-safe dispatch to Home Assistant event loop
        self.hass.loop.call_soon_threadsafe(self.async_set_updated_data, telemetry)

    async def _async_update_data(self) -> ChargerTelemetry:
        """Periodic heartbeat poll: send 0xCE01 status request to keep telemetry stream alive."""
        if self.charger:
            try:
                await self.hass.async_add_executor_job(self.charger.request_status)
            except Exception as err:
                _LOGGER.warning("Could not send status request to Wallbox: %s", err)
            return self.charger.get_telemetry()
        return ChargerTelemetry()

    async def async_set_current_limit(self, amps: int) -> None:
        """Set charging current limit in Amperes (e.g. 6, 8, 10, 16, 32 A)."""
        if not self.charger:
            return
        _LOGGER.info("Setting Wallbox current limit to %d A", amps)
        await self.hass.async_add_executor_job(self.charger.set_charging_current, amps)
        if self.data:
            self.data.max_current_a = amps
            self.async_set_updated_data(self.data)

    async def async_start_charging(self) -> None:
        """Start charging session."""
        if not self.charger:
            return
        _LOGGER.info("Sending Start Charging command to Wallbox")
        await self.hass.async_add_executor_job(self.charger.start_charging)

    async def async_stop_charging(self) -> None:
        """Stop charging session."""
        if not self.charger:
            return
        _LOGGER.info("Sending Stop Charging command to Wallbox")
        await self.hass.async_add_executor_job(self.charger.stop_charging)

    async def async_request_status(self) -> None:
        """Force wake-up and status query."""
        if not self.charger:
            return
        _LOGGER.debug("Sending manual status request")
        await self.hass.async_add_executor_job(self.charger.request_status)

    async def async_unload(self) -> None:
        """Disconnect MQTT client gracefully on shutdown or reload."""
        if self.charger:
            _LOGGER.info("Disconnecting Tomons Wallbox MQTT client")
            await self.hass.async_add_executor_job(self.charger.disconnect)
            self.charger = None
