"""Switch entity to start and stop charging on Tomons / Inateck Wallbox."""

from typing import Any
from homeassistant.components.switch import SwitchEntity
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
    """Set up Tomons Wallbox switch entities from config entry."""
    coordinator: TomonsDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([TomonsChargingSwitch(coordinator)])


class TomonsChargingSwitch(CoordinatorEntity[TomonsDataUpdateCoordinator], SwitchEntity):
    """Switch entity to trigger start / pause of the EV charging process."""

    _attr_icon = "mdi:ev-station"
    _attr_translation_key = "charging_control"
    _attr_has_entity_name = True

    def __init__(self, coordinator: TomonsDataUpdateCoordinator) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_charging_switch"
        self._attr_name = "Laden"

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
    def is_on(self) -> bool:
        """Return True if Wallbox is actively delivering charging power."""
        if not self.coordinator.data:
            return False
        status = self.coordinator.data.cable_status or ""
        power = self.coordinator.data.power_w or 0
        return "Lädt aktiv" in status or power > 50

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start charging."""
        await self.coordinator.async_start_charging()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop charging."""
        await self.coordinator.async_stop_charging()
