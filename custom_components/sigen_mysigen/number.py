"""The duration used by the Start battery override button."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import MAX_OVERRIDE_MINUTES, MAX_POWER_LIMIT_KW, MIN_OVERRIDE_MINUTES
from .const import CONF_OVERRIDE_MINUTES, CONF_OVERRIDE_POWER
from .entity import MySigenEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the duration. Only set up while control is allowed."""
    coordinator = entry.runtime_data
    async_add_entities([
        OverrideDuration(coordinator, "battery_override_duration"),
        OverridePowerLimit(coordinator, "battery_override_power_limit"),
    ])


class OverrideDuration(MySigenEntity, NumberEntity):
    """Stored in Home Assistant. Changing it sends nothing to the plant."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = MIN_OVERRIDE_MINUTES
    _attr_native_max_value = MAX_OVERRIDE_MINUTES
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_mode = NumberMode.BOX

    @property
    def available(self) -> bool:
        # A local setting; it does not depend on mySigen being reachable.
        return True

    @property
    def native_value(self) -> int:
        return self.coordinator.override_minutes

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.set_option(CONF_OVERRIDE_MINUTES, int(value))
        self.async_write_ha_state()


class OverridePowerLimit(MySigenEntity, NumberEntity):
    """The most power a charge or discharge override may use; 0 means no limit.

    Stored in Home Assistant. Changing it sends nothing to the plant.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_POWER_LIMIT_KW
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_mode = NumberMode.BOX

    @property
    def suggested_object_id(self) -> str | None:
        # The name explains "0 = no limit"; keep that out of the entity ID.
        return "battery override power limit"

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> float:
        return self.coordinator.override_power_kw or 0.0

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.set_option(CONF_OVERRIDE_POWER, round(float(value), 1))
        self.async_write_ha_state()
