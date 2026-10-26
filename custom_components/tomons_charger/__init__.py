"""Tomons / Inateck EV Wallbox Integration for Home Assistant."""

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN, PLATFORMS
from .coordinator import TomonsDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Tomons Wallbox from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    coordinator = TomonsDataUpdateCoordinator(hass, entry)
    try:
        await coordinator.async_setup()
    except Exception as err:
        _LOGGER.error("Failed to connect to Tomons Wallbox: %s", err)
        raise ConfigEntryNotReady(f"Could not connect to Wallbox: {err}") from err

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Tomons Wallbox config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: TomonsDataUpdateCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_unload()

    return unload_ok
