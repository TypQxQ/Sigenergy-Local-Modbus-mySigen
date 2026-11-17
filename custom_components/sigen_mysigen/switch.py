"""Switches for plant settings that stay until changed back (step C)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .commands import async_run_setting
from .control import async_set_battery_export, async_set_peak_shaving
from .entity import MySigenEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the switches. Only set up while control is allowed."""
    coordinator = entry.runtime_data
    async_add_entities([
        BatteryExport(coordinator, "battery_export"),
        PeakShaving(coordinator, "peak_shaving"),
    ])


class BatteryExport(MySigenEntity, SwitchEntity):
    """On: the battery may send energy to the grid. Off: only solar is exported."""

    _attr_entity_registry_enabled_default = False

    @property
    def available(self) -> bool:
        export = self.coordinator.battery_export
        return super().available and export is not None and export.owner is not None

    @property
    def is_on(self) -> bool | None:
        export = self.coordinator.battery_export
        return export.owner if export is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        # What applies right now; an installer setting can override the owner's.
        export = self.coordinator.battery_export
        return {"in_effect": export.effective} if export is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await async_run_setting(
            self.coordinator, async_set_battery_export(self.coordinator, enabled=True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await async_run_setting(
            self.coordinator, async_set_battery_export(self.coordinator, enabled=False)
        )


class PeakShaving(MySigenEntity, SwitchEntity):
    """On: the battery helps keep grid power under the peak shaving limit."""

    _attr_entity_registry_enabled_default = False

    @property
    def available(self) -> bool:
        peak = self.coordinator.peak_shaving
        return super().available and peak is not None and peak.enabled is not None

    @property
    def is_on(self) -> bool | None:
        peak = self.coordinator.peak_shaving
        return peak.enabled if peak is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        peak = self.coordinator.peak_shaving
        return {"time_slots": len(peak.slots)} if peak is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await async_run_setting(
            self.coordinator, async_set_peak_shaving(self.coordinator, enabled=True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await async_run_setting(
            self.coordinator, async_set_peak_shaving(self.coordinator, enabled=False)
        )
