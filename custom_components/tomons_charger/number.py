"""Number entity for setting charging current limit on Tomons / Inateck Wallbox."""

from homeassistant.components.number import (
    NumberEntity,
    NumberMode,
)
from homeassistant.const import UnitOfElectricCurrent
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
    """Set up Tomons Wallbox number entities from config entry."""
    coordinator: TomonsDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([TomonsChargingCurrentNumber(coordinator)])


class TomonsChargingCurrentNumber(CoordinatorEntity[TomonsDataUpdateCoordinator], NumberEntity):
    """Number entity allowing slider adjustment of the maximum charging current limit."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 6.0
    _attr_native_max_value = 32.0
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_icon = "mdi:current-ac"
    _attr_translation_key = "charging_current"
    _attr_has_entity_name = True

    def __init__(self, coordinator: TomonsDataUpdateCoordinator) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_charging_current_limit"
        self._attr_name = "Ladestromgrenze"

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

    @property
    def native_value(self) -> float:
        """Return currently set charging current limit in Amperes."""
        if self.coordinator.data and self.coordinator.data.max_current_a is not None:
            return float(self.coordinator.data.max_current_a)
        return 6.0

    async def async_set_native_value(self, value: float) -> None:
        """Set a new charging current limit."""
        amps = int(round(value))
        await self.coordinator.async_set_current_limit(amps)
