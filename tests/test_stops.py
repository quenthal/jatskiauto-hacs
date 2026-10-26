"""Nearest-stop search."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from custom_components.jatskiauto.api import parse_routes, parse_stops
from custom_components.jatskiauto.stops import nearest_stops, next_visit, routes_for_postal_code

from .fake_api import ROUTES, STOPS_9001, STOPS_9002

TZ = ZoneInfo("Europe/Helsinki")


def test_routes_for_postal_code_exact_then_area():
    routes = parse_routes(ROUTES)
    assert [r.route_id for r in routes_for_postal_code(routes, "00991")] == ["99_100_1"]
    assert [r.route_id for r in routes_for_postal_code(routes, "00990")] == ["99_100_1", "99_200_2"]
    # No exact match: same 3-digit area.
    assert [r.route_id for r in routes_for_postal_code(routes, "01235")] == ["98_300_1"]
    assert routes_for_postal_code(routes, "33100") == []


def test_next_visit():
    stop = parse_stops(STOPS_9001, date(2027, 6, 1))[1]
    assert next_visit(stop, datetime(2027, 6, 1, 12, tzinfo=TZ)) == datetime(2027, 6, 4, 17, 5, tzinfo=TZ)
    assert next_visit(stop, datetime(2027, 6, 4, 17, 6, tzinfo=TZ)) == datetime(2027, 6, 18, 17, 5, tzinfo=TZ)
    assert next_visit(stop, datetime(2027, 7, 1, tzinfo=TZ)) is None


def test_nearest_stops_across_routes():
    routes = parse_routes(ROUTES)
    today = date(2027, 6, 1)
    by_route = [(routes[0], parse_stops(STOPS_9001, today)), (routes[1], parse_stops(STOPS_9002, today))]
    found = nearest_stops(by_route, 60.0003, 25.0030, datetime(2027, 6, 1, tzinfo=TZ), 3)
    assert [c.stop.name for c in found] == ["Keksitie 1", "Toinen reitti 5", "Alkupiste"]
    assert round(found[0].distance_m) == 47
    assert found[0].key == "9001|1"
