"""Sigenergy ESS mySigen cloud add-on.

Reads mySigen for a plant that the Sigenergy Local Modbus integration
already provides. It sends no commands.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    issue_registry as ir,
)

from .api import MySigenAuthError, MySigenError, MySigenRateLimitError
from .const import CONF_PLANT_ENTRY_ID, DOMAIN, MODBUS_DOMAIN
from .coordinator import MySigenCoordinator
from .link import STATION_MISMATCH, async_link_problem

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]

NO_PERMISSION = "no_permission"
_ISSUES = ("plant_missing", STATION_MISMATCH, NO_PERMISSION)


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
