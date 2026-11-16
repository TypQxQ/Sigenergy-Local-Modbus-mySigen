"""Polling of mySigen for one linked plant."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
    async_get_clientsession,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    GatewayReadings,
    MySigenAuthError,
    MySigenConnectionError,
    MySigenError,
    MySigenPermissionError,
    MySigenRateLimitError,
    MySigenSession,
    OverrideStatus,
    single_send_trace_config,
)
from .const import (
    CONF_ALLOW_CONTROL,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

_BACKOFF_START = 30.0
_BACKOFF_MAX = 900.0
# How long an answer without Gateway readings is trusted before asking again.
_GATEWAY_RECHECK = 600.0


def _write_session(hass: HomeAssistant) -> Any:
    """Create the session used for changes; it never repeats a request.

    One per coordinator. Home Assistant closes it when the config entry is
    unloaded, so it must not be kept and reused across reloads.
    """
    session = async_create_clientsession(
        hass, trace_configs=[single_send_trace_config()]
    )
    # Second safeguard for the same thing, where aiohttp offers it.
    if hasattr(session, "_retry_connection"):
        session._retry_connection = False  # noqa: SLF001
    return session


@dataclass(frozen=True)
class MySigenData:
    """One poll. `gateway` is None when it gave no readings this time."""

    override: OverrideStatus
    gateway: GatewayReadings | None


class MySigenCoordinator(DataUpdateCoordinator[MySigenData]):
    """Read the battery override and the Gateway at the configured interval."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        interval = min(MAX_SCAN_INTERVAL, max(MIN_SCAN_INTERVAL, int(interval)))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(seconds=interval),
        )
        self.session = MySigenSession(
            async_get_clientsession(hass),
            entry.data[CONF_REGION],
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            write_session=_write_session(hass),
        )
        self.last_success: datetime | None = None
        # One command at a time per connection.
        self.command_lock = asyncio.Lock()
        # How the last start or cancel ended, for the sensor and diagnostics.
        self.last_command: dict[str, str] | None = None
        # None until mySigen has answered: True with readings, False without.
        self.has_gateway: bool | None = None
        self._gateway_recheck_at = 0.0
        # True when mySigen refused the read itself, not the sign-in.
        self.permission_denied = False
        # Result of the startup station check, for diagnostics.
        self.link_problem: str | None = None
        # Kept outside the coordinator: a failed first refresh discards the
        # coordinator, and the next setup attempt must still honour the wait.
        self._limits: dict[str, float] = hass.data.setdefault(DOMAIN, {}).setdefault(
            entry.entry_id, {"blocked_until": 0.0, "streak": 0}
        )

    @property
    def link_verified(self) -> bool:
        """True when startup matched the account's station to the plant."""
        return self.link_problem is None

    @property
    def allow_control(self) -> bool:
        """True only when the user switched control on for this connection."""
        return self.config_entry.options.get(CONF_ALLOW_CONTROL) is True

    def record_command(self, action: str, result: str) -> None:
        """Remember how a command ended and tell the entities."""
        self.last_command = {
            "action": action,
            "result": result,
            "at": dt_util.utcnow().isoformat(),
        }
        self.async_update_listeners()

    def publish_override(self, status: OverrideStatus) -> None:
        """Show a freshly read override state without waiting for a poll."""
        gateway = self.data.gateway if self.data else None
        self.last_success = dt_util.utcnow()
        self.async_set_updated_data(MySigenData(status, gateway))

    @property
    def rate_limit_wait(self) -> float:
        """Seconds left of the pause mySigen asked for; 0 when none."""
        return max(0.0, self._limits["blocked_until"] - time.monotonic())

    def note_rate_limit(self, err: MySigenRateLimitError) -> float:
        """Record a rate limit and return how long to stay quiet."""
        self._limits["streak"] += 1
        wait = err.retry_after or min(
            _BACKOFF_START * 2 ** (self._limits["streak"] - 1), _BACKOFF_MAX
        )
        self._limits["blocked_until"] = time.monotonic() + wait
        return wait

    async def _async_gateway(self) -> GatewayReadings | None:
        """Read the Gateway without letting it fail the whole poll."""
        now = time.monotonic()
        if self.has_gateway is False and now < self._gateway_recheck_at:
            return None
        try:
            gateway = await self.session.async_gateway_readings()
        except (MySigenConnectionError, MySigenPermissionError) as err:
            # Unknown stays unknown: a failed read says nothing about
            # whether the system has a Gateway.
            _LOGGER.debug("mySigen Gateway read failed: %s", err)
            return None
        if gateway is not None:
            self.has_gateway = True
        elif self.has_gateway is not True:
            # No readings this time. Ask again later instead of deciding for
            # good: the Gateway may be added or come online.
            self.has_gateway = False
            self._gateway_recheck_at = now + _GATEWAY_RECHECK
        return gateway

    async def _async_update_data(self) -> MySigenData:
        # Stay quiet while the service has asked for fewer requests.
        if self.rate_limit_wait:
            raise UpdateFailed("Waiting after a mySigen rate limit")
        try:
            status = await self.session.async_override_status()
            gateway = await self._async_gateway()
        except MySigenAuthError as err:
            raise ConfigEntryAuthFailed(
                f"mySigen refused the stored account ({err})"
            ) from err
        except MySigenPermissionError as err:
            self.permission_denied = True
            raise UpdateFailed(f"mySigen did not allow this read ({err})") from err
        except MySigenRateLimitError as err:
            wait = self.note_rate_limit(err)
            raise UpdateFailed(f"mySigen rate limit, waiting {wait:.0f} s") from err
        except MySigenError as err:
            raise UpdateFailed(f"mySigen read failed: {err}") from err
        self._limits["streak"] = 0
        self.permission_denied = False
        self.last_success = dt_util.utcnow()
        return MySigenData(status, gateway)
