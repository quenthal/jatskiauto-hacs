"""Live tracking: sleeping outside the window, a visit from start to passed."""

from datetime import timedelta

from homeassistant.const import CONF_API_KEY
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from custom_components.jatskiauto.const import CONF_NOTIFY_MINUTES, CONF_ROUTE_FILE, CONF_ROUTE_ID, CONF_STOP_INDEX, DOMAIN

from .fake_api import API, DATA_KEY, STOPS_9001, mock_api, mock_osrm

# Target: third stop of route 9001 ("Keksitie 3", 4.6.2027 17:10, window 16:10-19:40).
DATA = {CONF_API_KEY: DATA_KEY, CONF_ROUTE_FILE: "9001", CONF_ROUTE_ID: "99_100_1", "route_name": "REITTI 99_100_1 pe", CONF_STOP_INDEX: 2, "stop_name": "Keksitie 3"}
STOP_LON = [25.0, 25.00359, 25.00718, 25.01077]


class Truck:
    """Fake device API: one truck whose position the test moves."""

    def __init__(self, aioclient_mock):
        self.lon = STOP_LON[0]
        self.calls = 0
        aioclient_mock.get(f"{API}/v1/public/device/", side_effect=self.respond)

    async def respond(self, method, url, data):
        self.calls += 1
        now = dt_util.now()
        feature = {"geometry": {"coordinates": [self.lon, 60.0]}, "properties": {"id": 5, "name": "Testiauto", "timestamp": now.isoformat()}}
        return AiohttpClientMockResponse(method, url, json={"features": [feature]})


def entity(hass, entry, platform, key):
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{key}")


async def setup(hass, aioclient_mock, options=None):
    aioclient_mock.get(f"{API}/v1/public/route/search/", params={"routes": "9001"}, json=STOPS_9001)
    mock_api(aioclient_mock)
    entry = MockConfigEntry(domain=DOMAIN, unique_id="99_100_1_2", data=DATA, options=options or {CONF_NOTIFY_MINUTES: 5})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def step(hass, freezer, seconds):
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_no_polling_outside_window_then_wakes(hass, aioclient_mock, freezer):
    freezer.move_to("2027-06-04 12:00:00+03:00")
    truck = Truck(aioclient_mock)
    entry = await setup(hass, aioclient_mock)
    status = hass.states.get(entity(hass, entry, "sensor", "status"))
    assert status.state == "scheduled"
    assert status.attributes["tracking_window_start"] == "2027-06-04T16:10:00+03:00"
    assert hass.states.get(entity(hass, entry, "sensor", "eta_likely")).state == "2027-06-04T14:10:00+00:00"
    for _ in range(4):
        await step(hass, freezer, 3600)
    assert truck.calls == 0  # 12:00 -> 16:00, before the window
    await step(hass, freezer, 11 * 60)  # 16:11, the window opened at 16:10
    assert hass.states.get(entity(hass, entry, "sensor", "status")).state != "scheduled"
    assert truck.calls >= 1
    await hass.config_entries.async_unload(entry.entry_id)


async def test_visit_from_start_to_passed(hass, aioclient_mock, freezer):
    freezer.move_to("2027-06-04 16:09:00+03:00")
    truck = Truck(aioclient_mock)
    events = []
    entry = await setup(hass, aioclient_mock)
    event_id = entity(hass, entry, "event", "events")
    tracker_id = entity(hass, entry, "device_tracker", "truck")
    tracker_writes = []
    def on_event(e):
        new = e.data["new_state"]
        if e.data["entity_id"] == event_id and new is not None and new.attributes.get("event_type"):
            events.append(new.attributes["event_type"])

    hass.bus.async_listen("state_changed", on_event)
    hass.bus.async_listen("state_changed", lambda e: tracker_writes.append(e) if e.data["entity_id"] == tracker_id else None)
    live = entry.runtime_data.live

    async def drive(lon, polls):
        truck.lon = lon
        for _ in range(polls):
            await step(hass, freezer, live.update_interval.total_seconds() if live.update_interval else 60)

    await step(hass, freezer, 61)  # window opens
    await drive(STOP_LON[0], 1)
    await drive(STOP_LON[1], 4)  # sells at stop 2
    state = hass.states.get(entity(hass, entry, "sensor", "status")).state
    assert state == "approaching_1_stop"
    assert hass.states.get(entity(hass, entry, "sensor", "stops_remaining")).state == "1"
    await drive(STOP_LON[2], 4)  # at the target
    assert hass.states.get(entity(hass, entry, "sensor", "status")).state == "arrived"
    assert hass.states.get(entity(hass, entry, "binary_sensor", "within_threshold")).state == "on"
    assert hass.states.get(entity(hass, entry, "sensor", "arrival")).state != "unknown"
    await drive(25.0095, 3)  # leaves towards stop 4
    assert hass.states.get(entity(hass, entry, "sensor", "status")).state == "passed"
    assert hass.states.get(entity(hass, entry, "binary_sensor", "within_threshold")).state == "off"

    assert events[0] == "tracking_started"
    assert events.count("within_threshold") == 1
    assert events.index("within_threshold") < events.index("arrived") < events.index("passed")
    assert "reached_stop" in events
    # Parked truck does not rewrite its position; each move of 200 m does.
    assert 3 <= len(tracker_writes) <= 6

    calls = truck.calls
    for _ in range(10):
        await step(hass, freezer, 60)
    assert truck.calls == calls  # no polling after passed
    await hass.config_entries.async_unload(entry.entry_id)


async def test_api_error_in_window_recovers(hass, aioclient_mock, freezer):
    freezer.move_to("2027-06-04 17:00:00+03:00")
    mock_api(aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/v1/public/route/search/", params={"routes": "9001"}, json=STOPS_9001)
    aioclient_mock.get(f"{API}/v1/public/device/", status=503)
    mock_osrm(aioclient_mock)
    entry = MockConfigEntry(domain=DOMAIN, unique_id="99_100_1_2", data=DATA)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(entity(hass, entry, "sensor", "status")).state == "unavailable"
    assert entry.runtime_data.live.update_interval == timedelta(seconds=60)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_diagnostics_hide_key_stop_and_position(hass, aioclient_mock, freezer):
    from custom_components.jatskiauto.diagnostics import async_get_config_entry_diagnostics

    freezer.move_to("2027-06-04 17:00:00+03:00")
    Truck(aioclient_mock)
    entry = await setup(hass, aioclient_mock)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = str(diag)
    assert DATA_KEY not in text and "Keksitie" not in text and "99_100_1" not in text and "60.0" not in text
    assert diag["route"]["stop_count"] == 4 and diag["route"]["road_geometry_legs"] == 3
    await hass.config_entries.async_unload(entry.entry_id)


async def test_within_threshold_stays_on_when_estimate_moves_back(hass, aioclient_mock, freezer):
    """The estimate may move later again; the binary sensor must not flap."""
    freezer.move_to("2027-06-04 16:09:00+03:00")
    truck = Truck(aioclient_mock)
    entry = await setup(hass, aioclient_mock, {CONF_NOTIFY_MINUTES: 60})
    await step(hass, freezer, 61)
    sensor = entity(hass, entry, "binary_sensor", "within_threshold")
    assert hass.states.get(sensor).state == "on"
    hass.config_entries.async_update_entry(entry, options={CONF_NOTIFY_MINUTES: 0})
    await hass.async_block_till_done()
    truck.lon = STOP_LON[0]
    await step(hass, freezer, 60)
    assert hass.states.get(sensor).state == "on"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_restart_during_visit_does_not_repeat_notification(hass, aioclient_mock, freezer, hass_storage):
    freezer.move_to("2027-06-04 16:09:00+03:00")
    Truck(aioclient_mock)
    entry = await setup(hass, aioclient_mock, {CONF_NOTIFY_MINUTES: 60})
    event_id = entity(hass, entry, "event", "events")
    events = []

    def on_event(e):
        new = e.data["new_state"]
        # Like the blueprint: only fresh events count; a state restored at startup is not one.
        if e.data["entity_id"] == event_id and new is not None and new.attributes.get("event_type"):
            if abs((dt_util.utcnow() - dt_util.parse_datetime(new.state)).total_seconds()) < 10:
                events.append(new.attributes["event_type"])

    hass.bus.async_listen("state_changed", on_event)
    await step(hass, freezer, 61)
    assert events == ["tracking_started", "within_threshold"]
    await step(hass, freezer, 30)  # visit state written; a restart takes a while
    assert hass_storage[f"jatskiauto.visit.{entry.entry_id}"]["data"]["fired"] == ["tracking_started", "within_threshold"]

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await step(hass, freezer, 60)
    assert "within_threshold" not in events[2:] and "tracking_started" not in events[2:]
    assert hass.states.get(entity(hass, entry, "binary_sensor", "within_threshold")).state == "on"
    await hass.config_entries.async_unload(entry.entry_id)

