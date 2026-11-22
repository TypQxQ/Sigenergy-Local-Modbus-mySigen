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

from .api import (
    AC_CHARGER_KEEP,
    AC_CHARGER_PROBES,
    MySigenError,
    MySigenRateLimitError,
    masked_shape,
)
from .const import CONF_PLANT_ENTRY_ID, DOMAIN, MODBUS_DOMAIN
from .link import local_inverter_serials
from .modules import SOURCES

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD}


def _small_ints(value: Any) -> list[int] | int | None:
    """Mode codes or a generation number as given; None for anything else."""
    def code(item: Any) -> int | None:
        if isinstance(item, bool):
            return None
        if isinstance(item, int) and 0 <= item < 1000:
            return item
        if isinstance(item, str) and item.strip().isdigit() and len(item.strip()) <= 3:
            return int(item)
        return None

    if isinstance(value, list):
        codes = [code(item) for item in value[:20]]
        return None if None in codes else codes
    return code(value)


async def _ac_charger_probe(coordinator: Any) -> dict[str, Any]:
    """The first AC charger's answers, read once for this download.

    Nothing reads them otherwise: the add-on has no AC charger entities yet.
    Codes and settings are kept, everything else is masked.
    """
    session = coordinator.session
    result: dict[str, Any] = {
        "listed": len(session.ac_serials),
        "station_fields": dict(session.ac_station_fields),
    }
    if not session.ac_serials:
        return result
    if wait := coordinator.rate_limit_wait:
        result["probe"] = f"rate limited: waiting {wait:.0f} s"
        return result
    serial = session.ac_serials[0]
    probe: dict[str, Any] = {}
    for name, _path in AC_CHARGER_PROBES:
        try:
            data = await session.async_ac_charger_raw(name, serial)
        except MySigenRateLimitError as err:
            wait = coordinator.note_rate_limit(err)
            probe[name] = f"rate limited: waiting {wait:.0f} s"
            break
        except MySigenError as err:
            probe[name] = f"unavailable: {type(err).__name__}: {err}"
            continue
        codes = _small_ints(data) if name in ("supported_modes", "generation") else None
        probe[name] = (
            {"value": codes} if codes is not None
            else masked_shape(data, keep=AC_CHARGER_KEEP)
        )
    result["probe"] = probe
    return result


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
            "stated_by_mysigen": status.explicit,
            "mode": status.mode,
            "ends_at": status.ends_at.isoformat() if status.ends_at else None,
        }

    result["control"] = {
        "allowed": coordinator.allow_control,
        "button_mode": coordinator.override_mode,
        "button_minutes": coordinator.override_minutes,
        "button_power_kw": coordinator.override_power_kw,
        "last_command": coordinator.last_command,
    }
    gateway = data.gateway if data else None
    result["gateway"] = {
        "detected": coordinator.has_gateway,
        "readings": sorted(gateway.values) if gateway else None,
        "texts": sorted(gateway.texts) if gateway else None,
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

    peak = coordinator.peak_shaving
    export = coordinator.battery_export
    current = coordinator.grid_current_limit
    grid = coordinator.grid_connection
    result["settings"] = {
        "available_modes": sorted(coordinator.available_modes),
        "operating_mode": coordinator.operating_mode,
        "battery_export": None if export is None else {
            "allowed": export.allowed, "allowed_in_effect": export.allowed_in_effect,
            "installer": export.installer,
        },
        "peak_shaving": None if peak is None else {
            "enabled": peak.enabled, "time_slots": len(peak.slots),
            "single_limit_kw": peak.single_limit,
        },
        "grid_connection": None if grid is None else {
            "state": grid.state, "offered": grid.offered,
            "status": grid.status, "manual": grid.manual,
        },
        "grid_current_limit": None if current is None else {
            "in_effect": current.enabled, "effective_a": current.effective,
            "owner_a": current.owner, "installer_a": current.installer,
        },
        "grid_disconnect_allowed": coordinator.allow_grid_disconnect,
        "sigen_ai": None if coordinator.ai_settings is None else {
            "vacation_mode": coordinator.ai_settings.vacation,
            "vacation_mode_shown": coordinator.ai_settings.vacation_shown,
            "strategy": coordinator.ai_settings.strategy,
        },
        "last_setting": coordinator.last_setting,
    }
    watch = coordinator.storm_watch

    def when(value: Any) -> str | None:
        return value.isoformat() if value is not None else None

    result["storm_watch"] = {
        "shown_for_station": coordinator.session.storm_watch_shown,
        "enabled": None if watch is None else watch.enabled,
        "charged_full": None if watch is None else watch.charged_full,
        "alerts": None if watch is None else [
            {
                "event_type": alert.event_type,
                "weather": alert.weather,
                "charging_start": when(alert.charging_start),
                "onset": when(alert.onset),
                "end": when(alert.end),
            }
            for alert in watch.alerts
        ],
    }
    # Counts only: the names themselves and the serial numbers stay out.
    memory = coordinator.pv_name_store.memory
    result["pv_string_names"] = {
        "names_per_inverter": sorted(coordinator.pv_name_counts.values()),
        "devices_named_by_addon": None if memory is None else len(memory.applied),
    }
    dc_mode = coordinator.dc_charge_mode
    dc_setting = coordinator.dc_charge_setting
    # A count only; the charger serial numbers stay out.
    result["dc_charger"] = {
        "listed": len(coordinator.session.dc_serials),
        "mode": None if dc_mode is None else dc_mode.mode,
        "mode_fields": None if dc_mode is None else sorted(dc_mode.raw),
        "discharge_floor": None if dc_mode is None else dc_mode.discharge_floor,
        "stop_level": None if dc_setting is None else dc_setting.stop_level,
        "allowed_power_kw": None if dc_setting is None else dc_setting.allowed_power,
        "v2x_enabled": coordinator.v2x_enabled,
        "level_range": coordinator.soc_range,
    }
    # Ports and states only; the names people gave their loads stay out.
    result["smart_loads"] = {
        "device_types_known": len(coordinator.session.device_kinds),
        "loads": [
            {
                "port": load.path,
                "attached_to": load.host,
                "switch_enabled": load.load_id in coordinator.watched_smart_loads,
                "on": None if state is None else state.on,
                "operation_mode": None if state is None else state.operation_mode,
            }
            for load in coordinator.smart_loads
            for state in (coordinator.smart_load_states.get(load.load_id),)
        ],
    }
    ac_mode = coordinator.ac_charge_mode
    result["ac_charger"] = {
        "mode": None if ac_mode is None else ac_mode.mode,
        "mode_fields": None if ac_mode is None else sorted(ac_mode.raw),
        "battery_boost": None if ac_mode is None else ac_mode.boost,
        "boost_cutoff": None if ac_mode is None else ac_mode.boost_cutoff,
        "supported_modes": sorted(coordinator.ac_supported_modes),
        "session_energy_kwh": coordinator.ac_session_energy,
        **await _ac_charger_probe(coordinator),
    }

    # Positions and which readings arrived; the serial numbers stay out.
    reader = coordinator.modules
    result["battery_modules"] = {
        "listed": len(reader.modules),
        "modules": [
            {
                "position": module.position,
                "under_inverter": module.inverter_serial is not None,
                **{
                    source: sorted(reader.data[source].get(module.serial) or {})
                    for source in SOURCES
                },
                # Error type and HTTP status or mySigen code; no values.
                "errors": {
                    source: reader.errors[source][module.serial]
                    for source in SOURCES
                    if module.serial in reader.errors[source]
                },
            }
            for module in reader.modules
        ],
    }
    # Structure of the first module's three answers, every value masked, so
    # the field names can be checked against a real plant.
    if reader.modules and not coordinator.rate_limit_wait:
        serial = reader.modules[0].serial
        shapes: dict[str, Any] = {}
        for name, read in (
            ("info", coordinator.session.async_module_static_raw),
            ("readings", coordinator.session.async_module_live_raw),
            ("pack", coordinator.session.async_module_pack_raw),
        ):
            try:
                shapes[name] = masked_shape(await read(serial))
            except MySigenRateLimitError as err:
                wait = coordinator.note_rate_limit(err)
                shapes[name] = f"rate limited: waiting {wait:.0f} s"
                break
            except MySigenError as err:
                shapes[name] = f"unavailable: {type(err).__name__}: {err}"
        result["battery_module_shape"] = shapes
    return result
