"""Sensors for Jätskiauto."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import CONF_ROUTE_ID, CONF_STOP_INDEX, CONF_STOP_NAME, DOMAIN, SCHEDULE_TZ
from .coordinator import JatskiautoConfigEntry, RouteDataCoordinator
from .stops import next_visit


async def async_setup_entry(
    hass: HomeAssistant, entry: JatskiautoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([NextVisitSensor(entry.runtime_data.route)])


def device_info(entry: JatskiautoConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Jätskiauto {entry.data[CONF_STOP_NAME]}",
        manufacturer="Paikannuspalvelu",
        model=f"Reitti {entry.data[CONF_ROUTE_ID]}, pysäkki {entry.data[CONF_STOP_INDEX] + 1}",
        entry_type=DeviceEntryType.SERVICE,
    )


class NextVisitSensor(CoordinatorEntity[RouteDataCoordinator], SensorEntity):
    """Next scheduled visit at the followed stop."""

    _attr_has_entity_name = True
    _attr_translation_key = "next_visit"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: RouteDataCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_next_visit"
        self._attr_device_info = device_info(entry)

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
