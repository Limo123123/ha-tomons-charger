"""Button entities for Tomons / Inateck Wallbox."""

from homeassistant.components.button import ButtonEntity
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
    """Set up Tomons Wallbox button entities from config entry."""
    coordinator: TomonsDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([TomonsRefreshStatusButton(coordinator)])


class TomonsRefreshStatusButton(CoordinatorEntity[TomonsDataUpdateCoordinator], ButtonEntity):
    """Button to manually request live telemetry stream from the Wallbox."""

    _attr_icon = "mdi:refresh"
    _attr_translation_key = "refresh_status"
    _attr_has_entity_name = True

    def __init__(self, coordinator: TomonsDataUpdateCoordinator) -> None:
        """Initialize button entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_refresh_status"
        self._attr_name = "Telemetrie aktualisieren"

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

    async def async_press(self) -> None:
        """Handle button press: send wake-up / status request packet."""
        await self.coordinator.async_request_status()
