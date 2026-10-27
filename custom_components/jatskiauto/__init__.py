"""The Jätskiauto integration: follow an ice-cream truck to your stop."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback

from .const import CONF_ROUTE_FILE
from .coordinator import JatskiautoConfigEntry, JatskiautoRuntimeData, RouteDataCoordinator, create_client
from .geometry import async_fetch_geometry, async_load_geometry, stops_signature
from .live import LiveCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.DEVICE_TRACKER, Platform.EVENT, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: JatskiautoConfigEntry) -> bool:
    """Set up one followed stop."""
    client = create_client(hass, entry)
    route = RouteDataCoordinator(hass, entry, client)
    await route.async_config_entry_first_refresh()
    live = LiveCoordinator(hass, entry, client, route)
    entry.runtime_data = JatskiautoRuntimeData(client=client, route=route, live=live)

    route_file = entry.data[CONF_ROUTE_FILE]
    fetched_for: set[str] = set()

    async def _ensure_geometry() -> None:
        stops = route.data.stops
        signature = stops_signature(stops)
        if signature in fetched_for or len(stops) < 2:
            return
        fetched_for.add(signature)
        legs = await async_load_geometry(hass, route_file, stops)
        if legs is None:
            legs = await async_fetch_geometry(hass, route_file, stops)
            if not any(legs):
                fetched_for.discard(signature)  # OSRM unreachable: retry on the next route refresh
        live.legs = legs

    @callback
    def _route_updated() -> None:
        entry.async_create_background_task(hass, _ensure_geometry(), f"{entry.entry_id} road geometry")
        hass.async_create_task(live.async_request_refresh())

    entry.async_on_unload(route.async_add_listener(_route_updated))
    entry.async_on_unload(live.async_shutdown_timers)
    entry.async_create_background_task(hass, _ensure_geometry(), f"{entry.entry_id} road geometry")
    # Entities first, so that the event entity hears the first events of a visit.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await live.async_refresh()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: JatskiautoConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
