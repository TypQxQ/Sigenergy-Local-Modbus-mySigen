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

from .api import MySigenError, MySigenRateLimitError, masked_shape
from .const import CONF_PLANT_ENTRY_ID, DOMAIN, MODBUS_DOMAIN
from .link import local_inverter_serials

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return the state of one mySigen connection."""
    plant_entry_id = entry.data[CONF_PLANT_ENTRY_ID]
    plant_entry = hass.config_entries.async_get_entry(plant_entry_id)
    addon = await async_get_integration(hass, DOMAIN)
    modbus = await async_get_integration(hass, MODBUS_DOMAIN)

    result: dict[str, Any] = {
        "addon_version": str(addon.version),
        "entry": {
            "state": entry.state.value,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "modbus": {
            "version": str(modbus.version),
            "plant_entry_state": plant_entry.state.value if plant_entry else None,
            # A count only; the serial numbers themselves stay out.
            "inverter_serials_known": len(local_inverter_serials(hass, plant_entry_id)),
        },
        "station_check": None,
        "polling": None,
        "battery_override": None,
    }

    # The coordinator only exists after a successful setup. A failed setup
    # is exactly when this download is wanted, so report what there is.
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None:
        return result

    data = coordinator.data
    status = data.override if data else None
    result["station_check"] = coordinator.link_problem or "ok"
    result["polling"] = {
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
    }
    if status is not None:
        result["battery_override"] = {
            "enabled": status.enabled,
            "mode": status.mode,
            "ends_at": status.ends_at.isoformat() if status.ends_at else None,
        }

    result["control"] = {
        "allowed": coordinator.allow_control,
        "last_command": coordinator.last_command,
    }
    gateway = data.gateway if data else None
    result["gateway"] = {
        "detected": coordinator.has_gateway,
        "readings": sorted(gateway.values) if gateway else None,
        "contactor": gateway.contactor if gateway else None,
    }

    # Structure of the Gateway answer, with every value masked. It is read
    # on demand for this download and is not polled.
    if coordinator.rate_limit_wait:
        result["gateway_shape"] = "skipped: waiting after a rate limit"
    else:
        try:
            result["gateway_shape"] = masked_shape(
                await coordinator.session.async_gateway()
            )
        except MySigenRateLimitError as err:
            # Keep the shared wait in step, so polling pauses as well.
            wait = coordinator.note_rate_limit(err)
            result["gateway_shape"] = f"rate limited: waiting {wait:.0f} s"
        except MySigenError as err:
            result["gateway_shape"] = f"unavailable: {type(err).__name__}: {err}"
    return result
