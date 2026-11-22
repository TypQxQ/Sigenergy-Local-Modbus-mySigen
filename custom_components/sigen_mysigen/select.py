"""The override the Start battery override button will start."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import AI_STRATEGIES, CONTROL_MODES, DC_CHARGE_MODES, OPERATING_MODES
from .commands import async_run_setting
from .const import CONF_OVERRIDE_MODE, CONF_PLANT_ENTRY_ID
from .control import (
    async_set_ai_strategy,
    async_set_dc_charge_mode,
    async_set_operating_mode,
)
from .link import remote_ems_state
from .entity import MySigenEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the dropdown. Only set up while control is allowed."""
    coordinator = entry.runtime_data
    entities: list[SelectEntity] = [
        OverrideToStart(coordinator, "battery_override_to_start"),
        OperatingMode(coordinator, "operating_mode"),
        AiStrategy(coordinator, "ai_strategy"),
    ]
    if coordinator.has_dc_charger:
        entities.append(DcChargingMode(coordinator, "dc_charger_charging_mode"))
    async_add_entities(entities)


class OverrideToStart(MySigenEntity, SelectEntity):
    """Stored in Home Assistant. Changing it sends nothing to the plant.

    The options are fixed keys; what the user reads comes from translations.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(CONTROL_MODES)

    @property
    def available(self) -> bool:
        # A local setting; it does not depend on mySigen being reachable.
        return True

    @property
    def current_option(self) -> str:
        return self.coordinator.override_mode

    async def async_select_option(self, option: str) -> None:
        self.coordinator.set_option(CONF_OVERRIDE_MODE, option)
        self.async_write_ha_state()


class OperatingMode(MySigenEntity, SelectEntity):
    """How the plant runs day to day. It stays until changed back.

    Shows no option while the plant runs in a mode not offered here, such as
    Remote EMS or a custom profile.
    """

    _attr_entity_registry_enabled_default = False

    @property
    def options(self) -> list[str]:
        offered = self.coordinator.available_modes
        return [key for key, code in OPERATING_MODES.items() if code in offered]

    @property
    def available(self) -> bool:
        # Unavailable until the plant has listed the modes it offers.
        return (
            super().available
            and self.coordinator.operating_mode is not None
            and any(code in self.coordinator.available_modes for code in OPERATING_MODES.values())
        )

    @property
    def current_option(self) -> str | None:
        code = self.coordinator.operating_mode
        return next((key for key, value in OPERATING_MODES.items() if value == code), None)

    async def async_select_option(self, option: str) -> None:
        plant_entry_id = self.coordinator.config_entry.data[CONF_PLANT_ENTRY_ID]
        await async_run_setting(
            self.coordinator,
            async_set_operating_mode(
                self.coordinator,
                mode=option,
                remote_ems=lambda: remote_ems_state(self.hass, plant_entry_id),
            ),
        )


class DcChargingMode(MySigenEntity, SelectEntity):
    """Chooses how the DC charger charges. It stays until changed back."""

    _attr_entity_registry_enabled_default = False
    _attr_options = list(DC_CHARGE_MODES)

    @property
    def available(self) -> bool:
        mode = self.coordinator.dc_charge_mode
        return super().available and mode is not None and mode.mode is not None

    @property
    def current_option(self) -> str | None:
        mode = self.coordinator.dc_charge_mode
        return mode.mode if mode is not None else None

    async def async_select_option(self, option: str) -> None:
        await async_run_setting(
            self.coordinator, async_set_dc_charge_mode(self.coordinator, mode=option)
        )


class AiStrategy(MySigenEntity, SelectEntity):
    """Sigen AI's strategy preference. It stays until changed back.

    Unavailable unless the plant runs in Sigen AI mode.
    """

    _attr_entity_registry_enabled_default = False
    _attr_options = list(AI_STRATEGIES)

    @property
    def available(self) -> bool:
        settings = self.coordinator.ai_settings
        return (
            super().available
            and self.coordinator.operating_mode == OPERATING_MODES["sigen_ai"]
            and settings is not None
            and settings.strategy is not None
        )

    @property
    def current_option(self) -> str | None:
        settings = self.coordinator.ai_settings
        code = settings.strategy if settings is not None else None
        return next((key for key, value in AI_STRATEGIES.items() if value == code), None)

    async def async_select_option(self, option: str) -> None:
        await async_run_setting(
            self.coordinator, async_set_ai_strategy(self.coordinator, strategy=option)
        )
