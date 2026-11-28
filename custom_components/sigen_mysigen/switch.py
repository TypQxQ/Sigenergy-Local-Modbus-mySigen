"""Switches for plant settings that stay until changed back, and smart loads."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .commands import async_run_setting
from .const import CONF_PLANT_ENTRY_ID
from .api import OPERATING_MODES, SmartLoad
from .control import (
    async_set_ac_battery_boost,
    async_set_battery_export,
    async_set_dc_battery_boost,
    async_set_off_grid,
    async_set_peak_shaving,
    async_set_smart_load,
    async_set_v2x_enabled,
    async_set_vacation_mode,
)
from .entity import MySigenEntity
from .link import plant_grid_state


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the switches. Only set up while control is allowed."""
    coordinator = entry.runtime_data
    entities: list[SwitchEntity] = [
        BatteryExport(coordinator, "battery_export"),
        PeakShaving(coordinator, "peak_shaving"),
    ]
    if coordinator.allow_grid_disconnect:
        entities.append(GridDisconnect(coordinator, "grid_disconnect"))
    if coordinator.has_dc_charger:
        entities += [
            AllowV2x(coordinator, "dc_charger_allow_v2x"),
            AllowDcBatteryBoost(coordinator, "dc_charger_allow_battery_boost"),
        ]
    if coordinator.has_ac_charger:
        entities.append(AllowAcBatteryBoost(coordinator, "ac_charger_allow_battery_boost"))
    settings = coordinator.ai_settings
    if settings is None or settings.vacation_shown:
        # Unknown after a failed read: shown as unavailable until read.
        entities.append(VacationMode(coordinator, "vacation_mode"))
    entities.extend(SmartLoadSwitch(coordinator, load) for load in coordinator.smart_loads)
    async_add_entities(entities)


class BatteryExport(MySigenEntity, SwitchEntity):
    """On: the battery may send energy to the grid. Off: only solar is exported."""

    _attr_entity_registry_enabled_default = False

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.battery_export is not None

    @property
    def is_on(self) -> bool | None:
        export = self.coordinator.battery_export
        return export.allowed if export is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        # Whether export is allowed right now; an installer setting can
        # override the owner's.
        export = self.coordinator.battery_export
        return {"in_effect": export.allowed_in_effect} if export is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await async_run_setting(
            self.coordinator, async_set_battery_export(self.coordinator, allowed=True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await async_run_setting(
            self.coordinator, async_set_battery_export(self.coordinator, allowed=False)
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


class GridDisconnect(MySigenEntity, SwitchEntity):
    """On: the home is separated from the grid and runs on battery and solar."""

    _attr_entity_registry_enabled_default = False

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.grid_connection is not None

    @property
    def is_on(self) -> bool | None:
        connection = self.coordinator.grid_connection
        return connection.off_grid if connection is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        connection = self.coordinator.grid_connection
        if connection is None:
            return None
        return {"grid_connection": connection.state, "offered_by_mysigen": connection.offered}

    async def _async_set(self, off_grid: bool) -> None:
        plant_entry_id = self.coordinator.config_entry.data[CONF_PLANT_ENTRY_ID]
        await async_run_setting(
            self.coordinator,
            async_set_off_grid(
                self.coordinator,
                off_grid=off_grid,
                plant_grid=lambda: plant_grid_state(self.hass, plant_entry_id),
            ),
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)


class AllowV2x(MySigenEntity, SwitchEntity):
    """On: the car may supply power to the home through the DC charger."""

    _attr_entity_registry_enabled_default = False

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.v2x_enabled is not None

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.v2x_enabled

    async def _async_set(self, enabled: bool) -> None:
        await async_run_setting(
            self.coordinator, async_set_v2x_enabled(self.coordinator, enabled=enabled)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)


class AllowDcBatteryBoost(MySigenEntity, SwitchEntity):
    """On: the home battery helps charge the car, down to the cut-off level."""

    _attr_entity_registry_enabled_default = False

    @property
    def available(self) -> bool:
        mode = self.coordinator.dc_charge_mode
        return super().available and mode is not None and mode.boost is not None

    @property
    def is_on(self) -> bool | None:
        mode = self.coordinator.dc_charge_mode
        return mode.boost if mode is not None else None

    async def _async_set(self, enabled: bool) -> None:
        await async_run_setting(
            self.coordinator, async_set_dc_battery_boost(self.coordinator, enabled=enabled)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)


class AllowAcBatteryBoost(MySigenEntity, SwitchEntity):
    """On: the home battery helps charge the car, down to the cut-off level."""

    _attr_entity_registry_enabled_default = False

    @property
    def available(self) -> bool:
        mode = self.coordinator.ac_charge_mode
        return super().available and mode is not None and mode.boost is not None

    @property
    def is_on(self) -> bool | None:
        mode = self.coordinator.ac_charge_mode
        return mode.boost if mode is not None else None

    async def _async_set(self, enabled: bool) -> None:
        await async_run_setting(
            self.coordinator, async_set_ac_battery_boost(self.coordinator, enabled=enabled)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)


class VacationMode(MySigenEntity, SwitchEntity):
    """On: Sigen AI knows nobody is home, as with the app's vacation mode.

    Unavailable unless the plant runs in Sigen AI mode.
    """

    _attr_entity_registry_enabled_default = False

    @property
    def available(self) -> bool:
        settings = self.coordinator.ai_settings
        return (
            super().available
            and self.coordinator.operating_mode == OPERATING_MODES["sigen_ai"]
            and settings is not None
            and settings.vacation_shown and settings.vacation is not None
        )

    @property
    def is_on(self) -> bool | None:
        settings = self.coordinator.ai_settings
        return settings.vacation if settings is not None else None

    async def _async_set(self, enabled: bool) -> None:
        await async_run_setting(
            self.coordinator, async_set_vacation_mode(self.coordinator, enabled=enabled)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)


class SmartLoadSwitch(MySigenEntity, SwitchEntity):
    """On: the smart load on a SigenStor or Gateway port gets power.

    Works while mySigen controls the load by hand, where the app shows its
    switch. The states are read at every poll while a switch is enabled.
    """

    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: Any, load: SmartLoad) -> None:
        super().__init__(coordinator, "smart_load", f"smart_load_{load.load_id}")
        self._load = load
        self._attr_translation_placeholders = {"load": load.name}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.coordinator.watched_smart_loads.add(self._load.load_id)
        # Show the state now rather than after the next poll.
        await self.coordinator.async_request_refresh()

    async def async_will_remove_from_hass(self) -> None:
        self.coordinator.watched_smart_loads.discard(self._load.load_id)
        await super().async_will_remove_from_hass()

    @property
    def _state(self) -> Any:
        return self.coordinator.smart_load_states.get(self._load.load_id)

    @property
    def available(self) -> bool:
        state = self._state
        return super().available and state is not None and state.on is not None

    @property
    def is_on(self) -> bool | None:
        state = self._state
        return state.on if state is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self._state
        return {
            "port": self._load.path,
            "attached_to": self._load.host,
            "manual_control": state.manual if state is not None else None,
        }

    async def _async_set(self, on: bool) -> None:
        await async_run_setting(
            self.coordinator,
            async_set_smart_load(self.coordinator, load_id=self._load.load_id, on=on),
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)
