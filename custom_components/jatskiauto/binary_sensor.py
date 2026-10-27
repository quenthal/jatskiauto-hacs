"""Binary sensor: the truck can be at the stop within the notification lead time."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import JatskiautoConfigEntry
from .entity import JatskiautoEntity
from .live import LiveCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: JatskiautoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([WithinThresholdSensor(entry.runtime_data.live)])


class WithinThresholdSensor(JatskiautoEntity[LiveCoordinator], BinarySensorEntity):
    """On from 'can be here within N minutes' until the truck has passed."""

    def __init__(self, coordinator: LiveCoordinator) -> None:
        super().__init__(coordinator, "within_threshold")

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data and self.coordinator.data.within_threshold)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"threshold_minutes": self.coordinator.notify_minutes}
