"""Data coordinators for Jätskiauto."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import CannotConnect, InvalidAuth, PaikannuspalveluClient, Stop
from .const import CONF_ROUTE_FILE, CONF_ROUTE_ID, CONF_STOP_INDEX, CONF_STOP_NAME, DOMAIN, SCHEDULE_TZ
from .stops import resolve_stop_index

if TYPE_CHECKING:
    from .live import LiveCoordinator

_LOGGER = logging.getLogger(__name__)

# Schedules are published for the season and change rarely.
ROUTE_REFRESH = timedelta(hours=12)

type JatskiautoConfigEntry = ConfigEntry[JatskiautoRuntimeData]


@dataclass
class JatskiautoRuntimeData:
    """Objects shared by the platforms of one config entry."""

    client: PaikannuspalveluClient
    route: RouteDataCoordinator
    live: LiveCoordinator


@dataclass
class RouteData:
    """Stops of the followed route; the target stop is stops[stop_index]."""

    stops: list[Stop]
    stop_index: int

    @property
    def target(self) -> Stop | None:
        return self.stops[self.stop_index] if 0 <= self.stop_index < len(self.stops) else None


class RouteDataCoordinator(DataUpdateCoordinator[RouteData]):
    """Keeps the stops and schedules of the followed route."""

    config_entry: JatskiautoConfigEntry

    def __init__(self, hass: HomeAssistant, entry: JatskiautoConfigEntry, client: PaikannuspalveluClient) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} route", update_interval=ROUTE_REFRESH)
        self.client = client

    async def _async_update_data(self) -> RouteData:
        entry = self.config_entry
        today = dt_util.now(SCHEDULE_TZ).date()
        try:
            stops = await self.client.stops(entry.data[CONF_ROUTE_FILE], today)
            if not stops:
                # The route may have been republished under a new file id.
                route = next((r for r in await self.client.routes() if r.route_id == entry.data[CONF_ROUTE_ID]), None)
                if route and route.file != entry.data[CONF_ROUTE_FILE]:
                    self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_ROUTE_FILE: route.file})
                    stops = await self.client.stops(route.file, today)
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed from err
        except CannotConnect as err:
            raise UpdateFailed(str(err)) from err

        index = resolve_stop_index(stops, entry.data[CONF_STOP_INDEX], entry.data[CONF_STOP_NAME])
        issue_id = f"stop_missing_{entry.entry_id}"
        if index is None:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="stop_missing",
                translation_placeholders={"stop": entry.data[CONF_STOP_NAME], "route": entry.data[CONF_ROUTE_ID]},
            )
            return RouteData(stops, -1)
        ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        if index != entry.data[CONF_STOP_INDEX]:
            # The stop order changed; remember where the stop is now.
            self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_STOP_INDEX: index})
        return RouteData(stops, index)


def create_client(hass: HomeAssistant, entry: ConfigEntry) -> PaikannuspalveluClient:
    return PaikannuspalveluClient(async_get_clientsession(hass), entry.data[CONF_API_KEY])
