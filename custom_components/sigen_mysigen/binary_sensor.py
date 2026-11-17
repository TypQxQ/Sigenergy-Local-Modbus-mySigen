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
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [BatteryOverrideActive(coordinator, "battery_override")]
    if coordinator.has_dc_charger:
        entities.append(V2xAllowed(coordinator, "dc_charger_v2x"))
    async_add_entities(entities)


class BatteryOverrideActive(MySigenEntity, BinarySensorEntity):
    """On while a temporary battery override is running."""

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.override.enabled


class V2xAllowed(MySigenEntity, BinarySensorEntity):
    """On when the car may supply power through the DC charger."""

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.v2x_enabled
