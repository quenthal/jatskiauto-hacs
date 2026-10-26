"""The Jätskiauto integration: follow an ice-cream truck to your stop."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import JatskiautoConfigEntry, JatskiautoRuntimeData, RouteDataCoordinator, create_client

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: JatskiautoConfigEntry) -> bool:
    """Set up one followed stop."""
    client = create_client(hass, entry)
    route = RouteDataCoordinator(hass, entry, client)
    await route.async_config_entry_first_refresh()
    entry.runtime_data = JatskiautoRuntimeData(client=client, route=route)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: JatskiautoConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
