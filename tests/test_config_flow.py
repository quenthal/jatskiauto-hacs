"""Config, options and reauth flows."""

from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jatskiauto.const import (
    CONF_NOTIFY_MINUTES,
    CONF_RECORD_TRACES,
    CONF_ROUTE_FILE,
    CONF_ROUTE_ID,
    CONF_STOP_INDEX,
    CONF_WINDOW_AFTER,
    CONF_WINDOW_BEFORE,
    DOMAIN,
)

from .fake_api import API, DATA_KEY, mock_api

LOCATION = {"latitude": 60.0003, "longitude": 25.0030}


async def start(hass, aioclient_mock):
    mock_api(aioclient_mock)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: f" {DATA_KEY} "})
    assert result["type"] is FlowResultType.MENU
    return result


async def test_location_flow_creates_entry(hass, aioclient_mock, freezer):
    freezer.move_to("2027-06-01 12:00:00+03:00")
    result = await start(hass, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "location"})
    assert result["step_id"] == "location"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"location": LOCATION, "postal_code": "00990"})
    assert result["step_id"] == "stop"
    options = result["data_schema"].schema["stop"].config["options"]
    assert options[0]["value"] == "9001|1"
    assert options[0]["label"] == "Keksitie 1 – 47 m – reitti 99_100_1 (pe) – seuraava 4.6. 17:05"
    assert len(options) == 5
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"stop": "9001|1"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Keksitie 1 (99_100_1)"
    assert result["data"] == {
        CONF_API_KEY: DATA_KEY,
        CONF_ROUTE_FILE: "9001",
        CONF_ROUTE_ID: "99_100_1",
        "route_name": "REITTI 99_100_1 pe kevät",
        CONF_STOP_INDEX: 1,
        "stop_name": "Keksitie 1",
    }
    # The home location is not stored.
    assert "latitude" not in str(result["data"])


async def test_location_flow_unknown_postal_code(hass, aioclient_mock):
    result = await start(hass, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "location"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"location": LOCATION, "postal_code": "33100"})
    assert result["errors"] == {"postal_code": "no_routes_for_postal_code"}


async def test_route_flow_lists_all_stops(hass, aioclient_mock):
    result = await start(hass, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "route"})
    assert result["step_id"] == "route"
    routes = [o["value"] for o in result["data_schema"].schema[CONF_ROUTE_FILE].config["options"]]
    assert routes == ["9003", "9001", "9002"]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ROUTE_FILE: "9001"})
    options = result["data_schema"].schema["stop"].config["options"]
    assert [o["value"] for o in options] == ["9001|0", "9001|1", "9001|2", "9001|3"]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"stop": "9001|3"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_STOP_INDEX] == 3


async def test_invalid_key_and_connection_error(hass, aioclient_mock):
    aioclient_mock.get(f"{API}/v1/public/route/", text="Incorrect request, required data missing!")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "bad"})
    assert result["errors"] == {"base": "invalid_auth"}
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/v1/public/route/", status=403)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "bad"})
    assert result["errors"] == {"base": "cannot_connect"}


async def test_same_stop_twice_aborts(hass, aioclient_mock):
    MockConfigEntry(domain=DOMAIN, unique_id="99_100_1_1", data={CONF_API_KEY: DATA_KEY}).add_to_hass(hass)
    result = await start(hass, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "route"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ROUTE_FILE: "9001"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"stop": "9001|1"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_options_flow(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: DATA_KEY})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    new = {CONF_NOTIFY_MINUTES: 8, CONF_WINDOW_BEFORE: 90, CONF_WINDOW_AFTER: 120, CONF_RECORD_TRACES: True}
    result = await hass.config_entries.options.async_configure(result["flow_id"], new)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == new


async def test_reauth_updates_key(hass, aioclient_mock):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="99_100_1_1", data={CONF_API_KEY: "old", CONF_ROUTE_FILE: "9001", CONF_STOP_INDEX: 1, "stop_name": "Keksitie 1", CONF_ROUTE_ID: "99_100_1"})
    entry.add_to_hass(hass)
    mock_api(aioclient_mock)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "new"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "new"
