"""The duration used by the Start battery override button."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import (
    MAX_OVERRIDE_MINUTES,
    MAX_PEAK_SHAVING_KW,
    MAX_POWER_LIMIT_KW,
    MIN_OVERRIDE_MINUTES,
)
from .commands import async_run_setting
from .const import CONF_OVERRIDE_MINUTES, CONF_OVERRIDE_POWER, CONF_PLANT_ENTRY_ID
from .control import (
    async_set_dc_discharge_floor,
    async_set_dc_stop_level,
    async_set_peak_shaving,
)
from .link import remote_ems_state
from .entity import MySigenEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the duration. Only set up while control is allowed."""
    coordinator = entry.runtime_data
    entities: list[NumberEntity] = [
        OverrideDuration(coordinator, "battery_override_duration"),
        OverridePowerLimit(coordinator, "battery_override_power_limit"),
        PeakShavingLimit(coordinator, "peak_shaving_limit"),
    ]
    if coordinator.has_dc_charger:
        entities += [
            DcStopChargingAt(coordinator, "dc_charger_stop_charging_at"),
            DcDischargeFloor(coordinator, "dc_charger_discharge_floor"),
        ]
    async_add_entities(entities)


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
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> float:
        return self.coordinator.override_power_kw or 0.0

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.set_option(CONF_OVERRIDE_POWER, round(float(value), 1))
        self.async_write_ha_state()


class PeakShavingLimit(MySigenEntity, NumberEntity):
    """The grid power the plant tries to stay under while peak shaving is on.

    Only available when the schedule has a single time slot; a schedule with
    several slots is changed in the mySigen app.
    """

    _attr_entity_registry_enabled_default = False
    _attr_native_min_value = 0.1
    _attr_native_max_value = MAX_PEAK_SHAVING_KW
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_mode = NumberMode.BOX

    @property
    def available(self) -> bool:
        peak = self.coordinator.peak_shaving
        return super().available and peak is not None and peak.single_limit is not None

    @property
    def native_value(self) -> float | None:
        peak = self.coordinator.peak_shaving
        return peak.single_limit if peak is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await async_run_setting(
            self.coordinator,
            async_set_peak_shaving(self.coordinator, limit_kw=round(float(value), 1)),
        )


class _DcLevel(MySigenEntity, NumberEntity):
    """A vehicle charge level for the DC charger, within mySigen's range."""

    _attr_entity_registry_enabled_default = False
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.BOX

    @property
    def native_min_value(self) -> float:
        return self.coordinator.soc_range[0] if self.coordinator.soc_range else 0

    @property
    def native_max_value(self) -> float:
        return self.coordinator.soc_range[1] if self.coordinator.soc_range else 100

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.soc_range is not None
            and self.native_value is not None
        )

    def _remote_ems(self) -> str | None:
        return remote_ems_state(
            self.hass, self.coordinator.config_entry.data[CONF_PLANT_ENTRY_ID]
        )


class DcStopChargingAt(_DcLevel):
    """Charging stops when the car reaches this level."""

    @property
    def native_value(self) -> int | None:
        setting = self.coordinator.dc_charge_setting
        return setting.stop_level if setting is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await async_run_setting(self.coordinator, async_set_dc_stop_level(
            self.coordinator, level=int(round(value)), remote_ems=self._remote_ems,
        ))


class DcDischargeFloor(_DcLevel):
    """The car stops supplying power at this level."""

    @property
    def native_value(self) -> int | None:
        mode = self.coordinator.dc_charge_mode
        return mode.discharge_floor if mode is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await async_run_setting(self.coordinator, async_set_dc_discharge_floor(
            self.coordinator, level=int(round(value)), remote_ems=self._remote_ems,
        ))
