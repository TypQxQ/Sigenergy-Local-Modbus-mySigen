"""Sensors for the mySigen cloud add-on."""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import OVERRIDE_MODES
from .entity import MySigenEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the override mode, its end time and the update time."""
    coordinator = entry.runtime_data
    async_add_entities([
        BatteryOverrideMode(coordinator, "battery_override_mode"),
        BatteryOverrideEnd(coordinator, "battery_override_end"),
        LastUpdate(coordinator, "last_update"),
    ])


class BatteryOverrideMode(MySigenEntity, SensorEntity):
    """The mode of the running override; unknown when none is running."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(OVERRIDE_MODES.values())

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.mode


class BatteryOverrideEnd(MySigenEntity, SensorEntity):
    """When the running override ends; unknown when none is running."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.data.ends_at


class LastUpdate(MySigenEntity, SensorEntity):
    """When mySigen last answered, so old cloud values can be recognised."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        # Stays readable during an outage; that is when it matters.
        return True

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.last_success
