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
