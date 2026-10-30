"""DataUpdateCoordinator for Tomons / Inateck Wallbox."""

import logging
import time
import json
import os
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
    CONF_MAC,
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
        self.mac: str = entry.data.get(CONF_MAC, "AC:A7:04:88:43:02")
        self.firmware_version: Optional[str] = entry.data.get("firmware_version", None)
        self.current_limit_a: int = 16

        self.cloud: Optional[TomonsCloudClient] = None
        self.charger: Optional[TomonsCharger] = None

        self.session_energy_kwh: float = 0.0
        self.last_session_energy_kwh: float = 0.0
        self.cumulative_energy_kwh: float = 0.0
        self._session_start_hw_energy: float = 0.0
        self._running_session_kwh: float = 0.0
        self._last_power_timestamp: Optional[float] = None
        self._was_charging: bool = False

        self._energy_file: Optional[str] = None
        try:
            if hasattr(self.hass, "config") and hasattr(self.hass.config, "path"):
                self._energy_file = self.hass.config.path(f".{self.device_id}_energy.json")
            else:
                self._energy_file = f".{self.device_id}_energy.json"
        except Exception:
            self._energy_file = f".{self.device_id}_energy.json"

        self._load_energy_cache()

    def _load_energy_cache(self) -> None:
        """Load persistent cumulative and last session energy from disk."""
        if not self._energy_file:
            return
        try:
            if os.path.exists(self._energy_file):
                with open(self._energy_file, "r") as f:
                    data = json.load(f)
                    self.cumulative_energy_kwh = float(data.get("cumulative_energy_kwh", 0.0))
                    self.last_session_energy_kwh = float(data.get("last_session_energy_kwh", 0.0))
                    _LOGGER.info(
                        "Loaded persistent energy cache: cumulative=%.2f kWh, last_session=%.2f kWh",
                        self.cumulative_energy_kwh,
                        self.last_session_energy_kwh,
                    )
        except Exception as ex:
            _LOGGER.debug("Could not load energy cache: %s", ex)

    def _save_energy_cache(self) -> None:
        """Save persistent cumulative and last session energy to disk."""
        if not self._energy_file:
            return
        try:
            with open(self._energy_file, "w") as f:
                json.dump({
                    "cumulative_energy_kwh": round(self.cumulative_energy_kwh, 2),
                    "last_session_energy_kwh": round(self.last_session_energy_kwh, 2),
                }, f)
        except Exception as ex:
            _LOGGER.debug("Could not save energy cache: %s", ex)

    def restore_cumulative_energy(self, restored_kwh: float) -> None:
        """Restore cumulative energy from Home Assistant RestoreEntity."""
        if restored_kwh > self.cumulative_energy_kwh:
            _LOGGER.info(
                "Restoring cumulative energy from HA entity state: %.2f kWh (was %.2f kWh)",
                restored_kwh,
                self.cumulative_energy_kwh,
            )
            self.cumulative_energy_kwh = round(restored_kwh, 2)
            self._save_energy_cache()

    @property
    def total_energy_kwh(self) -> float:
        """Return cumulative total energy in kWh (monotonic total_increasing)."""
        running = self._running_session_kwh if self._was_charging else 0.0
        total = self.cumulative_energy_kwh + running
        if total == 0.0 and self.last_session_energy_kwh > 0:
            total = self.last_session_energy_kwh
        return round(total, 2)

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

            # Restore cached current limit from telemetry fragment manager if available
            cached_limit = getattr(self.charger.fragment_manager.last_telemetry, "max_current_a", None)
            if cached_limit:
                self.current_limit_a = cached_limit

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
        current = telemetry.current_a or 0.0
        status = telemetry.cable_status or ""

        # Charging session detection
        is_charging = (
            status == "Lädt aktiv"
            or ("Lädt" in status and power > 50)
            or (power > 200 and current > 1.0 and status != "Nicht verbunden (Standby)")
        )

        hw_energy = getattr(telemetry, "hardware_session_energy_kwh", None)
        hw_total = getattr(telemetry, "total_energy_kwh", None)
        if hw_total is not None and hw_total > 0 and hw_total > self.cumulative_energy_kwh:
            self.cumulative_energy_kwh = round(hw_total, 2)
            self._save_energy_cache()

        if is_charging:
            if not self._was_charging:
                # Started new charging session
                self._was_charging = True
                self.session_energy_kwh = 0.0
                if hw_energy is not None and (hw_energy <= 0.1 or hw_energy < self.last_session_energy_kwh):
                    self._session_start_hw_energy = 0.0
                else:
                    self._session_start_hw_energy = hw_energy if (hw_energy is not None and hw_energy > 0) else 0.0
                self._last_power_timestamp = now
                _LOGGER.info(
                    "Tomons Wallbox: Charging session started (start hw_energy=%.2f kWh)",
                    self._session_start_hw_energy,
                )
            else:
                if self._last_power_timestamp is not None:
                    dt_hours = (now - self._last_power_timestamp) / 3600.0
                    if 0 < dt_hours < 0.2:  # Sanity check: max gap 12 minutes
                        effective_power = power
                        if effective_power <= 0 and current > 0.5:
                            v = telemetry.voltage_v or 230.0
                            effective_power = round((v / 1.73205 * current * 3) if v > 300 else (v * current))
                        added_kwh = (effective_power / 1000.0) * dt_hours
                        self.session_energy_kwh += added_kwh
                self._last_power_timestamp = now

                # If hardware meter has reset for the new session, adjust start offset
                if hw_energy is not None and hw_energy < self._session_start_hw_energy:
                    self._session_start_hw_energy = 0.0

            # Calculate live running session energy
            running_hw = 0.0
            if hw_energy is not None:
                if hw_energy >= self._session_start_hw_energy:
                    running_hw = hw_energy - self._session_start_hw_energy
                else:
                    running_hw = hw_energy
            self._running_session_kwh = max(running_hw, self.session_energy_kwh)
        else:
            if self._was_charging:
                # Finished charging session!
                delivered_kwh = max(self._running_session_kwh, self.session_energy_kwh)
                if delivered_kwh > 0:
                    self.last_session_energy_kwh = round(delivered_kwh, 2)
                    self.cumulative_energy_kwh = round(self.cumulative_energy_kwh + delivered_kwh, 2)
                    self._save_energy_cache()
                self._was_charging = False
                self._last_power_timestamp = None
                self._session_start_hw_energy = 0.0
                self._running_session_kwh = 0.0
                _LOGGER.info(
                    "Tomons Wallbox: Charging session ended. Session: %.2f kWh, Total Cumulative: %.2f kWh",
                    self.last_session_energy_kwh,
                    self.cumulative_energy_kwh,
                )
            else:
                # Standby / Idle: seed cumulative_energy_kwh if 0 and hardware reports last session
                if self.cumulative_energy_kwh == 0.0 and hw_energy is not None and hw_energy > 0:
                    self.cumulative_energy_kwh = round(hw_energy, 2)
                    self.last_session_energy_kwh = round(hw_energy, 2)
                    self._save_energy_cache()
                    _LOGGER.info(
                        "Seeded cumulative energy from Wallbox last session: %.2f kWh",
                        self.cumulative_energy_kwh,
                    )
                self._running_session_kwh = 0.0

        # Sync configured current limit
        if telemetry.max_current_a is not None:
            self.current_limit_a = telemetry.max_current_a
        elif self.current_limit_a is not None:
            telemetry.max_current_a = self.current_limit_a

        # Keep telemetry total_energy_kwh consistent with coordinator
        telemetry.total_energy_kwh = self.total_energy_kwh

        # Thread-safe dispatch to Home Assistant event loop
        self.hass.loop.call_soon_threadsafe(self.async_set_updated_data, telemetry)

    async def _async_update_data(self) -> ChargerTelemetry:
        """Periodic heartbeat poll: send 0xCE01 status request to keep telemetry stream alive."""
        if self.charger:
            if not self.charger.is_connected:
                _LOGGER.info("Tomons Wallbox: MQTT connection lost, reconnecting...")
                await self.hass.async_add_executor_job(self.charger.reconnect)
            try:
                await self.hass.async_add_executor_job(self.charger.request_status)
            except Exception as err:
                _LOGGER.warning("Could not send status request to Wallbox: %s", err)
            telemetry = self.charger.get_telemetry()
            if telemetry.max_current_a is not None:
                self.current_limit_a = telemetry.max_current_a
            elif self.current_limit_a is not None:
                telemetry.max_current_a = self.current_limit_a
            telemetry.total_energy_kwh = self.total_energy_kwh
            return telemetry
        return ChargerTelemetry(max_current_a=self.current_limit_a, total_energy_kwh=self.total_energy_kwh)

    async def async_set_current_limit(self, amps: int) -> None:
        """Set charging current limit in Amperes (e.g. 6, 8, 10, 16, 32 A)."""
        if not self.charger:
            return
        _LOGGER.info("Setting Wallbox current limit to %d A", amps)
        self.current_limit_a = amps
        if not self.charger.is_connected:
            _LOGGER.info("Tomons Wallbox: MQTT disconnected, reconnecting before command...")
            await self.hass.async_add_executor_job(self.charger.reconnect)
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
