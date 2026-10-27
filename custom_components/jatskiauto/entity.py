"""Base entity for Jätskiauto."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import CONF_ROUTE_ID, CONF_STOP_INDEX, CONF_STOP_NAME, DOMAIN


class JatskiautoEntity[C: DataUpdateCoordinator](CoordinatorEntity[C]):
    """One device per followed stop."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: C, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Jätskiauto {entry.data[CONF_STOP_NAME]}",
            manufacturer="Paikannuspalvelu",
            model=f"Reitti {entry.data[CONF_ROUTE_ID]}, pysäkki {entry.data[CONF_STOP_INDEX] + 1}",
            entry_type=DeviceEntryType.SERVICE,
        )
