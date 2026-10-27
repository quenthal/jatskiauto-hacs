"""Finding the stops near a location."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .api import Route, Stop
from .const import SCHEDULE_TZ
from .tracker import haversine_m, parse_hhmm


@dataclass(frozen=True)
class StopChoice:
    """A stop the user can pick, with what is needed to describe it."""

    route: Route
    stop: Stop
    distance_m: float
    next_visit: datetime | None

    @property
    def key(self) -> str:
        return f"{self.route.file}|{self.stop.index}"


def routes_for_postal_code(routes: list[Route], postal_code: str) -> list[Route]:
    """Routes serving the postal code; the 3-digit area if none serve it exactly."""
    code = postal_code.strip()
    exact = [r for r in routes if code in r.zips]
    if exact:
        return exact
    return [r for r in routes if any(z[:3] == code[:3] for z in r.zips)]


def next_visit(stop: Stop, now: datetime) -> datetime | None:
    """Next scheduled visit at or after now, as an aware datetime in Finnish time."""
    times = []
    for s in stop.schedules:
        dt = datetime.combine(datetime.fromisoformat(s["date"]).date(), parse_hhmm(s["time"]), tzinfo=SCHEDULE_TZ)
        if dt >= now:
            times.append(dt)
    return min(times, default=None)


def nearest_stops(
    stops_by_route: list[tuple[Route, list[Stop]]], lat: float, lon: float, now: datetime, count: int
) -> list[StopChoice]:
    """The nearest stops across routes, one entry per route and stop."""
    choices = [
        StopChoice(route, stop, haversine_m(lat, lon, stop.lat, stop.lon), next_visit(stop, now))
        for route, stops in stops_by_route
        for stop in stops
    ]
    choices.sort(key=lambda c: c.distance_m)
    return choices[:count]


def resolve_stop_index(stops: list[Stop], index: int, name: str) -> int | None:
    """Find the followed stop again after the route's stop list has changed.

    The stop order can change between seasons, so the stored index alone is not
    enough: keep it while the name still matches, otherwise take the stop with
    the same name nearest to the old position, or None if it is gone.
    """
    if 0 <= index < len(stops) and stops[index].name == name:
        return index
    same = [s.index for s in stops if s.name == name]
    return min(same, key=lambda i: abs(i - index)) if same else None
