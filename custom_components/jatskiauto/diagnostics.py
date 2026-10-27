"""Diagnostics: tracking state without the data key or the followed stop."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from .const import CONF_ROUTE_FILE, CONF_ROUTE_ID, CONF_ROUTE_NAME, CONF_STOP_NAME
from .coordinator import JatskiautoConfigEntry

TO_REDACT = {CONF_API_KEY, CONF_ROUTE_FILE, CONF_ROUTE_ID, CONF_ROUTE_NAME, CONF_STOP_NAME, "latitude", "longitude"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: JatskiautoConfigEntry) -> dict[str, Any]:
    data = entry.runtime_data
    route = data.route.data
    live = data.live.data
    return {
        "entry": async_redact_data({"data": dict(entry.data), "options": dict(entry.options)}, TO_REDACT),
        "route": {
            "stop_count": len(route.stops) if route else None,
            "target_found": bool(route and route.target),
            "target_schedule_count": len(route.target.schedules) if route and route.target else None,
            "road_geometry_legs": sum(1 for leg in (data.live.legs or []) if leg),
        },
        "live": async_redact_data(asdict(live), TO_REDACT) if live else None,
        "live_last_update_success": data.live.last_update_success,
    }
