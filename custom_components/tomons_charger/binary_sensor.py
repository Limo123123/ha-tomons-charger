"""Binary sensors for Tomons / Inateck Wallbox."""

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, MANUFACTURER, CONF_DEVICE_NAME, CONF_PRODUCT_MODEL, CONF_MAC
from .coordinator import TomonsDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tomons Wallbox binary sensors from config entry."""
    coordinator: TomonsDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        TomonsPluggedInBinarySensor(coordinator),
        TomonsChargingBinarySensor(coordinator),
        TomonsOnlineBinarySensor(coordinator),
    ]

    async_add_entities(entities)


class TomonsBaseBinarySensor(CoordinatorEntity[TomonsDataUpdateCoordinator], BinarySensorEntity):
    """Base binary sensor for Tomons Wallbox."""

    def __init__(self, coordinator: TomonsDataUpdateCoordinator, sensor_type: str) -> None:
        """Initialize binary sensor."""
        super().__init__(coordinator)
        self._sensor_type = sensor_type
        self._attr_unique_id = f"{coordinator.device_id}_{sensor_type}"
        self._attr_has_entity_name = True

    @property
    def available(self) -> bool:
        """Return True if binary sensor is available."""
        if not super().available:
            return False
        if self._sensor_type == "connectivity":
            return True
        return self.coordinator.is_online

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


class TomonsPluggedInBinarySensor(TomonsBaseBinarySensor):
    """Indicates whether an EV charging cable is plugged into the car."""

    _attr_device_class = BinarySensorDeviceClass.PLUG
    _attr_translation_key = "plugged_in"

    def __init__(self, coordinator: TomonsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "plugged_in")
        self._attr_name = "Fahrzeug angesteckt"

    @property
    def is_on(self) -> bool:
        if not self.coordinator.data:
            return False
        status = self.coordinator.data.cable_status or ""
        power = self.coordinator.data.power_w or 0
        current = self.coordinator.data.current_a or 0.0
        if status == "Nicht verbunden (Standby)":
            return False
        return "Verbunden" in status or "Lädt" in status or (power > 200 and current > 1.0)


class TomonsChargingBinarySensor(TomonsBaseBinarySensor):
    """Indicates whether active EV charging is in progress."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING
    _attr_translation_key = "charging"

    def __init__(self, coordinator: TomonsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "charging")
        self._attr_name = "Ladevorgang aktiv"

    @property
    def is_on(self) -> bool:
        if not self.coordinator.data:
            return False
        status = self.coordinator.data.cable_status or ""
        power = self.coordinator.data.power_w or 0
        current = self.coordinator.data.current_a or 0.0
        if status == "Nicht verbunden (Standby)":
            return False
        return "Lädt aktiv" in status or (power > 200 and current > 1.0)


class TomonsOnlineBinarySensor(TomonsBaseBinarySensor):
    """Indicates cloud / broker connectivity."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "connectivity"

    def __init__(self, coordinator: TomonsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "connectivity")
        self._attr_name = "Verbindung"

    @property
    def is_on(self) -> bool:
        return self.coordinator.is_online
