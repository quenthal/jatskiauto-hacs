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
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import CannotConnect, InvalidAuth, PaikannuspalveluClient, Stop
from .const import CONF_ROUTE_FILE, CONF_STOP_INDEX, DOMAIN, SCHEDULE_TZ

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
        try:
            stops = await self.client.stops(self.config_entry.data[CONF_ROUTE_FILE], dt_util.now(SCHEDULE_TZ).date())
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed from err
        except CannotConnect as err:
            raise UpdateFailed(str(err)) from err
        return RouteData(stops, self.config_entry.data[CONF_STOP_INDEX])


def create_client(hass: HomeAssistant, entry: ConfigEntry) -> PaikannuspalveluClient:
    return PaikannuspalveluClient(async_get_clientsession(hass), entry.data[CONF_API_KEY])
