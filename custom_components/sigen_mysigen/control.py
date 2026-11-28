"""Start and cancel the temporary battery override.

This is the only code path that changes the plant. Its rules:

* Nothing is sent unless control was switched on for the connection.
* Before a start, a fresh read must state that no override is running and
  the Modbus integration must show Remote EMS off, checked last, right
  before sending. Either one is left alone.
* A request is sent once. It is never repeated, whatever the answer.
* After an accepted request, a fresh read must show the expected state.
* When the answer is lost, the outcome is reported as unknown.

The module does not import Home Assistant, so the rules can be tested alone.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from .api import (
    AC_CHARGE_MODES,
    AI_STRATEGIES,
    DEFAULT_FLOOR_RANGE,
    DEFAULT_LEVEL_RANGE,
    CONTROL_MODES,
    DC_CHARGE_MODES,
    GRID_GOING_OFF,
    GRID_OFF,
    GRID_ON,
    GRID_RETURNING,
    MAX_PEAK_SHAVING_KW,
    OPERATING_MODE_NAMES,
    OPERATING_MODES,
    MAX_OVERRIDE_MINUTES,
    MAX_POWER_LIMIT_KW,
    MIN_OVERRIDE_MINUTES,
    MySigenError,
    MySigenRateLimitError,
    MySigenUnknownOutcome,
    OverrideStatus,
)

RESULT_CONFIRMED = "confirmed"
RESULT_REFUSED = "refused"
RESULT_UNKNOWN = "unknown"
RESULT_NOT_CONFIRMED = "not_confirmed"
RESULT_NO_OVERRIDE = "no_override"
RESULTS = (
    RESULT_CONFIRMED,
    RESULT_REFUSED,
    RESULT_UNKNOWN,
    RESULT_NOT_CONFIRMED,
    RESULT_NO_OVERRIDE,
)

REMOTE_EMS_ON = "on"
REMOTE_EMS_OFF = "off"

# The Modbus integration's grid connection states.
PLANT_ON_GRID = "on_grid"
PLANT_OFF_GRID_MANUAL = "off_grid_manual"

_READBACK_DELAY = 3.0


class CommandError(Exception):
    """A command did not end in a confirmed change."""

    result = RESULT_REFUSED


class CommandRefused(CommandError):
    """Nothing was changed."""


class CommandUnknown(CommandError):
    """The request may or may not have reached the plant."""

    result = RESULT_UNKNOWN


class CommandNotConfirmed(CommandError):
    """mySigen accepted the request but does not show the expected state."""

    result = RESULT_NOT_CONFIRMED


def _check_ready(coordinator: Any) -> None:
    if not coordinator.allow_control:
        raise CommandRefused(
            "Control is switched off for this mySigen connection. "
            "Switch it on under Reconfigure."
        )
    if not coordinator.link_verified:
        # Reading may continue unverified; changing a plant may not. The
        # account's station could be a different system than the plant
        # whose Remote EMS state is checked.
        raise CommandRefused(
            "The mySigen system could not be checked against the plant when "
            "this connection started. Reload the connection and try again."
        )
    if wait := coordinator.rate_limit_wait:
        raise CommandRefused(f"mySigen asked for a pause; try again in {wait:.0f} s.")


async def _fresh_status(coordinator: Any) -> OverrideStatus:
    """Read the current override before sending anything."""
    try:
        status = await coordinator.session.async_override_status()
    except MySigenRateLimitError as err:
        coordinator.note_rate_limit(err)
        raise CommandRefused("mySigen is limiting requests. Nothing was sent.") from err
    except MySigenError as err:
        raise CommandRefused(
            f"Could not read the current state ({err}). Nothing was sent."
        ) from err
    if not status.explicit:
        # An answer without a clear on or off is not proof of off.
        raise CommandRefused(
            "mySigen did not state whether an override is running. "
            "Nothing was sent."
        )
    return status


async def _send(coordinator: Any, request: Callable[[], Awaitable[None]]) -> None:
    """Send one change, exactly once, and sort the failure by what it means."""
    try:
        await request()
    except MySigenUnknownOutcome as err:
        raise CommandUnknown(
            f"The answer was lost ({err}). The request was not sent again; "
            "the override sensors will show what the plant did."
        ) from err
    except MySigenRateLimitError as err:
        coordinator.note_rate_limit(err)
        raise CommandRefused("mySigen is limiting requests and refused.") from err
    except MySigenError as err:
        raise CommandRefused(f"mySigen refused the request ({err}).") from err


async def _confirm(
    coordinator: Any,
    expected: Callable[[OverrideStatus], bool],
    sleep: Callable[[float], Awaitable[None]],
) -> OverrideStatus:
    """Read back until the expected state shows; two reads at most."""
    status: OverrideStatus | None = None
    for attempt in range(2):
        if attempt:
            await sleep(_READBACK_DELAY)
        try:
            status = await coordinator.session.async_override_status()
        except MySigenRateLimitError as err:
            coordinator.note_rate_limit(err)
            break
        except MySigenError:
            continue
        if expected(status):
            return status
    if status is None:
        raise CommandUnknown(
            "mySigen accepted the request, but the state could not be read "
            "back. The override sensors will show the result."
        )
    coordinator.publish_override(status)
    raise CommandNotConfirmed(
        "mySigen accepted the request but does not show the expected state."
    )


async def _run(coordinator: Any, action: str, command: Awaitable[str]) -> str:
    """Record how a command ended, for the result sensor and diagnostics."""
    try:
        result = await command
    except CommandError as err:
        coordinator.record_command(action, err.result)
        raise
    coordinator.record_command(action, result)
    return result


async def async_start_override(
    coordinator: Any,
    *,
    mode: str,
    minutes: int,
    remote_ems: Callable[[], str | None],
    power_kw: float | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Start a timed override. Raises CommandError unless it is confirmed.

    `power_kw` caps a charge or discharge override; None means no limit.
    """

    async def start() -> str:
        if mode not in CONTROL_MODES:
            raise CommandRefused(f"Mode {mode!r} is not available for control.")
        if not MIN_OVERRIDE_MINUTES <= minutes <= MAX_OVERRIDE_MINUTES:
            raise CommandRefused(
                f"Duration must be {MIN_OVERRIDE_MINUTES} to "
                f"{MAX_OVERRIDE_MINUTES} minutes."
            )
        if power_kw is not None and not 0 < power_kw <= MAX_POWER_LIMIT_KW:
            raise CommandRefused(
                f"Power limit must be above 0 and at most {MAX_POWER_LIMIT_KW:g} kW."
            )
        _check_ready(coordinator)
        current = await _fresh_status(coordinator)
        if current.enabled:
            coordinator.publish_override(current)
            raise CommandRefused(
                f"An override is already running ({current.mode}). "
                "It was left in place; cancel it first."
            )
        # Read last, inside the lock and with nothing awaited before the
        # send, so a start that waited in line cannot use an old answer.
        remote_ems_now = remote_ems()
        if remote_ems_now == REMOTE_EMS_ON:
            raise CommandRefused(
                "Remote EMS is active on the plant. It was left in place."
            )
        if remote_ems_now != REMOTE_EMS_OFF:
            raise CommandRefused(
                "Could not confirm that Remote EMS is off. Nothing was sent."
            )
        await _send(
            coordinator,
            lambda: coordinator.session.async_start_override(mode, minutes, power_kw),
        )
        status = await _confirm(
            coordinator,
            lambda s: s.explicit and s.enabled and s.mode == mode,
            sleep,
        )
        coordinator.publish_override(status)
        return RESULT_CONFIRMED

    async with coordinator.command_lock:
        return await _run(coordinator, "start", start())


async def async_cancel_override(
    coordinator: Any,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """End the running override, whoever started it."""

    async def cancel() -> str:
        _check_ready(coordinator)
        current = await _fresh_status(coordinator)
        if not current.enabled:
            coordinator.publish_override(current)
            return RESULT_NO_OVERRIDE
        await _send(coordinator, coordinator.session.async_cancel_override)
        status = await _confirm(
            coordinator, lambda s: s.explicit and not s.enabled, sleep
        )
        coordinator.publish_override(status)
        return RESULT_CONFIRMED

    async with coordinator.command_lock:
        return await _run(coordinator, "cancel", cancel())


async def _change_setting(
    coordinator: Any,
    action: str,
    *,
    read: Callable[[], Awaitable[Any]],
    send: Callable[[Any], Awaitable[None]],
    expected: Callable[[Any], bool],
    publish: Callable[[Any], None],
    sleep: Callable[[float], Awaitable[None]],
    before: Callable[[Any], None] | None = None,
    settle: bool = False,
    readbacks: int = 2,
) -> str:
    """Change a setting that stays: read, send once, read back.

    Nothing is sent when the fresh read already shows the requested value.
    `before` may refuse after the read, with nothing awaited before the send.
    `settle` waits before the first read-back too, for a value the plant
    reports with a short delay, and `readbacks` is how many reads at most.
    When the change is not confirmed, the settings are read again at the
    next poll, so a late change still shows within one update.
    """

    async def change() -> str:
        _check_ready(coordinator)
        try:
            current = await read()
        except MySigenRateLimitError as err:
            coordinator.note_rate_limit(err)
            raise CommandRefused("mySigen is limiting requests. Nothing was sent.") from err
        except MySigenError as err:
            raise CommandRefused(
                f"Could not read the current setting ({err}). Nothing was sent."
            ) from err
        if current is None:
            raise CommandRefused(
                "mySigen did not report the current setting. Nothing was sent."
            )
        if expected(current):
            publish(current)
            return RESULT_CONFIRMED
        if before is not None:
            before(current)
        await _send(coordinator, lambda: send(current))
        latest = None
        for attempt in range(readbacks):
            if attempt or settle:
                await sleep(_READBACK_DELAY)
            try:
                latest = await read()
            except MySigenRateLimitError as err:
                coordinator.note_rate_limit(err)
                break
            except MySigenError:
                continue
            if latest is not None and expected(latest):
                publish(latest)
                return RESULT_CONFIRMED
        read_soon = getattr(coordinator, "read_settings_soon", None)
        if read_soon is not None:
            read_soon()
        if latest is None:
            raise CommandUnknown(
                "mySigen accepted the change, but the setting could not be read back."
            )
        publish(latest)
        raise CommandNotConfirmed(
            "mySigen accepted the change but does not show it yet. It is read "
            "again at the next update."
        )

    async with coordinator.command_lock:
        try:
            result = await change()
        except CommandError as err:
            coordinator.record_setting(action, err.result)
            raise
        coordinator.record_setting(action, result)
        return result


async def async_set_operating_mode(
    coordinator: Any,
    *,
    mode: str,
    remote_ems: Callable[[], str | None],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Choose the operating mode. It stays until changed back."""
    code = OPERATING_MODES.get(mode)
    if code is None and mode in OPERATING_MODE_NAMES:
        raise CommandRefused(
            f"The operating mode {mode!r} can't be chosen from Home Assistant. Remote "
            "EMS is switched in Sigenergy ESS; the other modes are set in the mySigen app."
        )
    if code is None or code not in coordinator.available_modes:
        # Unknown until the plant has listed its modes.
        raise CommandRefused(f"Operating mode {mode!r} is not available on this plant.")

    def before(_current: Any) -> None:
        state = remote_ems()
        if state == REMOTE_EMS_ON:
            raise CommandRefused(
                "Remote EMS is active on the plant. The operating mode was left unchanged."
            )
        if state != REMOTE_EMS_OFF:
            raise CommandRefused(
                "Could not confirm that Remote EMS is off. Nothing was sent."
            )

    return await _change_setting(
        coordinator,
        "operating_mode",
        read=coordinator.session.async_operating_mode,
        send=lambda _current: coordinator.session.async_set_operating_mode(code),
        expected=lambda current: current == code,
        publish=coordinator.publish_operating_mode,
        sleep=sleep,
        before=before,
    )


async def async_set_battery_export(
    coordinator: Any,
    *,
    allowed: bool,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Allow or block exporting from the battery. It stays until changed back."""
    return await _change_setting(
        coordinator,
        "battery_export",
        read=coordinator.session.async_battery_export,
        send=lambda current: coordinator.session.async_set_battery_export(current, allowed),
        expected=lambda current: current.allowed is allowed,
        publish=coordinator.publish_battery_export,
        sleep=sleep,
    )


async def async_set_peak_shaving(
    coordinator: Any,
    *,
    enabled: bool | None = None,
    limit_kw: float | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Switch peak shaving on or off, or change its limit. It stays until changed."""
    if limit_kw is not None and not 0 < limit_kw <= MAX_PEAK_SHAVING_KW:
        raise CommandRefused(
            f"Peak shaving limit must be above 0 and at most {MAX_PEAK_SHAVING_KW:g} kW."
        )

    def before(current: Any) -> None:
        if enabled and not current.slots:
            raise CommandRefused(
                "Peak shaving has no time slot. Set one up in the mySigen app first."
            )
        if limit_kw is not None and not current.slots:
            raise CommandRefused(
                "The peak shaving schedule has no time slot. Set one up in the "
                "mySigen app first. Nothing was sent."
            )
        if limit_kw is not None and len(current.slots) != 1:
            raise CommandRefused(
                "The peak shaving schedule has more than one time slot. "
                "Change the limits in the mySigen app. Nothing was sent."
            )

    def expected(current: Any) -> bool:
        if enabled is not None and current.enabled is not enabled:
            return False
        if limit_kw is not None and (
            current.single_limit is None or round(current.single_limit, 1) != round(limit_kw, 1)
        ):
            return False
        return True

    def schedule(current: Any) -> dict[str, Any]:
        raw = current.with_enabled(enabled) if enabled is not None else dict(current.raw)
        if limit_kw is not None:
            raw = type(current)(raw).with_limit(limit_kw)
        return raw

    return await _change_setting(
        coordinator,
        "peak_shaving" if limit_kw is None else "peak_shaving_limit",
        read=coordinator.session.async_peak_shaving,
        send=lambda current: coordinator.session.async_set_peak_shaving(schedule(current)),
        expected=expected,
        publish=coordinator.publish_peak_shaving,
        sleep=sleep,
        before=before,
    )


async def async_set_grid_current_limit(
    coordinator: Any,
    *,
    amps: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Change the max grid connection current. It stays until changed back."""
    amps = round(float(amps), 1)
    if amps <= 0:
        raise CommandRefused("The max grid connection current must be above 0 A.")

    def before(current: Any) -> None:
        if current.installer is not None and amps > current.installer:
            raise CommandRefused(
                f"The installer allows at most {current.installer:g} A. Nothing was sent."
            )

    return await _change_setting(
        coordinator,
        "grid_current_limit",
        read=coordinator.session.async_grid_current_limit,
        send=lambda _current: coordinator.session.async_set_grid_current_limit(amps),
        expected=lambda current: current.enabled is not False
        and current.effective is not None
        and round(current.effective, 1) == amps,
        publish=coordinator.publish_grid_current_limit,
        sleep=sleep,
        before=before,
    )


async def async_set_off_grid(
    coordinator: Any,
    *,
    off_grid: bool,
    plant_grid: Callable[[], str | None],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Disconnect the plant from the grid, or reconnect it.

    Sent only when mySigen offers the switch, its fresh read shows the plant
    settled in the other state, and the Modbus integration agrees. Reconnecting
    is only done after a manual disconnect, never during a grid outage.
    """
    if not coordinator.allow_grid_disconnect:
        raise CommandRefused(
            "Disconnecting from the grid is switched off for this mySigen "
            "connection. Switch it on under Reconfigure."
        )
    wanted = (GRID_GOING_OFF, GRID_OFF) if off_grid else (GRID_ON, GRID_RETURNING)

    def before(current: Any) -> None:
        if not current.offered:
            raise CommandRefused(
                "mySigen does not offer the grid switch for this plant right now. "
                "Nothing was sent."
            )
        if off_grid and current.state != GRID_ON:
            raise CommandRefused(
                f"The plant is not settled on the grid ({current.state}). Nothing was sent."
            )
        if not off_grid and not current.manual_off_grid:
            raise CommandRefused(
                "The plant is not off the grid by a manual disconnect. It may be a "
                "grid outage, which reconnects by itself. Nothing was sent."
            )
        # Read last, with nothing awaited before the send.
        plant = plant_grid()
        needed = PLANT_ON_GRID if off_grid else PLANT_OFF_GRID_MANUAL
        if plant != needed:
            raise CommandRefused(
                f"The Modbus integration shows the grid connection as {plant!r}, "
                f"not {needed!r}. Nothing was sent."
            )

    return await _change_setting(
        coordinator,
        "disconnect_grid" if off_grid else "reconnect_grid",
        read=coordinator.session.async_grid_connection,
        send=lambda _current: coordinator.session.async_set_off_grid(off_grid),
        expected=lambda current: current.state in wanted,
        publish=coordinator.publish_grid_connection,
        sleep=sleep,
        before=before,
    )


def _check_charger(coordinator: Any) -> None:
    if not coordinator.has_dc_charger:
        raise CommandRefused("mySigen lists no DC charger for this plant.")


def _check_level(level: int, allowed: tuple[int, int]) -> None:
    low, high = allowed
    if not low <= level <= high:
        raise CommandRefused(f"The vehicle charge level must be {low} to {high} %.")


def dc_stop_range(coordinator: Any) -> tuple[int, int]:
    """The levels the charge limit may take; 0 to 100 % when not reported."""
    return coordinator.soc_range or DEFAULT_LEVEL_RANGE


def dc_floor_range(coordinator: Any) -> tuple[int, int]:
    """The levels the V2X floor may take, from the charger's last answer."""
    mode = coordinator.dc_charge_mode
    return mode.floor_range if mode is not None else DEFAULT_FLOOR_RANGE


def boost_cutoff_range(coordinator: Any) -> tuple[int, int]:
    """The levels a battery boost cut-off may take; 0 to 100 % when not reported.

    The mySigen app limits both chargers' cut-off to the station's
    `device/charge/mode/soc/range`, not to limits in the mode answer.
    """
    return coordinator.soc_range or DEFAULT_LEVEL_RANGE


async def async_set_dc_charge_mode(
    coordinator: Any,
    *,
    mode: str,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Choose how the DC charger charges. It stays until changed back."""
    _check_charger(coordinator)
    if mode not in DC_CHARGE_MODES:
        raise CommandRefused(f"Charging mode {mode!r} is not available.")
    return await _change_setting(
        coordinator,
        "dc_charge_mode",
        read=coordinator.session.async_dc_charge_mode,
        send=lambda current: coordinator.session.async_set_dc_charge_mode(current.body(mode=mode)),
        expected=lambda current: current.mode == mode,
        publish=coordinator.publish_dc_charge_mode,
        sleep=sleep,
        # A charger confirms a change later than the plant.
        settle=True,
        readbacks=3,
    )


async def async_set_dc_discharge_floor(
    coordinator: Any,
    *,
    level: int,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Set the charge level the car keeps when it supplies power."""
    _check_charger(coordinator)
    return await _change_setting(
        coordinator,
        "dc_discharge_floor",
        read=coordinator.session.async_dc_charge_mode,
        send=lambda current: coordinator.session.async_set_dc_charge_mode(
            current.body(discharge_floor=level)
        ),
        expected=lambda current: current.discharge_floor == level,
        publish=coordinator.publish_dc_charge_mode,
        sleep=sleep,
        # The charger's own limits, from the answer just read.
        before=lambda current: _check_level(level, current.floor_range),
        # A charger confirms a change later than the plant.
        settle=True,
        readbacks=3,
    )


def _check_boost_reported(current: Any) -> None:
    if current.boost is None:
        raise CommandRefused(
            "mySigen did not report whether battery boost is on. Nothing was sent."
        )


def _check_cutoff(level: int, coordinator: Any) -> None:
    low, high = boost_cutoff_range(coordinator)
    if not low <= level <= high:
        raise CommandRefused(f"The battery boost cut-off must be {low} to {high} %.")


async def async_set_dc_battery_boost(
    coordinator: Any,
    *,
    enabled: bool,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Switch DC battery boost on or off: the home battery helps charge the car."""
    _check_charger(coordinator)
    return await _change_setting(
        coordinator,
        "dc_battery_boost",
        read=coordinator.session.async_dc_charge_mode,
        send=lambda current: coordinator.session.async_set_dc_charge_mode(
            current.body(boost=enabled)
        ),
        expected=lambda current: current.boost is enabled,
        publish=coordinator.publish_dc_charge_mode,
        sleep=sleep,
        before=_check_boost_reported,
        # A charger confirms a change later than the plant.
        settle=True,
        readbacks=3,
    )


async def async_set_dc_boost_cutoff(
    coordinator: Any,
    *,
    level: int,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Set the home battery level at which DC battery boost stops."""
    _check_charger(coordinator)
    _check_cutoff(level, coordinator)
    return await _change_setting(
        coordinator,
        "dc_boost_cutoff",
        read=coordinator.session.async_dc_charge_mode,
        send=lambda current: coordinator.session.async_set_dc_charge_mode(
            current.body(boost_cutoff=level)
        ),
        expected=lambda current: current.boost_cutoff == level,
        publish=coordinator.publish_dc_charge_mode,
        sleep=sleep,
        # A charger confirms a change later than the plant.
        settle=True,
        readbacks=3,
    )


async def async_set_dc_stop_level(
    coordinator: Any,
    *,
    level: int,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Set the vehicle charge level at which charging stops."""
    _check_charger(coordinator)
    _check_level(level, dc_stop_range(coordinator))
    return await _change_setting(
        coordinator,
        "dc_stop_level",
        read=coordinator.session.async_dc_charge_setting,
        send=lambda current: coordinator.session.async_set_dc_stop_level(current, level),
        expected=lambda current: current.stop_level == level,
        publish=coordinator.publish_dc_charge_setting,
        sleep=sleep,
        # A charger confirms a change later than the plant.
        settle=True,
        readbacks=3,
    )


async def async_set_v2x_enabled(
    coordinator: Any,
    *,
    enabled: bool,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Allow or block the car supplying power to the home."""
    _check_charger(coordinator)
    return await _change_setting(
        coordinator,
        "v2x_enabled",
        read=coordinator.session.async_v2x_enabled,
        send=lambda _current: coordinator.session.async_set_v2x_enabled(enabled),
        expected=lambda current: current is enabled,
        publish=coordinator.publish_v2x_enabled,
        sleep=sleep,
        # A charger confirms a change later than the plant.
        settle=True,
        readbacks=3,
    )


def _check_ac_charger(coordinator: Any) -> None:
    if not coordinator.has_ac_charger:
        raise CommandRefused("mySigen lists no AC charger for this plant.")


async def _change_ac(
    coordinator: Any,
    action: str,
    *,
    expected: Callable[[Any], bool],
    sleep: Callable[[float], Awaitable[None]],
    mode: str | None = None,
    boost: bool | None = None,
    boost_cutoff: int | None = None,
    before: Callable[[Any], None] | None = None,
) -> str:
    """Change one AC charger setting, sending the others as read.

    Charging mode and battery boost exist only in mySigen: Remote EMS and
    Modbus only start, stop and limit the charger, so Remote EMS is not
    checked, as for the DC charger.
    """
    _check_ac_charger(coordinator)
    return await _change_setting(
        coordinator,
        action,
        read=coordinator.session.async_ac_charge_mode,
        send=lambda current: coordinator.session.async_set_ac_charge_mode(
            current.body(mode=mode, boost=boost, boost_cutoff=boost_cutoff)
        ),
        expected=expected,
        publish=coordinator.publish_ac_charge_mode,
        sleep=sleep,
        before=before,
        # A charger confirms a change later than the plant.
        settle=True,
        readbacks=3,
    )


async def async_set_ac_charge_mode(
    coordinator: Any,
    *,
    mode: str,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Choose how the AC charger charges. It stays until changed back."""
    _check_ac_charger(coordinator)
    code = AC_CHARGE_MODES.get(mode)
    offered = coordinator.ac_supported_modes
    if code is None or (offered and code not in offered):
        raise CommandRefused(f"Charging mode {mode!r} is not available on this charger.")
    return await _change_ac(
        coordinator, "ac_charge_mode",
        mode=mode, expected=lambda current: current.mode == mode, sleep=sleep,
    )


async def async_set_ac_battery_boost(
    coordinator: Any,
    *,
    enabled: bool,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Switch battery boost on or off: the home battery helps charge the car."""
    return await _change_ac(
        coordinator, "ac_battery_boost",
        boost=enabled, expected=lambda current: current.boost is enabled,
        sleep=sleep, before=_check_boost_reported,
    )


async def async_set_ac_boost_cutoff(
    coordinator: Any,
    *,
    level: int,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Set the home battery level at which battery boost stops."""
    _check_ac_charger(coordinator)
    _check_cutoff(level, coordinator)
    return await _change_ac(
        coordinator, "ac_boost_cutoff",
        boost_cutoff=level, expected=lambda current: current.boost_cutoff == level,
        sleep=sleep,
    )


def _sigen_ai_settings_read(coordinator: Any, what: str) -> Callable[[], Awaitable[Any]]:
    """Read the Sigen AI settings, refusing unless the plant runs in Sigen AI.

    The operating mode is read fresh once, before the settings; read-backs
    skip it. A plant in any other mode, Remote EMS included, is refused,
    also when the setting already shows the requested value.
    """
    checked = False

    async def read() -> Any:
        nonlocal checked
        if not checked:
            mode = await coordinator.session.async_operating_mode()
            if mode is not None:
                coordinator.publish_operating_mode(mode)
            if mode != OPERATING_MODES["sigen_ai"]:
                raise CommandRefused(
                    f"{what} is only available in Sigen AI mode"
                    + ("" if mode is None else f" (the plant runs in mode {mode})")
                    + ". Nothing was sent."
                )
            checked = True
        return await coordinator.session.async_ai_settings()

    return read


async def async_set_vacation_mode(
    coordinator: Any,
    *,
    enabled: bool,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Switch Sigen AI's vacation mode on or off. It stays until changed back.

    Only in Sigen AI mode. Neither the operating mode nor the strategy is
    changed.
    """

    def before(current: Any) -> None:
        if not current.vacation_shown:
            raise CommandRefused(
                "mySigen does not offer vacation mode for this plant. Nothing was sent."
            )
        if current.vacation is None:
            raise CommandRefused(
                "mySigen did not report whether vacation mode is on. Nothing was sent."
            )

    return await _change_setting(
        coordinator,
        "vacation_mode",
        read=_sigen_ai_settings_read(coordinator, "Vacation mode"),
        send=lambda _current: coordinator.session.async_set_vacation_mode(enabled),
        expected=lambda current: current.vacation_shown and current.vacation is enabled,
        publish=coordinator.publish_ai_settings,
        sleep=sleep,
        before=before,
    )


async def async_set_ai_strategy(
    coordinator: Any,
    *,
    strategy: str,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Choose Sigen AI's strategy preference. It stays until changed back.

    Only in Sigen AI mode. Neither the operating mode nor vacation mode is
    changed.
    """
    code = AI_STRATEGIES.get(strategy)
    if code is None:
        raise CommandRefused(f"Strategy {strategy!r} is not available.")

    def before(current: Any) -> None:
        if current.strategy is None:
            raise CommandRefused(
                "mySigen did not report the current strategy. Nothing was sent."
            )

    return await _change_setting(
        coordinator,
        "ai_strategy",
        read=_sigen_ai_settings_read(coordinator, "The strategy preference"),
        send=lambda _current: coordinator.session.async_set_ai_strategy(code),
        expected=lambda current: current.strategy == code,
        publish=coordinator.publish_ai_settings,
        sleep=sleep,
        before=before,
    )


async def async_set_smart_load(
    coordinator: Any,
    *,
    load_id: int,
    on: bool,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Switch a smart load on a SigenStor or Gateway port on or off.

    Remote EMS does not switch smart loads and the app does not block it, so
    it is not checked. The load must be under manual control in mySigen,
    where the app shows its switch.
    """
    load = next((load for load in coordinator.smart_loads if load.load_id == load_id), None)
    if load is None:
        raise CommandRefused("mySigen no longer lists this smart load. Nothing was sent.")

    async def read() -> Any:
        return (await coordinator.session.async_smart_load_states()).get(load_id)

    def before(current: Any) -> None:
        # Also reached when the load already shows the requested state but is
        # not under manual control: that is refused, not confirmed.
        if not current.manual:
            raise CommandRefused(
                "mySigen is not controlling this smart load by hand right now "
                f"(operation mode {current.operation_mode}). Set it to manual in "
                "the mySigen app. Nothing was sent."
            )
        if current.on is None:
            raise CommandRefused(
                "mySigen did not report whether the smart load is on. Nothing was sent."
            )

    return await _change_setting(
        coordinator,
        "smart_load",
        read=read,
        send=lambda _current: coordinator.session.async_set_smart_load(load.path, on),
        # Only counts under manual control, also when it is already set.
        expected=lambda current: current.manual and current.on is on,
        publish=lambda current: coordinator.publish_smart_load(load_id, current),
        sleep=sleep,
        before=before,
        # The state follows a switch within a few seconds, not at once.
        settle=True,
    )
