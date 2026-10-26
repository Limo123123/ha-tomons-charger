"""Binary sensors for Tomons / Inateck Wallbox."""

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, MANUFACTURER, CONF_DEVICE_NAME, CONF_PRODUCT_MODEL
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
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device_id)},
            name=self.coordinator.entry.data.get(CONF_DEVICE_NAME, "Tomons Wallbox"),
            manufacturer=MANUFACTURER,
            model=self.coordinator.entry.data.get(CONF_PRODUCT_MODEL, "EV Wallbox"),
            sw_version="1.1.5",
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
        if not self.coordinator.data or not self.coordinator.data.cable_status:
            return False
        status = self.coordinator.data.cable_status
        return "Verbunden" in status or "Lädt" in status


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
        power = self.coordinator.data.power_w or 0
        status = self.coordinator.data.cable_status or ""
        return "Lädt aktiv" in status or power > 50


class TomonsOnlineBinarySensor(TomonsBaseBinarySensor):
    """Indicates cloud / broker connectivity."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "connectivity"

    def __init__(self, coordinator: TomonsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "connectivity")
        self._attr_name = "Verbindung"

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.is_online if self.coordinator.data else False
