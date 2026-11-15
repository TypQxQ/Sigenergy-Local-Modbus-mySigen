"""Base entity that places mySigen values on the Modbus plant device."""
from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_PLANT_ENTRY_ID, MODBUS_DOMAIN
from .coordinator import MySigenCoordinator


class MySigenEntity(CoordinatorEntity[MySigenCoordinator]):
    """A mySigen value shown on the existing plant device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MySigenCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        # Link to the device the Modbus integration owns, the way helper
        # entities do. Passing device_info with its identifier instead makes
        # Home Assistant create a second device for this config entry.
        plant_entry_id = entry.data[CONF_PLANT_ENTRY_ID]
        identifier = (MODBUS_DOMAIN, f"{plant_entry_id}_plant")
        self.device_entry = next(
            (
                device
                for device in dr.async_entries_for_config_entry(
                    dr.async_get(coordinator.hass), plant_entry_id
                )
                if identifier in device.identifiers
            ),
            None,
        )
