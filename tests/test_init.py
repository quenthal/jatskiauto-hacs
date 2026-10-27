"""Setting up an entry and its sensor."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jatskiauto.const import CONF_ROUTE_FILE, CONF_ROUTE_ID, CONF_STOP_INDEX, DOMAIN

from .fake_api import API, DATA_KEY, mock_api

DATA = {CONF_API_KEY: DATA_KEY, CONF_ROUTE_FILE: "9001", CONF_ROUTE_ID: "99_100_1", "route_name": "REITTI 99_100_1 pe", CONF_STOP_INDEX: 1, "stop_name": "Keksitie 1"}


async def setup(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="99_100_1_1", data=DATA, title="Keksitie 1 (99_100_1)")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_and_next_visit_sensor(hass, aioclient_mock, freezer):
    freezer.move_to("2027-06-04 17:30:00+03:00")
    mock_api(aioclient_mock)
    entry = await setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_next_visit")
    state = hass.states.get(entity_id)
    assert state.state == "2027-06-18T14:05:00+00:00"
    assert state.attributes["stop_name"] == "Keksitie 1"
    assert state.attributes["upcoming_visits"] == ["2027-06-18 17:05"]
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_retries_when_api_down(hass, aioclient_mock):
    aioclient_mock.get(f"{API}/v1/public/route/search/", status=503)
    entry = await setup(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_rejected_key_starts_reauth(hass, aioclient_mock):
    aioclient_mock.get(f"{API}/v1/public/route/search/", status=401)
    entry = await setup(hass)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_stop_order_change_is_followed(hass, aioclient_mock, freezer):
    """The season's stop list is reordered: the entry follows the stop by name."""
    freezer.move_to("2027-06-01 12:00:00+03:00")
    from .fake_api import STOPS_9001, mock_osrm

    reordered = {"features": [dict(f, properties={**f["properties"], "sequence_on_route": 3 - f["properties"]["sequence_on_route"]}) for f in STOPS_9001["features"]]}
    aioclient_mock.get(f"{API}/v1/public/route/search/", params={"routes": "9001"}, json=reordered)
    aioclient_mock.get(f"{API}/v1/public/device/", json={"features": []})
    mock_osrm(aioclient_mock)
    entry = await setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data[CONF_STOP_INDEX] == 2  # "Keksitie 1" was index 1, is now index 2
    await hass.config_entries.async_unload(entry.entry_id)


async def test_missing_stop_raises_repair_issue(hass, aioclient_mock):
    from homeassistant.helpers import issue_registry as ir

    from .fake_api import mock_osrm

    aioclient_mock.get(f"{API}/v1/public/route/search/", params={"routes": "9001"}, json={"features": [
        {"geometry": {"coordinates": [25.0, 60.0]}, "properties": {"sequence_on_route": 0, "name_on_route": "Muu", "schedules": []}},
        {"geometry": {"coordinates": [25.01, 60.0]}, "properties": {"sequence_on_route": 1, "name_on_route": "Toinen", "schedules": []}},
    ]})
    aioclient_mock.get(f"{API}/v1/public/device/", json={"features": []})
    mock_osrm(aioclient_mock)
    entry = await setup(hass)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"stop_missing_{entry.entry_id}") is not None
    status = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_status")
    assert hass.states.get(status).state == "no_visits"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_republished_route_file_is_found_by_route_id(hass, aioclient_mock):
    from .fake_api import ROUTES, STOPS_9001, mock_osrm

    aioclient_mock.get(f"{API}/v1/public/route/search/", params={"routes": "9001"}, json={"features": []})
    aioclient_mock.get(f"{API}/v1/public/route/search/", params={"routes": "9101"}, json=STOPS_9001)
    aioclient_mock.get(f"{API}/v1/public/route/", json=[{**ROUTES[0], "file": 9101}, *ROUTES[1:]])
    aioclient_mock.get(f"{API}/v1/public/device/", json={"features": []})
    mock_osrm(aioclient_mock)
    entry = await setup(hass)
    assert entry.data[CONF_ROUTE_FILE] == "9101"
    assert len(entry.runtime_data.route.data.stops) == 4
    await hass.config_entries.async_unload(entry.entry_id)
