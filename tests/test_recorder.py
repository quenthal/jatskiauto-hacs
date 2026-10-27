"""History database: the truck's coordinates are not recorded."""

from homeassistant.util import dt as dt_util
import pytest

from .test_live import Truck, entity, setup, step


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """The recorder has to start before Home Assistant in this module."""
    return


async def test_truck_position_not_recorded(hass, recorder_mock, aioclient_mock, freezer):
    """The map shows the truck, but no coordinates are written to the history."""
    from homeassistant.components.recorder.history import get_significant_states
    from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

    freezer.move_to("2027-06-04 16:09:00+03:00")
    Truck(aioclient_mock)
    entry = await setup(hass, aioclient_mock)
    tracker_id = entity(hass, entry, "device_tracker", "truck")
    start = dt_util.utcnow()
    await step(hass, freezer, 61)
    live_state = hass.states.get(tracker_id)
    assert live_state.attributes["latitude"] == 60.0
    await async_wait_recording_done(hass)
    history = await recorder_mock.async_add_executor_job(get_significant_states, hass, start, None, [tracker_id])
    recorded = history[tracker_id]
    assert recorded and all("latitude" not in s.attributes and "longitude" not in s.attributes for s in recorded)
    await hass.config_entries.async_unload(entry.entry_id)
