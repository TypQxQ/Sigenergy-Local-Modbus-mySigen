"""When each battery module is read.

At most one module is read per poll, so a plant with many modules does not
send a burst of requests. Its readings are kept fresh for a few minutes and
its model and rated capacity for a day. Nothing here imports Home Assistant.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

from .api import BatteryModule, MySigenConnectionError, MySigenPermissionError

_LOGGER = logging.getLogger(__name__)

# How old a module's readings may get before it is read again.
MODULE_INTERVAL = 300.0
# Model, firmware and rated capacity change rarely.
MODULE_STATIC_INTERVAL = 86400.0
# A failed read is tried once more this soon, then at the normal interval.
MODULE_RETRY = 60.0


def module_identifier(entry_id: str, serial: str) -> str:
    """The device registry identifier of one module's device."""
    return f"{entry_id}_battery_{serial}"


class _Session(Protocol):
    async def async_module_static(self, serial: str) -> dict[str, Any]: ...
    async def async_module_live(self, serial: str) -> dict[str, Any]: ...


class ModuleReader:
    """Readings of the station's battery modules, read one module at a time."""

    def __init__(self, modules: tuple[BatteryModule, ...]) -> None:
        self.modules = modules
        # Serial -> readings. None after a failed read: shown as unavailable.
        self.static: dict[str, dict[str, Any] | None] = {}
        self.live: dict[str, dict[str, Any] | None] = {}
        self._static_due = {module.serial: float("-inf") for module in modules}
        self._live_due = {module.serial: float("-inf") for module in modules}
        self._retrying: set[str] = set()

    def module(self, serial: str) -> BatteryModule | None:
        return next((module for module in self.modules if module.serial == serial), None)

    def _next(self, now: float) -> BatteryModule | None:
        """The module waiting longest for a read, or None when none is due."""
        due = [
            (min(self._static_due[m.serial], self._live_due[m.serial]), index, m)
            for index, m in enumerate(self.modules)
            if min(self._static_due[m.serial], self._live_due[m.serial]) <= now
        ]
        return min(due)[2] if due else None

    async def async_read_next(self, session: _Session, now: float) -> str | None:
        """Read the module that is due, if any; return its serial.

        A refused or failed read marks only that module's readings as
        unavailable. Rate limits and sign-in errors are raised, so the poll
        pauses as it does for every other read.
        """
        module = self._next(now)
        if module is None:
            return None
        serial = module.serial
        failed = False
        if self._static_due[serial] <= now:
            try:
                self.static[serial] = await session.async_module_static(serial)
                self._static_due[serial] = now + MODULE_STATIC_INTERVAL
            except (MySigenConnectionError, MySigenPermissionError) as err:
                _LOGGER.debug("mySigen battery module read failed: %s", err)
                # Keep an earlier answer; the model does not change.
                self.static.setdefault(serial, None)
                self._static_due[serial] = now + MODULE_INTERVAL
        if self._live_due[serial] <= now:
            try:
                self.live[serial] = await session.async_module_live(serial)
            except (MySigenConnectionError, MySigenPermissionError) as err:
                _LOGGER.debug("mySigen battery module readings failed: %s", err)
                # mySigen no longer confirms the old values.
                self.live[serial] = None
                failed = True
            if failed and serial not in self._retrying:
                self._retrying.add(serial)
                self._live_due[serial] = now + MODULE_RETRY
            else:
                self._retrying.discard(serial)
                self._live_due[serial] = now + MODULE_INTERVAL
        return serial

    def value(self, serial: str, key: str) -> Any:
        """One reading, from either read; None when unknown."""
        for source in (self.live, self.static):
            values = source.get(serial)
            if values and key in values:
                return values[key]
        return None

    def available(self, serial: str, static: bool) -> bool:
        """False before the first answer and after a failed read."""
        return (self.static if static else self.live).get(serial) is not None
