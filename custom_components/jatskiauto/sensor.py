"""Sensors for Jätskiauto."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import SCHEDULE_TZ
from .coordinator import JatskiautoConfigEntry, RouteDataCoordinator
from .entity import JatskiautoEntity
from .live import STATUSES, LiveCoordinator, LiveState
from .stops import next_visit


def _minute(value: datetime | None) -> datetime | None:
    """Timestamps are shown to the minute; this also keeps state writes rare."""
    return value.replace(second=0, microsecond=0) if value else None


@dataclass(frozen=True, kw_only=True)
class LiveSensorDescription(SensorEntityDescription):
    value_fn: Callable[[LiveState], Any]
    attrs_fn: Callable[[LiveState], dict[str, Any]] | None = None


def _status_attrs(s: LiveState) -> dict[str, Any]:
    est = s.estimate
    return {
        "scheduled": s.visit.scheduled.isoformat() if s.visit else None,
        "tracking_window_start": s.visit.start.isoformat() if s.visit else None,
        "tracking_window_end": s.visit.end.isoformat() if s.visit else None,
        "estimate_source": est.source if est else None,
        "lateness_minutes": round((est.likely - s.visit.scheduled).total_seconds() / 60) if est and s.visit else None,
        "vehicle": s.device_name,
    }


LIVE_SENSORS = [
    LiveSensorDescription(
        key="status", device_class=SensorDeviceClass.ENUM, options=STATUSES, value_fn=lambda s: s.status, attrs_fn=_status_attrs
    ),
    LiveSensorDescription(
        key="eta_likely", device_class=SensorDeviceClass.TIMESTAMP, value_fn=lambda s: _minute(s.estimate.likely) if s.estimate else None
    ),
    LiveSensorDescription(
        key="eta_earliest", device_class=SensorDeviceClass.TIMESTAMP, value_fn=lambda s: _minute(s.estimate.earliest) if s.estimate else None
    ),
    LiveSensorDescription(key="stops_remaining", value_fn=lambda s: s.stops_remaining),
    LiveSensorDescription(key="arrival", device_class=SensorDeviceClass.TIMESTAMP, value_fn=lambda s: _minute(s.arrival)),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: JatskiautoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    data = entry.runtime_data
    async_add_entities([NextVisitSensor(data.route), *(LiveSensor(data.live, d) for d in LIVE_SENSORS)])


class LiveSensor(JatskiautoEntity[LiveCoordinator], SensorEntity):
    """A value from the live state."""

    entity_description: LiveSensorDescription

    def __init__(self, coordinator: LiveCoordinator, description: LiveSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data) if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator.data) if fn and self.coordinator.data else None


class NextVisitSensor(JatskiautoEntity[RouteDataCoordinator], SensorEntity):
    """Next scheduled visit at the followed stop."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: RouteDataCoordinator) -> None:
        super().__init__(coordinator, "next_visit")

    @property
    def native_value(self) -> datetime | None:
        target = self.coordinator.data.target
        return next_visit(target, dt_util.now()) if target else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        target = self.coordinator.data.target
        if not target:
            return {"stop_found": False}
        now = dt_util.now(SCHEDULE_TZ)
        upcoming = [s for s in target.schedules if f"{s['date']} {s['time']}" >= f"{now.date().isoformat()} {now:%H:%M}"]
        return {
            "stop_found": True,
            "stop_name": target.name,
            "stop_number": target.index + 1,
            "upcoming_visits": [f"{s['date']} {s['time']}" for s in upcoming],
            "season_visit_count": len(target.schedules),
        }
