"""The duration used by the Start battery override button."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import (
    MAX_OVERRIDE_MINUTES,
    MAX_PEAK_SHAVING_KW,
    MAX_POWER_LIMIT_KW,
    MIN_OVERRIDE_MINUTES,
)
from .commands import async_run_setting
from .const import CONF_OVERRIDE_MINUTES, CONF_OVERRIDE_POWER
from .control import (
    async_set_ac_boost_cutoff,
    async_set_dc_boost_cutoff,
    async_set_dc_discharge_floor,
    async_set_dc_stop_level,
    async_set_grid_current_limit,
    async_set_peak_shaving,
    boost_cutoff_range,
    dc_floor_range,
    dc_stop_range,
)
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
        GridCurrentLimitNumber(coordinator, "grid_current_limit"),
    ]
    if coordinator.has_dc_charger:
        entities += [
            DcStopChargingAt(coordinator, "dc_charger_stop_charging_at"),
            DcDischargeFloor(coordinator, "dc_charger_discharge_floor"),
            DcBoostCutoffLevel(coordinator, "dc_charger_boost_cutoff_level"),
        ]
    if coordinator.has_ac_charger:
        entities.append(AcBoostCutoffLevel(coordinator, "ac_charger_boost_cutoff_level"))
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

    The value shows when the schedule has a single time slot, and only then
    can it be changed. With no slot or several, it shows unknown, the
    `time_slots` attribute says why, and a change is refused with the reason.
    """

    _attr_entity_registry_enabled_default = False
    _attr_native_min_value = 0.1
    _attr_native_max_value = MAX_PEAK_SHAVING_KW
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_mode = NumberMode.BOX

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.peak_shaving is not None

    @property
    def native_value(self) -> float | None:
        peak = self.coordinator.peak_shaving
        return peak.single_limit if peak is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, int] | None:
        peak = self.coordinator.peak_shaving
        return {"time_slots": len(peak.slots)} if peak is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await async_run_setting(
            self.coordinator,
            async_set_peak_shaving(self.coordinator, limit_kw=round(float(value), 1)),
        )


class GridCurrentLimitNumber(MySigenEntity, NumberEntity):
    """The most current per phase the plant may draw from the grid.

    The app's Grid Settings > Max Grid Connection Current. It cannot go
    above the installer's ceiling.
    """

    _attr_entity_registry_enabled_default = False
    _attr_native_min_value = 1
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_mode = NumberMode.BOX

    @property
    def available(self) -> bool:
        # Without a limit it stays available and shows unknown; setting a
        # value switches the limit on.
        return super().available and self.coordinator.grid_current_limit is not None

    @property
    def native_max_value(self) -> float:
        limit = self.coordinator.grid_current_limit
        return limit.installer if limit is not None and limit.installer else 1000.0

    @property
    def native_value(self) -> float | None:
        limit = self.coordinator.grid_current_limit
        return limit.effective if limit is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        limit = self.coordinator.grid_current_limit
        if limit is None:
            return None
        return {"in_effect": limit.enabled, "installer_limit": limit.installer}

    async def async_set_native_value(self, value: float) -> None:
        await async_run_setting(
            self.coordinator,
            async_set_grid_current_limit(self.coordinator, amps=float(value)),
        )


class _DcLevel(MySigenEntity, NumberEntity):
    """A charge level in percent, of the car or the home battery, within a range."""

    _attr_entity_registry_enabled_default = False
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.BOX

    def _range(self) -> tuple[int, int]:
        raise NotImplementedError

    @property
    def native_min_value(self) -> float:
        return self._range()[0]

    @property
    def native_max_value(self) -> float:
        return self._range()[1]

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None


class DcStopChargingAt(_DcLevel):
    """Charging stops when the car reaches this level."""

    def _range(self) -> tuple[int, int]:
        return dc_stop_range(self.coordinator)

    @property
    def native_value(self) -> int | None:
        setting = self.coordinator.dc_charge_setting
        return setting.stop_level if setting is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await async_run_setting(self.coordinator, async_set_dc_stop_level(
            self.coordinator, level=int(round(value)),
        ))


class DcDischargeFloor(_DcLevel):
    """The car stops supplying power at this level."""

    def _range(self) -> tuple[int, int]:
        return dc_floor_range(self.coordinator)

    @property
    def native_value(self) -> int | None:
        mode = self.coordinator.dc_charge_mode
        return mode.discharge_floor if mode is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await async_run_setting(self.coordinator, async_set_dc_discharge_floor(
            self.coordinator, level=int(round(value)),
        ))


class DcBoostCutoffLevel(_DcLevel):
    """DC battery boost stops when the home battery reaches this level."""

    def _range(self) -> tuple[int, int]:
        return boost_cutoff_range(self.coordinator)

    @property
    def native_value(self) -> int | None:
        mode = self.coordinator.dc_charge_mode
        return mode.boost_cutoff if mode is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await async_run_setting(self.coordinator, async_set_dc_boost_cutoff(
            self.coordinator, level=int(round(value)),
        ))


class AcBoostCutoffLevel(_DcLevel):
    """Battery boost stops when the home battery reaches this level."""

    def _range(self) -> tuple[int, int]:
        return boost_cutoff_range(self.coordinator)

    @property
    def native_value(self) -> int | None:
        mode = self.coordinator.ac_charge_mode
        return mode.boost_cutoff if mode is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await async_run_setting(self.coordinator, async_set_ac_boost_cutoff(
            self.coordinator, level=int(round(value)),
        ))
