"""Sigenergy ESS mySigen cloud add-on.

Reads mySigen for a plant that the Sigenergy Local Modbus integration
already provides. It sends no commands.
"""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    issue_registry as ir,
)

from .api import (
    CONTROL_MODES,
    MAX_OVERRIDE_MINUTES,
    MIN_OVERRIDE_MINUTES,
    MySigenAuthError,
    MySigenError,
    MySigenRateLimitError,
)
from .const import CONF_PLANT_ENTRY_ID, DOMAIN, MODBUS_DOMAIN
from .control import (
    CommandError,
    CommandRefused,
    async_cancel_override,
    async_start_override,
)
from .coordinator import MySigenCoordinator
from .link import STATION_MISMATCH, async_link_problem, remote_ems_state

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]

NO_PERMISSION = "no_permission"
_ISSUES = ("plant_missing", STATION_MISMATCH, NO_PERMISSION)


SERVICE_START = "start_battery_override"
SERVICE_CANCEL = "cancel_battery_override"
ATTR_ENTRY_ID = "config_entry_id"
ATTR_MODE = "mode"
ATTR_DURATION = "duration"

_CANCEL_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTRY_ID): cv.string})
_START_SCHEMA = _CANCEL_SCHEMA.extend({
    vol.Required(ATTR_MODE): vol.In(CONTROL_MODES),
    vol.Required(ATTR_DURATION): vol.All(
        vol.Coerce(int), vol.Range(MIN_OVERRIDE_MINUTES, MAX_OVERRIDE_MINUTES)
    ),
})


def _target_coordinator(hass: HomeAssistant, call: ServiceCall) -> MySigenCoordinator:
    """Find the connection an action is for."""
    loaded = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if entry_id := call.data.get(ATTR_ENTRY_ID):
        loaded = [entry for entry in loaded if entry.entry_id == entry_id]
        if not loaded:
            raise ServiceValidationError("That mySigen connection is not loaded.")
    elif len(loaded) != 1:
        raise ServiceValidationError(
            "Choose which mySigen connection to use."
            if loaded
            else "No mySigen connection is loaded."
        )
    return loaded[0].runtime_data


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the actions once; each call picks its connection."""

    async def handle(call: ServiceCall) -> None:
        coordinator = _target_coordinator(hass, call)
        try:
            if call.service == SERVICE_START:
                plant_entry_id = coordinator.config_entry.data[CONF_PLANT_ENTRY_ID]
                await async_start_override(
                    coordinator,
                    mode=call.data[ATTR_MODE],
                    minutes=call.data[ATTR_DURATION],
                    remote_ems=lambda: remote_ems_state(hass, plant_entry_id),
                )
            else:
                await async_cancel_override(coordinator)
        except CommandRefused as err:
            raise ServiceValidationError(str(err)) from err
        except CommandError as err:
            # The plant may have changed; let the next poll show it soon.
            await coordinator.async_request_refresh()
            raise HomeAssistantError(str(err)) from err

    hass.services.async_register(DOMAIN, SERVICE_START, handle, schema=_START_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CANCEL, handle, schema=_CANCEL_SCHEMA)
    return True


def _issue_id(key: str, entry: ConfigEntry) -> str:
    return f"{key}_{entry.entry_id}"


def _raise_link_issue(hass: HomeAssistant, entry: ConfigEntry, key: str) -> None:
    """Show a repair notice and stop setting up this connection."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(key, entry),
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=key,
        translation_placeholders={"name": entry.title},
    )
    raise ConfigEntryError(f"mySigen connection stopped: {key}")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a mySigen connection linked to one Modbus plant."""
    plant_entry_id = entry.data[CONF_PLANT_ENTRY_ID]
    plant_entry = hass.config_entries.async_get_entry(plant_entry_id)
    if plant_entry is None or plant_entry.domain != MODBUS_DOMAIN:
        _raise_link_issue(hass, entry, "plant_missing")

    # This add-on owns no devices. Drop any that an earlier version created
    # for this entry; its entities live on the Modbus plant device.
    device_registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        device_registry.async_remove_device(device.id)

    coordinator = MySigenCoordinator(hass, entry)

    # Send nothing while mySigen has asked for fewer requests. The wait is
    # kept across setup attempts, so a retry lands here until it is over.
    if wait := coordinator.rate_limit_wait:
        raise ConfigEntryNotReady(f"Waiting {wait:.0f} s after a mySigen rate limit")

    # Refuse an account whose station does not contain this plant's inverter.
    # When the comparison cannot be made, keep running and say so.
    try:
        problem = await async_link_problem(hass, coordinator.session, plant_entry_id)
    except MySigenAuthError as err:
        raise ConfigEntryAuthFailed(
            f"mySigen refused the stored account ({err})"
        ) from err
    except MySigenRateLimitError as err:
        wait = coordinator.note_rate_limit(err)
        raise ConfigEntryNotReady(
            f"mySigen rate limit, waiting {wait:.0f} s"
        ) from err
    except MySigenError as err:
        problem = str(err)
    if problem == STATION_MISMATCH:
        _raise_link_issue(hass, entry, STATION_MISMATCH)
    coordinator.link_problem = problem
    if problem is not None:
        _LOGGER.warning(
            "Could not verify the mySigen station for %s: %s", entry.title, problem
        )
    for key in _ISSUES:
        ir.async_delete_issue(hass, DOMAIN, _issue_id(key, entry))

    # A failure here only delays this entry; the Modbus plant is unaffected.
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        if coordinator.permission_denied:
            # Retrying cannot help; the account needs more access.
            _raise_link_issue(hass, entry, NO_PERMISSION)
        raise
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Forget the state kept for a removed connection."""
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    for key in _ISSUES:
        ir.async_delete_issue(hass, DOMAIN, _issue_id(key, entry))


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a mySigen connection."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
