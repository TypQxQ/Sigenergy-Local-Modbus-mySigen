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
    CONTROL_MODES,
    MAX_PEAK_SHAVING_KW,
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
) -> str:
    """Change a setting that stays: read, send once, read back.

    Nothing is sent when the fresh read already shows the requested value.
    `before` may refuse after the read, with nothing awaited before the send.
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
        for attempt in range(2):
            if attempt:
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
        if latest is None:
            raise CommandUnknown(
                "mySigen accepted the change, but the setting could not be read back."
            )
        publish(latest)
        raise CommandNotConfirmed("mySigen accepted the change but does not show it.")

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
    enabled: bool,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Allow or block exporting from the battery. It stays until changed back."""
    return await _change_setting(
        coordinator,
        "battery_export",
        read=coordinator.session.async_battery_export,
        send=lambda current: coordinator.session.async_set_battery_export(current, enabled),
        expected=lambda current: current.owner is enabled,
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
        if limit_kw is not None and len(current.slots) != 1:
            raise CommandRefused(
                "The peak shaving schedule has more than one time slot. "
                "Change the limits in the mySigen app."
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
