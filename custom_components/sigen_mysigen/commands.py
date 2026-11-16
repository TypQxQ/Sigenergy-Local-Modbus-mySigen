"""Run a battery override command for an action or a button.

The rules live in control.py. This module only supplies the Remote EMS
reading and turns the outcome into Home Assistant errors.
"""
from __future__ import annotations

from collections.abc import Awaitable

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import CONF_PLANT_ENTRY_ID
from .control import (
    CommandError,
    CommandRefused,
    async_cancel_override,
    async_start_override,
)
from .coordinator import MySigenCoordinator
from .link import remote_ems_state


async def async_run_command(
    hass: HomeAssistant,
    coordinator: MySigenCoordinator,
    *,
    mode: str | None = None,
    minutes: int | None = None,
    power_kw: float | None = None,
) -> None:
    """Start an override when a mode is given, otherwise cancel the running one."""
    try:
        if mode is None:
            await async_cancel_override(coordinator)
            return
        plant_entry_id = coordinator.config_entry.data[CONF_PLANT_ENTRY_ID]
        await async_start_override(
            coordinator,
            mode=mode,
            minutes=minutes,
            power_kw=power_kw,
            remote_ems=lambda: remote_ems_state(hass, plant_entry_id),
        )
    except CommandRefused as err:
        raise ServiceValidationError(str(err)) from err
    except CommandError as err:
        # The plant may have changed; let the next poll show it soon.
        await coordinator.async_request_refresh()
        raise HomeAssistantError(str(err)) from err


async def async_run_setting(coordinator: MySigenCoordinator, change: Awaitable[str]) -> None:
    """Run a setting change for a switch, number or dropdown."""
    try:
        await change
    except CommandRefused as err:
        raise ServiceValidationError(str(err)) from err
    except CommandError as err:
        # The setting may have changed; read the settings again at the next poll.
        coordinator.read_settings_soon()
        await coordinator.async_request_refresh()
        raise HomeAssistantError(str(err)) from err
