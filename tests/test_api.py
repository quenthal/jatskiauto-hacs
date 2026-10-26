"""Parsing and client error handling."""

from datetime import date

from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest

from custom_components.jatskiauto.api import (
    CannotConnect,
    InvalidAuth,
    PaikannuspalveluClient,
    parse_devices,
    parse_route_name,
    parse_routes,
    parse_schedule,
    parse_stops,
)

from .fake_api import API, DATA_KEY, DEVICES, ROUTES, STOPS_9001, mock_api


def test_parse_route_name():
    assert parse_route_name("REITTI 99_100_1 pe kevät") == ("99_100_1", "friday")
    assert parse_route_name("reitti 99_100_1 perjantai") == ("99_100_1", "friday")
    assert parse_route_name("Huoltoajo") == (None, None)


def test_parse_schedule():
    today = date(2027, 6, 4)
    assert parse_schedule("pe 18.6.2027 klo: 17:05", today) == {"date": "2027-06-18", "time": "17:05"}
    assert parse_schedule("pe 18.06.2027 klo:9:05", today) == {"date": "2027-06-18", "time": "09:05"}
    assert parse_schedule("tänään klo: 17:05", today) == {"date": "2027-06-04", "time": "17:05"}
    assert parse_schedule("bad", today) is None


def test_parse_routes_skips_unnamed():
    routes = parse_routes(ROUTES)
    assert [r.route_id for r in routes] == ["99_100_1", "99_200_2", "98_300_1"]
    assert routes[0].file == "9001"
    assert routes[0].zips == ("00990", "00991")


def test_parse_stops_orders_and_drops_contact():
    stops = parse_stops(STOPS_9001, date(2027, 6, 1))
    assert [s.name for s in stops] == ["Alkupiste", "Keksitie 1", "Keksitie 3", "Kuvitelmankuja"]
    assert [s.index for s in stops] == [0, 1, 2, 3]
    assert len(stops[3].schedules) == 2
    assert "contact" not in stops[0].as_dict() and "phone" not in stops[0].as_dict()


def test_parse_devices_skips_without_position():
    devices = parse_devices(DEVICES)
    assert [(d.id, d.lat, d.lon) for d in devices] == [("77", 60.0, 25.004)]


async def test_client_sends_key_and_parses(hass, aioclient_mock):
    mock_api(aioclient_mock)
    client = PaikannuspalveluClient(async_get_clientsession(hass), DATA_KEY)
    assert len(await client.routes()) == 3
    assert len(await client.stops("9001", date(2027, 6, 1))) == 4
    assert len(await client.devices()) == 1
    url = aioclient_mock.mock_calls[0][1]
    assert url.query["data_key"] == DATA_KEY


@pytest.mark.parametrize(("status", "error"), [(401, InvalidAuth), (403, InvalidAuth), (500, CannotConnect)])
async def test_client_errors(hass, aioclient_mock, status, error):
    aioclient_mock.get(f"{API}/v1/public/route/", status=status)
    client = PaikannuspalveluClient(async_get_clientsession(hass), DATA_KEY)
    with pytest.raises(error):
        await client.routes()


async def test_client_rejects_non_list_route_response(hass, aioclient_mock):
    aioclient_mock.get(f"{API}/v1/public/route/", json={"error": "x"})
    client = PaikannuspalveluClient(async_get_clientsession(hass), DATA_KEY)
    with pytest.raises(CannotConnect):
        await client.routes()
