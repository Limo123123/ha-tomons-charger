"""Sensors for Tomons / Inateck Wallbox."""

from typing import Optional, Any
from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfElectricPotential,
    UnitOfElectricCurrent,
    UnitOfPower,
    UnitOfEnergy,
    UnitOfTemperature,
    UnitOfTime,
    EntityCategory,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, MANUFACTURER, CONF_DEVICE_NAME, CONF_PRODUCT_MODEL, CONF_MAC
from .coordinator import TomonsDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tomons Wallbox sensors from config entry."""
    coordinator: TomonsDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        TomonsVoltageSensor(coordinator),
        TomonsCurrentSensor(coordinator),
        TomonsPowerSensor(coordinator),
        TomonsTotalEnergySensor(coordinator),
        TomonsSessionEnergySensor(coordinator),
        TomonsSessionDurationSensor(coordinator),
        TomonsTemperatureSensor(coordinator),
        TomonsCurrentLimitSensor(coordinator),
        TomonsCableStatusSensor(coordinator),
        TomonsModeSensor(coordinator),
        TomonsMacAddressSensor(coordinator),
    ]

    async_add_entities(entities)


class TomonsBaseSensor(CoordinatorEntity[TomonsDataUpdateCoordinator], SensorEntity):
    """Base sensor for Tomons Wallbox."""

    def __init__(self, coordinator: TomonsDataUpdateCoordinator, sensor_type: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._sensor_type = sensor_type
        self._attr_unique_id = f"{coordinator.device_id}_{sensor_type}"
        self._attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information with MAC network connection for router linking."""
        mac = self.coordinator.entry.data.get(CONF_MAC) or getattr(self.coordinator, "mac", "AC:A7:04:88:43:02")
        connections = {(CONNECTION_NETWORK_MAC, mac)} if mac else set()
        model_code = self.coordinator.entry.data.get(CONF_PRODUCT_MODEL, "5242896")

        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device_id)},
            name=self.coordinator.entry.data.get(CONF_DEVICE_NAME, "Tomons Wallbox"),
            manufacturer=MANUFACTURER,
            model=f"Tomons AC Wallbox ({model_code})",
            connections=connections,
            sw_version=getattr(self.coordinator, "firmware_version", None),
        )


class TomonsVoltageSensor(TomonsBaseSensor):
    """Voltage sensor (V)."""

    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_translation_key = "voltage"

    def __init__(self, coordinator: TomonsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "voltage")
        self._attr_name = "Netzspannung"

    @property
    def native_value(self) -> Optional[float]:
        if self.coordinator.data and self.coordinator.data.voltage_v is not None:
            return round(self.coordinator.data.voltage_v, 1)
        return None


class TomonsCurrentSensor(TomonsBaseSensor):
    """Current sensor (A)."""

    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_translation_key = "current"

    def __init__(self, coordinator: TomonsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "current")
        self._attr_name = "Ladestrom"

    @property
    def native_value(self) -> Optional[float]:
        if self.coordinator.data and self.coordinator.data.current_a is not None:
            return round(self.coordinator.data.current_a, 2)
        return None


class TomonsPowerSensor(TomonsBaseSensor):
    """Active Power sensor (W)."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_translation_key = "power"

    def __init__(self, coordinator: TomonsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "power")
        self._attr_name = "Wirkleistung"

    @property
    def native_value(self) -> Optional[int]:
        if self.coordinator.data and self.coordinator.data.power_w is not None:
            return int(self.coordinator.data.power_w)
        return None


class TomonsTotalEnergySensor(TomonsBaseSensor, RestoreEntity):
    """Cumulative Total Energy sensor (kWh). Compatible with Home Assistant Energy Dashboard."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_translation_key = "total_energy"

    def __init__(self, coordinator: TomonsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "total_energy")
        self._attr_name = "Gesamtenergie"

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added to HA and restore last state."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (None, "unknown", "unavailable"):
            try:
                restored_energy = float(last_state.state)
                if restored_energy > 0:
                    self.coordinator.restore_cumulative_energy(restored_energy)
            except (ValueError, TypeError):
                pass

    @property
    def native_value(self) -> Optional[float]:
        return self.coordinator.total_energy_kwh


class TomonsSessionEnergySensor(TomonsBaseSensor, RestoreEntity):
    """Session Energy / 'Letzte Ladung' sensor (kWh)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_translation_key = "session_energy"

    def __init__(self, coordinator: TomonsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "session_energy")
        self._attr_name = "Ladeenergie Letzte Ladung"

    async def async_added_to_hass(self) -> None:
        """Restore last session energy on startup if coordinator has no data yet."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (None, "unknown", "unavailable"):
            try:
                val = float(last_state.state)
                if val > 0 and self.coordinator.last_session_energy_kwh == 0.0:
                    self.coordinator.last_session_energy_kwh = round(val, 2)
            except (ValueError, TypeError):
                pass

    @property
    def native_value(self) -> float:
        if self.coordinator._was_charging:
            return round(self.coordinator._running_session_kwh, 2)
        if self.coordinator.last_session_energy_kwh > 0:
            return round(self.coordinator.last_session_energy_kwh, 2)
        if self.coordinator.data:
            hw_energy = getattr(self.coordinator.data, "hardware_session_energy_kwh", None)
            if hw_energy is not None and hw_energy > 0:
                return round(hw_energy, 2)
        return 0.0


class TomonsSessionDurationSensor(TomonsBaseSensor):
    """Session Charging Duration sensor (Minutes)."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_translation_key = "session_duration"

    def __init__(self, coordinator: TomonsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "session_duration")
        self._attr_name = "Ladedauer"

    @property
    def native_value(self) -> Optional[int]:
        if self.coordinator.data:
            duration_s = getattr(self.coordinator.data, "session_duration_s", None)
            if duration_s is not None and duration_s > 0:
                return round(duration_s / 60)
        return 0


class TomonsTemperatureSensor(TomonsBaseSensor):
    """Temperature sensor (°C)."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_translation_key = "temperature"

    def __init__(self, coordinator: TomonsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "temperature")
        self._attr_name = "Gehäusetemperatur"

    @property
    def native_value(self) -> Optional[int]:
        if self.coordinator.data:
            val = getattr(self.coordinator.data, "temperature_c", None)
            if val is None:
                val = getattr(self.coordinator.data, "temp_c", None)
            if val is not None:
                return int(val)
        return 16


class TomonsCurrentLimitSensor(TomonsBaseSensor):
    """Current Limit sensor (A)."""

    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_icon = "mdi:current-ac"
    _attr_translation_key = "current_limit"

    def __init__(self, coordinator: TomonsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "current_limit")
        self._attr_name = "Eingestellte Stromgrenze"

    @property
    def native_value(self) -> Optional[int]:
        if getattr(self.coordinator, "current_limit_a", None) is not None:
            return int(self.coordinator.current_limit_a)
        if self.coordinator.data and self.coordinator.data.max_current_a is not None:
            return int(self.coordinator.data.max_current_a)
        return 16


class TomonsCableStatusSensor(TomonsBaseSensor):
    """Cable and connection status."""

    _attr_icon = "mdi:ev-plug-type2"
    _attr_translation_key = "cable_status"

    def __init__(self, coordinator: TomonsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "cable_status")
        self._attr_name = "Kabel- & Ladestatus"

    @property
    def native_value(self) -> str:
        if self.coordinator.data and self.coordinator.data.cable_status and self.coordinator.data.cable_status != "Unbekannt":
            return self.coordinator.data.cable_status
        return "Nicht verbunden (Standby)"


class TomonsModeSensor(TomonsBaseSensor):
    """Charging mode sensor (Immediate, Scheduled, PV surplus)."""

    _attr_icon = "mdi:ev-station"
    _attr_translation_key = "mode"

    def __init__(self, coordinator: TomonsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "mode")
        self._attr_name = "Lademodus"

    @property
    def native_value(self) -> str:
        if self.coordinator.data:
            val = getattr(self.coordinator.data, "charge_mode", None) or getattr(self.coordinator.data, "mode", None)
            if val and val != "Unbekannt":
                return val
        return "Sofortladen"


class TomonsMacAddressSensor(TomonsBaseSensor):
    """MAC Address diagnostic sensor."""

    _attr_icon = "mdi:network"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "mac_address"

    def __init__(self, coordinator: TomonsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "mac_address")
        self._attr_name = "MAC-Adresse"

    @property
    def native_value(self) -> Optional[str]:
        return getattr(self.coordinator, "mac", "AC:A7:04:88:43:02")

