"""Polling of mySigen for one linked plant."""
from __future__ import annotations

from datetime import timedelta
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    MySigenAuthError,
    MySigenError,
    MySigenRateLimitError,
    MySigenSession,
    OverrideStatus,
)
from .const import (
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


class MySigenCoordinator(DataUpdateCoordinator[OverrideStatus]):
    """Read the battery override status at the configured interval."""

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
        )
        self._blocked_until = 0.0
        self._rate_limit_streak = 0

    async def _async_update_data(self) -> OverrideStatus:
        # Stay quiet while the service has asked for fewer requests.
        if time.monotonic() < self._blocked_until:
            raise UpdateFailed("Waiting after a mySigen rate limit")
        try:
            status = await self.session.async_override_status()
        except MySigenAuthError as err:
            raise ConfigEntryAuthFailed("mySigen refused the stored account") from err
        except MySigenRateLimitError as err:
            self._rate_limit_streak += 1
            wait = err.retry_after or min(
                _BACKOFF_START * 2 ** (self._rate_limit_streak - 1), _BACKOFF_MAX
            )
            self._blocked_until = time.monotonic() + wait
            raise UpdateFailed(f"mySigen rate limit, waiting {wait:.0f} s") from err
        except MySigenError as err:
            raise UpdateFailed(f"mySigen read failed: {err}") from err
        self._rate_limit_streak = 0
        return status
