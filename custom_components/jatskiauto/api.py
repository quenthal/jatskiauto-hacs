"""Client for the public Paikannuspalvelu API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import json
import re
from typing import Any

import aiohttp

from .const import DEFAULT_API_BASE, DEFAULT_AUTHOR, USER_AGENT

WEEKDAY_FI = {
    "ma": "monday", "maanantai": "monday",
    "ti": "tuesday", "tiistai": "tuesday",
    "ke": "wednesday", "keskiviikko": "wednesday",
    "to": "thursday", "torstai": "thursday",
    "pe": "friday", "perjantai": "friday",
    "la": "saturday", "lauantai": "saturday",
    "su": "sunday", "sunnuntai": "sunday",
}
ROUTE_NAME_RE = re.compile(r"REITTI\s+([\w_]+)\s+(\w+)", re.IGNORECASE)
TODAY_RE = re.compile(r"^tänään\s+klo:\s*(\d{1,2}:\d{2})$", re.IGNORECASE)
DATED_RE = re.compile(r"^\w+\s+(\d{1,2})\.(\d{1,2})\.(\d{4})\s+klo:\s*(\d{1,2}:\d{2})$")
TIMEOUT = aiohttp.ClientTimeout(total=30)
REJECTED_PREFIX = "Incorrect request"


class PaikannuspalveluError(Exception):
    """Base error for the API."""


class CannotConnect(PaikannuspalveluError):
    """The API could not be reached or returned an unexpected response."""


class InvalidAuth(PaikannuspalveluError):
    """The API rejected the data key."""


@dataclass(frozen=True)
class Route:
    """A route from the route list."""

    file: str
    route_id: str
    name: str
    weekday: str | None
    zips: tuple[str, ...]


@dataclass
class Stop:
    """A stop on a route, in driving order."""

    index: int
    name: str
    lat: float
    lon: float
    zip: str
    schedules: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Stop in the shape the tracker uses."""
        return {"name": self.name, "lat": self.lat, "lon": self.lon, "zip": self.zip, "schedules": self.schedules}


@dataclass(frozen=True)
class Device:
    """A live vehicle position."""

    id: str
    name: str | None
    lat: float
    lon: float
    heading: Any
    speed: Any
    timestamp: Any


def parse_route_name(name: str) -> tuple[str | None, str | None]:
    """Return (route_id, weekday) from a name like 'REITTI 11_101_1 pe ...'."""
    m = ROUTE_NAME_RE.match(name or "")
    if not m:
        return None, None
    return m.group(1), WEEKDAY_FI.get(m.group(2).lower())


def parse_schedule(raw: str, today: date) -> dict[str, str] | None:
    """Parse 'pe 17.7.2026 klo: 17:20' or 'tänään klo: 17:20' to date and HH:MM."""
    s = (raw or "").strip()
    if m := TODAY_RE.match(s):
        return {"date": today.isoformat(), "time": _hhmm(m.group(1))}
    if m := DATED_RE.match(s):
        day, month, year, tm = m.groups()
        return {"date": f"{year}-{int(month):02d}-{int(day):02d}", "time": _hhmm(tm)}
    return None


def _hhmm(value: str) -> str:
    h, m = value.split(":")
    return f"{int(h):02d}:{m}"


def parse_routes(items: list[dict[str, Any]]) -> list[Route]:
    """Routes with a recognisable route id."""
    routes = []
    for item in items:
        route_id, weekday = parse_route_name(item.get("name", ""))
        if not route_id or item.get("file") is None:
            continue
        zips = tuple(str(z) for z in item.get("zips", []) if str(z) not in ("", "00000"))
        routes.append(Route(str(item["file"]), route_id, item.get("name", ""), weekday, zips))
    return routes


def parse_stops(geojson: dict[str, Any], today: date) -> list[Stop]:
    """Stops of one route in driving order. Contact details are dropped."""
    feats = sorted(geojson.get("features", []), key=lambda f: f.get("properties", {}).get("sequence_on_route", 0))
    stops = []
    for feat in feats:
        coords = feat.get("geometry", {}).get("coordinates", [])
        if len(coords) < 2:
            continue
        props = feat.get("properties", {})
        schedules = [s for s in (parse_schedule(raw, today) for raw in props.get("schedules", [])) if s]
        stops.append(Stop(len(stops), props.get("name_on_route") or "", float(coords[1]), float(coords[0]), str(props.get("zip", "")), schedules))
    return stops


def parse_devices(geojson: dict[str, Any]) -> list[Device]:
    """Live vehicles from the device list."""
    out = []
    for feat in geojson.get("features", []):
        coords = feat.get("geometry", {}).get("coordinates", [])
        props = feat.get("properties", {})
        if len(coords) < 2 or props.get("id") is None:
            continue
        out.append(Device(str(props["id"]), props.get("name"), float(coords[1]), float(coords[0]), props.get("heading"), props.get("speed"), props.get("timestamp")))
    return out


class PaikannuspalveluClient:
    """Minimal async client. Never logs the data key."""

    def __init__(self, session: aiohttp.ClientSession, data_key: str, api_base: str = DEFAULT_API_BASE) -> None:
        self._session = session
        self._params = {"data_key": data_key, "author": DEFAULT_AUTHOR, "format": "geojson"}
        self._base = api_base.rstrip("/")

    async def _get(self, path: str, extra: dict[str, str] | None = None) -> Any:
        try:
            async with self._session.get(
                f"{self._base}{path}", params={**self._params, **(extra or {})}, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
            ) as resp:
                if resp.status == 401:
                    raise InvalidAuth
                if resp.status != 200:
                    # 403 is the firewall (e.g. a blocked User-Agent), not a rejected key.
                    raise CannotConnect(f"HTTP {resp.status} from {path}")
                text = await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(f"{type(err).__name__} from {path}") from err
        # A missing or wrong data key is answered with HTTP 200 and this plain text.
        if text.lstrip().startswith(REJECTED_PREFIX):
            raise InvalidAuth
        try:
            return json.loads(text)
        except ValueError as err:
            raise CannotConnect(f"{type(err).__name__} from {path}") from err

    async def routes(self) -> list[Route]:
        data = await self._get("/v1/public/route/")
        if not isinstance(data, list):
            raise CannotConnect("Unexpected route list")
        return parse_routes(data)

    async def stops(self, route_file: str, today: date) -> list[Stop]:
        return parse_stops(await self._get("/v1/public/route/search/", {"routes": route_file}), today)

    async def devices(self) -> list[Device]:
        return parse_devices(await self._get("/v1/public/device/"))
