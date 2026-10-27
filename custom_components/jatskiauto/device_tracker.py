"""The truck on the map while it is being tracked."""

from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.const import ATTR_GPS_ACCURACY, ATTR_LATITUDE, ATTR_LONGITUDE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import JatskiautoConfigEntry
from .entity import JatskiautoEntity
from .live import LiveCoordinator, Position
from .tracker import haversine_m

# Smaller moves are GPS jitter or a parked truck: not worth a state write.
MIN_MOVE_M = 30


async def async_setup_entry(
    hass: HomeAssistant, entry: JatskiautoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([TruckTracker(entry.runtime_data.live)])


class TruckTracker(JatskiautoEntity[LiveCoordinator], TrackerEntity):
    """Last GPS position of the tracked truck; unknown outside the tracking window."""

    _attr_source_type = SourceType.GPS
    # The live map shows the position, but no coordinates go to the history
    # database: only the state (e.g. not_home) is recorded.
    _unrecorded_attributes = frozenset({ATTR_LATITUDE, ATTR_LONGITUDE, ATTR_GPS_ACCURACY})

    def __init__(self, coordinator: LiveCoordinator) -> None:
        super().__init__(coordinator, "truck")
        self._shown: Position | None = None
        self._take(coordinator.data.position if coordinator.data else None)

    def _take(self, pos: Position | None) -> None:
        self._shown = pos
        self._attr_latitude = pos.latitude if pos else None
        self._attr_longitude = pos.longitude if pos else None
        self._attr_location_accuracy = pos.accuracy if pos else 0

    @callback
    def _handle_coordinator_update(self) -> None:
        pos = self.coordinator.data.position if self.coordinator.data else None
        old = self._shown
        if pos is not None and old is not None and haversine_m(old.latitude, old.longitude, pos.latitude, pos.longitude) < MIN_MOVE_M:
            return
        if pos is None and old is None:
            return
        self._take(pos)
        self.async_write_ha_state()
