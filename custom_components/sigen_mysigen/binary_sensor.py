"""Binary sensors for the mySigen cloud add-on."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import MySigenEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the override state."""
    async_add_entities([BatteryOverrideActive(entry.runtime_data, "battery_override")])


class BatteryOverrideActive(MySigenEntity, BinarySensorEntity):
    """On while a temporary battery override is running."""

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.enabled
