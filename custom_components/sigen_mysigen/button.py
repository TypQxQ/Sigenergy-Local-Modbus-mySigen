"""Buttons that start and stop the battery override from a dashboard."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .commands import async_run_command
from .entity import MySigenEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the two buttons. Only set up while control is allowed."""
    coordinator = entry.runtime_data
    async_add_entities([
        StartOverride(coordinator, "start_battery_override"),
        StopOverride(coordinator, "stop_battery_override"),
    ])


class StartOverride(MySigenEntity, ButtonEntity):
    """Start the chosen override for the chosen duration."""

    async def async_press(self) -> None:
        await async_run_command(
            self.hass,
            self.coordinator,
            mode=self.coordinator.override_mode,
            minutes=self.coordinator.override_minutes,
            power_kw=self.coordinator.override_power_kw,
        )


class StopOverride(MySigenEntity, ButtonEntity):
    """End the running override."""

    async def async_press(self) -> None:
        await async_run_command(self.hass, self.coordinator)
