"""Event entity: tracking started, reached a stop, within threshold, arrived, passed, signal lost."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import JatskiautoConfigEntry
from .entity import JatskiautoEntity
from .live import EVENT_TYPES, LiveCoordinator, event_signal


async def async_setup_entry(
    hass: HomeAssistant, entry: JatskiautoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([JatskiautoEvent(entry.runtime_data.live)])


class JatskiautoEvent(JatskiautoEntity[LiveCoordinator], EventEntity):
    """Fires once per occasion; automations trigger on it."""

    _attr_event_types = EVENT_TYPES

    def __init__(self, coordinator: LiveCoordinator) -> None:
        super().__init__(coordinator, "events")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, event_signal(self.coordinator.config_entry.entry_id), self._handle_event)
        )

    @callback
    def _handle_event(self, event_type: str, data: dict[str, Any]) -> None:
        self._trigger_event(event_type, data)
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Only events change this entity."""
