"""Diagnostics for the mySigen cloud add-on.

The download must stay safe to post in a public issue: no credentials,
tokens, station id, serial numbers or raw mySigen answers.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import CONF_PLANT_ENTRY_ID, DOMAIN, MODBUS_DOMAIN
from .link import local_inverter_serials

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return the state of one mySigen connection."""
    coordinator = entry.runtime_data
    plant_entry_id = entry.data[CONF_PLANT_ENTRY_ID]
    plant_entry = hass.config_entries.async_get_entry(plant_entry_id)
    addon = await async_get_integration(hass, DOMAIN)
    modbus = await async_get_integration(hass, MODBUS_DOMAIN)
    status = coordinator.data

    return {
        "addon_version": str(addon.version),
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "modbus": {
            "version": str(modbus.version),
            "plant_entry_state": plant_entry.state.value if plant_entry else None,
            # A count only; the serial numbers themselves stay out.
            "inverter_serials_known": len(local_inverter_serials(hass, plant_entry_id)),
        },
        "station_check": coordinator.link_problem or "ok",
        "polling": {
            "interval_seconds": coordinator.update_interval.total_seconds(),
            "last_update_success": coordinator.last_update_success,
            "last_success": (
                coordinator.last_success.isoformat() if coordinator.last_success else None
            ),
            "last_error_type": (
                type(coordinator.last_exception).__name__
                if coordinator.last_exception
                else None
            ),
            "rate_limit_wait_seconds": round(coordinator.rate_limit_wait),
        },
        "battery_override": None if status is None else {
            "enabled": status.enabled,
            "mode": status.mode,
            "ends_at": status.ends_at.isoformat() if status.ends_at else None,
        },
    }
