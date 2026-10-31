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
    CONF_PRODUCT_MODEL,
    CONF_MAX_CURRENT_LIMIT,
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
        self.model_code: str = str(entry.data.get(CONF_PRODUCT_MODEL, "5242896"))
        self.max_hardware_current_a: int = self._determine_max_hardware_current()
        self.current_limit_a: int = min(16, self.max_hardware_current_a)

        self.cloud: Optional[TomonsCloudClient] = None
        self.charger: Optional[TomonsCharger] = None

        self.session_energy_kwh: float = 0.0
        self.last_session_energy_kwh: float = 0.0
        self.cumulative_energy_kwh: float = 0.0
        self._session_start_hw_energy: float = 0.0
        self._running_session_kwh: float = 0.0
        self._last_power_timestamp: Optional[float] = None
        self._was_charging: bool = False
        self.is_online: bool = False
        self._last_telemetry_time: Optional[float] = None

        # Anomaly and spike filter tracking (debouncing & confirmation re-checks)
        self._anomaly_counts: Dict[str, int] = {}
        self._last_valid_metrics: Dict[str, Any] = {}
        self._last_recheck_time: float = 0.0

        self._energy_file: Optional[str] = None
        try:
            if hasattr(self.hass, "config") and hasattr(self.hass.config, "path"):
                self._energy_file = self.hass.config.path(f".{self.device_id}_energy.json")
            else:
                self._energy_file = f".{self.device_id}_energy.json"
        except Exception:
            self._energy_file = f".{self.device_id}_energy.json"

        self._load_energy_cache()

    def _determine_max_hardware_current(self) -> int:
        """Determine maximum supported charging current in Amperes based on model code or options."""
        # 1. User manual override from config entry options
        opt_limit = self.entry.options.get(CONF_MAX_CURRENT_LIMIT)
        if opt_limit:
            try:
                opt_int = int(opt_limit)
                if opt_int in (16, 32):
                    return opt_int
            except (ValueError, TypeError):
                pass

        # 2. Check product model code (e.g. 5242896 for 16A, 5242912 for 32A)
        raw_model = self.entry.data.get(CONF_PRODUCT_MODEL) or self.model_code
        try:
            model_int = int(raw_model)
            # Low byte encodes max current rating: 0x10 -> 16 A, 0x20 -> 32 A
            low_byte = model_int & 0xFF
            if low_byte == 0x20 or model_int == 5242912:
                return 32
            if low_byte == 0x10 or model_int == 5242896:
                return 16
        except (ValueError, TypeError):
            pass

        if "32" in str(raw_model):
            return 32

        # 3. Default to 16 A (safe default for standard 11 kW CEE16 / 3.7 kW Schuko units)
        return 16

    @property
    def model_name(self) -> str:
        """Return human-readable model name including power rating and model code."""
        power_str = "11 kW / 16 A" if self.max_hardware_current_a == 16 else "22 kW / 32 A"
        return f"Tomons AC Wallbox {power_str} ({self.model_code})"

    def _is_metric_plausible(self, metric: str, val: Any) -> bool:
        """Check if telemetry value is within realistic physical boundaries."""
        if val is None:
            return True
        try:
            num = float(val)
        except (ValueError, TypeError):
            return False

        if metric == "power_w":
            # For 16 A / 11 kW: max ~13500 W. For 32 A / 22 kW: max ~25000 W.
            max_power = 13500 if self.max_hardware_current_a <= 16 else 25000
            return 0 <= num <= max_power

        elif metric == "voltage_v":
            if num == 0.0:
                return True
            # European grid: nominal 230 V (1-phase: 180..265 V) or 400 V (3-phase: 320..445 V)
            # Spikes to 480 V are strictly rejected as unphysical
            return (180.0 <= num <= 265.0) or (320.0 <= num <= 445.0)

        elif metric == "current_a":
            # Max current: 18.5 A for 16 A box, 36.0 A for 32 A box
            max_current = 18.5 if self.max_hardware_current_a <= 16 else 36.0
            return 0.0 <= num <= max_current

        elif metric == "temperature_c":
            return -25.0 <= num <= 75.0

        return True

    def _filter_and_verify_telemetry(self, telemetry: ChargerTelemetry) -> ChargerTelemetry:
        """Filter unrealistic spikes, request confirmation re-checks, and debounce anomalies."""
        metrics_to_check = ["power_w", "voltage_v", "current_a", "temperature_c"]
        has_unconfirmed_anomaly = False
        confirmation_threshold = 3

        for metric in metrics_to_check:
            val = getattr(telemetry, metric, None)
            if val is None:
                continue

            if self._is_metric_plausible(metric, val):
                # Normal valid value: reset anomaly counter and update last known good value
                self._anomaly_counts[metric] = 0
                self._last_valid_metrics[metric] = val
            else:
                # Unrealistic spike detected!
                count = self._anomaly_counts.get(metric, 0) + 1
                self._anomaly_counts[metric] = count

                if count < confirmation_threshold:
                    # Unconfirmed anomaly: suppress the spike and restore last known valid value
                    has_unconfirmed_anomaly = True
                    fallback = self._last_valid_metrics.get(metric)
                    if fallback is None:
                        fallback = 0 if metric == "power_w" else (0.0 if metric == "current_a" else (400.0 if metric == "voltage_v" else 20.0))

                    _LOGGER.warning(
                        "Tomons Wallbox: Unrealistic %s spike detected (%s). "
                        "Suppressing spike (attempt %d/%d) and requesting immediate re-check from Wallbox...",
                        metric,
                        val,
                        count,
                        confirmation_threshold,
                    )
                    setattr(telemetry, metric, fallback)
                else:
                    # Confirmed repeatedly (>= 3 times): accept the value
                    _LOGGER.warning(
                        "Tomons Wallbox: %s value %s confirmed %d times consecutively; accepting value.",
                        metric,
                        val,
                        count,
                    )
                    self._last_valid_metrics[metric] = val

        if has_unconfirmed_anomaly:
            self._schedule_recheck_threadsafe()

        return telemetry

    def _schedule_recheck_threadsafe(self) -> None:
        """Thread-safe scheduling of an immediate status query to recheck suspected spikes."""
        now = time.time()
        if (now - self._last_recheck_time) < 1.5:
            return  # Rate-limit to prevent spamming
        self._last_recheck_time = now

        try:
            if hasattr(self.hass, "loop") and self.hass.loop and self.hass.loop.is_running():
                self.hass.loop.call_soon_threadsafe(self._async_trigger_recheck_task)
            else:
                self._async_trigger_recheck_task()
        except Exception as ex:
            _LOGGER.debug("Could not schedule spike re-check task: %s", ex)

    def _async_trigger_recheck_task(self) -> None:
        """Create async task on HA event loop to send status request."""
        try:
            if hasattr(self.hass, "async_create_task"):
                self.hass.async_create_task(self.async_request_status())
        except Exception as ex:
            _LOGGER.debug("Error creating async status request task: %s", ex)

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

            # Check initial device online status via cloud API
            try:
                dev_status = await self.hass.async_add_executor_job(self.cloud.get_device_status, self.device_id)
                self.is_online = (dev_status.get("device_status") == 1)
                _LOGGER.info("Tomons Wallbox initial online status from cloud: %s", self.is_online)
            except Exception as err:
                _LOGGER.debug("Could not get initial cloud device status: %s", err)
                self.is_online = False

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
        self._last_telemetry_time = now
        self.is_online = True
        telemetry.is_online = True

        # Apply anomaly and spike filter with verification re-queries
        telemetry = self._filter_and_verify_telemetry(telemetry)

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
                        max_allowed_power = 13500 if self.max_hardware_current_a <= 16 else 25000
                        if effective_power > max_allowed_power:
                            effective_power = max_allowed_power
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

        # Sync configured current limit and auto-detect 32A hardware capability if reported
        if telemetry.max_current_a is not None:
            if telemetry.max_current_a > 16 and self.max_hardware_current_a < 32:
                if not self.entry.options.get(CONF_MAX_CURRENT_LIMIT):
                    _LOGGER.info(
                        "Tomons Wallbox reported current limit %d A > 16 A; upgrading hardware limit to 32 A",
                        telemetry.max_current_a,
                    )
                    self.max_hardware_current_a = 32
            self.current_limit_a = telemetry.max_current_a
        elif self.current_limit_a is not None:
            telemetry.max_current_a = self.current_limit_a

        # Keep telemetry total_energy_kwh consistent with coordinator
        telemetry.total_energy_kwh = self.total_energy_kwh

        # Thread-safe dispatch to Home Assistant event loop
        self.hass.loop.call_soon_threadsafe(self.async_set_updated_data, telemetry)

    async def _async_update_data(self) -> ChargerTelemetry:
        """Periodic heartbeat poll: check online status, keep telemetry stream alive."""
        now = time.time()

        recently_active = (
            self._last_telemetry_time is not None
            and (now - self._last_telemetry_time) < 70.0
        )

        if not recently_active and self.cloud:
            try:
                dev_status = await self.hass.async_add_executor_job(self.cloud.get_device_status, self.device_id)
                self.is_online = (dev_status.get("device_status") == 1)
            except Exception as err:
                _LOGGER.debug("Error checking cloud device status: %s", err)
                if self._last_telemetry_time is None or (now - self._last_telemetry_time) > 90.0:
                    self.is_online = False
        else:
            if recently_active:
                self.is_online = True

        if self.charger:
            if not self.charger.is_connected:
                _LOGGER.info("Tomons Wallbox: MQTT connection lost, reconnecting...")
                await self.hass.async_add_executor_job(self.charger.reconnect)
            try:
                await self.hass.async_add_executor_job(self.charger.request_status)
            except Exception as err:
                _LOGGER.warning("Could not send status request to Wallbox: %s", err)
            telemetry = self.charger.get_telemetry()
            telemetry = self._filter_and_verify_telemetry(telemetry)
            if telemetry.max_current_a is not None:
                self.current_limit_a = telemetry.max_current_a
            elif self.current_limit_a is not None:
                telemetry.max_current_a = self.current_limit_a
            telemetry.total_energy_kwh = self.total_energy_kwh
            telemetry.is_online = self.is_online

            if not self.is_online:
                telemetry.cable_status = "Offline"
                telemetry.power_w = 0
                telemetry.current_a = 0.0

            return telemetry
        return ChargerTelemetry(
            max_current_a=self.current_limit_a,
            total_energy_kwh=self.total_energy_kwh,
            is_online=self.is_online,
            cable_status="Offline" if not self.is_online else "Nicht verbunden (Standby)",
        )

    async def async_set_current_limit(self, amps: int) -> None:
        """Set charging current limit in Amperes (e.g. 6, 8, 10, 16, 32 A)."""
        if not self.charger:
            return
        if amps > self.max_hardware_current_a:
            _LOGGER.warning(
                "Requested current limit %d A exceeds hardware maximum %d A; clamping to %d A",
                amps,
                self.max_hardware_current_a,
                self.max_hardware_current_a,
            )
            amps = self.max_hardware_current_a
        elif amps < 6:
            amps = 6
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
