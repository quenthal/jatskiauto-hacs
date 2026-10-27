"""Road geometry between consecutive stops.

The API only gives stop coordinates. The driving line between stops is
requested once per route from the public OSRM demo server (at most one request
per second, as its usage policy asks) and cached in Home Assistant's storage.
Missing legs fall back to a straight line, which the tracker can still use.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import Stop
from .const import DOMAIN, USER_AGENT

_LOGGER = logging.getLogger(__name__)

OSRM_BASE = "https://router.project-osrm.org/route/v1/driving"
OSRM_DELAY_S = 1.1
STORAGE_VERSION = 1
TIMEOUT = aiohttp.ClientTimeout(total=20)

type Legs = list[list[list[float]] | None]


def stops_signature(stops: list[Stop]) -> str:
    """Changes when the stops or their order change."""
    raw = json.dumps([[round(s.lat, 6), round(s.lon, 6)] for s in stops])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def straight_legs(stops: list[Stop]) -> list[list[list[float]]]:
    return [[[a.lat, a.lon], [b.lat, b.lon]] for a, b in zip(stops, stops[1:], strict=False)]


def fill_legs(stops: list[Stop], legs: Legs) -> list[list[list[float]]]:
    """Use straight lines for legs that have no road geometry."""
    straight = straight_legs(stops)
    return [leg if leg and len(leg) >= 2 else straight[i] for i, leg in enumerate((legs + [None] * len(straight))[: len(straight)])]


async def _osrm_leg(session: aiohttp.ClientSession, a: Stop, b: Stop) -> list[list[float]] | None:
    url = f"{OSRM_BASE}/{a.lon},{a.lat};{b.lon},{b.lat}"
    try:
        async with session.get(
            url, params={"overview": "full", "geometries": "geojson", "steps": "false"}, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return None
    if data.get("code") != "Ok" or not data.get("routes"):
        return None
    return [[round(c[1], 6), round(c[0], 6)] for c in data["routes"][0]["geometry"]["coordinates"]]


async def async_load_geometry(hass: HomeAssistant, route_file: str, stops: list[Stop]) -> Legs | None:
    """Cached legs for these stops, or None if not fetched yet."""
    store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.geometry.{route_file}")
    data = await store.async_load()
    if data and data.get("signature") == stops_signature(stops):
        return data["legs"]
    return None


async def async_fetch_geometry(hass: HomeAssistant, route_file: str, stops: list[Stop]) -> Legs:
    """Fetch every leg from OSRM and cache the result."""
    session = async_get_clientsession(hass)
    legs: Legs = []
    for i, (a, b) in enumerate(zip(stops, stops[1:], strict=False)):
        if i:
            await asyncio.sleep(OSRM_DELAY_S)
        legs.append(await _osrm_leg(session, a, b))
    missing = sum(1 for leg in legs if not leg)
    if missing:
        _LOGGER.debug("Road geometry missing for %s of %s legs; using straight lines for them", missing, len(legs))
    if legs and missing == len(legs):
        # OSRM unreachable: do not cache, try again on the next route refresh.
        return legs
    store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.geometry.{route_file}")
    await store.async_save({"signature": stops_signature(stops), "legs": legs})
    return legs
