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
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
    async_get_clientsession,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    BatteryExport,
    GridCurrentLimit,
    DcChargeMode,
    DcChargeSetting,
    GridConnection,
    PeakShaving,
    CONTROL_MODES,
    MAX_OVERRIDE_MINUTES,
    MAX_POWER_LIMIT_KW,
    MIN_OVERRIDE_MINUTES,
    GatewayReadings,
    MySigenAuthError,
    MySigenConnectionError,
    MySigenError,
    MySigenPermissionError,
    MySigenRateLimitError,
    MySigenSession,
    OverrideStatus,
    SmartLoad,
    SmartLoadState,
    StormWatch,
    AiSettings,
    normalize_serial,
    single_send_trace_config,
)
from .modules import INFO, ModuleReader
from .pv_names import PvNameStore, async_apply_pv_names, find_pv_string_devices
from .const import (
    CONF_ALLOW_CONTROL,
    CONF_ALLOW_GRID_DISCONNECT,
    CONF_OVERRIDE_MINUTES,
    CONF_OVERRIDE_MODE,
    CONF_OVERRIDE_POWER,
    CONF_PLANT_ENTRY_ID,
    DEFAULT_OVERRIDE_MINUTES,
    DEFAULT_OVERRIDE_MODE,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MODBUS_DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_BACKOFF_START = 30.0
_BACKOFF_MAX = 900.0
# Settings that stay until changed are read this often, and only while
# control is allowed. A change reads them back at once.
_SETTINGS_INTERVAL = 600.0
# How often the station's charger list is read again.
_CHARGER_RECHECK = 3600.0
# A failed charger read is tried once more this soon.
_CHARGER_RETRY = 60.0
# How long "no Gateway" is trusted before mySigen is asked again. Long
# enough not to bother the service, short enough that a Gateway that was
# added, or an answer that was a temporary error, is picked up the same day.
_GATEWAY_RECHECK = 3600.0


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
        # Entity names in the plant's chosen language; None for Automatic.
        self.entity_name_translations: dict[str, str] | None = None
        # The plant's setting those names were loaded for.
        self.entity_name_language: str | None = None
        # Platforms set up for this entry, so unload matches setup.
        self.platforms: list[str] = []
        # One command at a time per connection.
        self.command_lock = asyncio.Lock()
        # How the last start or cancel ended, for the sensor and diagnostics.
        self.last_command: dict[str, str] | None = None
        # Settings that stay until changed (step C); None when not read.
        self.available_modes: frozenset[int] = frozenset()
        self.operating_mode: int | None = None
        self.battery_export: BatteryExport | None = None
        self.peak_shaving: PeakShaving | None = None
        self.grid_current_limit: GridCurrentLimit | None = None
        # Sigen AI vacation mode and strategy preference.
        self.ai_settings: AiSettings | None = None
        self.last_setting: dict[str, str] | None = None
        # Grid connection (step D); only read while disconnecting is allowed.
        self.grid_connection: GridConnection | None = None
        # True from a state between on and off until a settled answer arrives,
        # also through answers that could not be read.
        self._grid_following = False
        # DC charger (step E); read whenever mySigen lists a charger.
        self.dc_charge_mode: DcChargeMode | None = None
        self.dc_charge_setting: DcChargeSetting | None = None
        self.v2x_enabled: bool | None = None
        self.soc_range: tuple[int, int] | None = None
        self._charger_read_at = float("-inf")
        self._charger_retrying = False
        # The charger list is read again this often; a change reloads the entry.
        self._station_reread_at = time.monotonic() + _CHARGER_RECHECK
        self._settings_read_at = float("-inf")
        # Smart loads on SigenStor and Gateway ports; listed at setup while
        # control is allowed, and checked again with the charger list.
        self.smart_loads: tuple[SmartLoad, ...] = ()
        # Load id -> state; a load missing here shows as unavailable.
        self.smart_load_states: dict[int, SmartLoadState] = {}
        # Loads whose switch is enabled in Home Assistant. The states are
        # read at every poll only while there is one.
        self.watched_smart_loads: set[int] = set()
        # Storm Watch; read only where mySigen shows it for the station. None
        # until read and after a failed read.
        self.storm_watch: StormWatch | None = None
        self._storm_read_at = float("-inf")
        # PV string names from the app: what the add-on set is kept here.
        self.pv_name_store = PvNameStore(hass, entry.entry_id, DOMAIN)
        # Inverters whose names were read the last time, with how many names.
        self.pv_name_counts: dict[str, int] = {}
        # Battery modules; set up once the device tree has been read.
        self.modules = ModuleReader(())
        # Serial -> the module's device, registered to the Modbus plant entry.
        self.module_devices: dict[str, dr.DeviceEntry] = {}
        # None until mySigen has answered: True with readings, False without.
        # While False the Gateway is asked once an hour, not on every poll.
        self.has_gateway: bool | None = None
        self._gateway_recheck_at = 0.0
        # Gateway sensors from an earlier start prove there is a Gateway; an
        # answer without readings then never switches the reads off.
        self.gateway_seen_before = any(
            entity.unique_id.startswith(f"{entry.entry_id}_gateway_grid_")
            for entity in er.async_entries_for_config_entry(
                er.async_get(hass), entry.entry_id
            )
        )
        # Counts override states published by commands, so a poll that was
        # already under way can tell its own reading is the older one.
        self._published = 0
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

    @property
    def allow_grid_disconnect(self) -> bool:
        """True only when control and grid disconnecting are both switched on."""
        return self.allow_control and (
            self.config_entry.options.get(CONF_ALLOW_GRID_DISCONNECT) is True
        )

    @property
    def override_minutes(self) -> int:
        """The duration the Start button uses, kept inside the allowed range."""
        try:
            minutes = int(self.config_entry.options.get(
                CONF_OVERRIDE_MINUTES, DEFAULT_OVERRIDE_MINUTES
            ))
        except (TypeError, ValueError):
            minutes = DEFAULT_OVERRIDE_MINUTES
        return min(MAX_OVERRIDE_MINUTES, max(MIN_OVERRIDE_MINUTES, minutes))

    @property
    def override_power_kw(self) -> float | None:
        """The power limit the Start button uses; None means no limit."""
        try:
            power = float(self.config_entry.options.get(CONF_OVERRIDE_POWER, 0))
        except (TypeError, ValueError):
            return None
        return power if 0 < power <= MAX_POWER_LIMIT_KW else None

    @property
    def override_mode(self) -> str:
        """The override the Start button starts."""
        mode = self.config_entry.options.get(CONF_OVERRIDE_MODE)
        return mode if mode in CONTROL_MODES else DEFAULT_OVERRIDE_MODE

    def set_option(self, key: str, value: Any) -> None:
        """Store a local setting. Nothing is sent and nothing is reloaded."""
        self.hass.config_entries.async_update_entry(
            self.config_entry, options={**self.config_entry.options, key: value}
        )

    def record_setting(self, action: str, result: str) -> None:
        """Remember how the last setting change ended, for diagnostics."""
        self.last_setting = {
            "action": action,
            "result": result,
            "at": dt_util.utcnow().isoformat(),
        }

    def read_settings_soon(self) -> None:
        """Read the settings again at the next poll."""
        self._settings_read_at = float("-inf")
        self._charger_read_at = float("-inf")

    def publish_operating_mode(self, code: int) -> None:
        self.operating_mode = code
        self.async_update_listeners()

    def publish_battery_export(self, export: BatteryExport) -> None:
        self.battery_export = export
        self.async_update_listeners()

    def publish_peak_shaving(self, schedule: PeakShaving) -> None:
        self.peak_shaving = schedule
        self.async_update_listeners()

    def publish_grid_current_limit(self, limit: GridCurrentLimit) -> None:
        self.grid_current_limit = limit
        self.async_update_listeners()

    @property
    def has_dc_charger(self) -> bool:
        return self.session.dc_serial is not None

    def publish_dc_charge_mode(self, value: DcChargeMode) -> None:
        self.dc_charge_mode = value
        self.async_update_listeners()

    def publish_dc_charge_setting(self, value: DcChargeSetting) -> None:
        self.dc_charge_setting = value
        self.async_update_listeners()

    def publish_v2x_enabled(self, value: bool) -> None:
        self.v2x_enabled = value
        self.async_update_listeners()

    def publish_ai_settings(self, value: AiSettings) -> None:
        self.ai_settings = value
        self.async_update_listeners()

    async def _async_read_storm_watch(self) -> None:
        """Read Storm Watch. A failing read clears it and is retried soon."""
        async with self.command_lock:
            if self.rate_limit_wait:
                return
            now = time.monotonic()
            self._storm_read_at = now
            try:
                self.storm_watch = await self.session.async_storm_watch()
            except (MySigenConnectionError, MySigenPermissionError) as err:
                # An old "no alert" must not stay on show.
                _LOGGER.debug("mySigen Storm Watch read failed: %s", err)
                self.storm_watch = None
                self._storm_read_at = now - _SETTINGS_INTERVAL + _CHARGER_RETRY

    async def async_update_pv_names(self) -> None:
        """Name the plant's PV string devices as in the app, where allowed.

        Sign-in and rate-limit errors are raised; an inverter whose names
        cannot be read is left as it is.
        """
        registry = dr.async_get(self.hass)
        devices = find_pv_string_devices(
            registry, self.config_entry.data[CONF_PLANT_ENTRY_ID], MODBUS_DOMAIN,
            normalize_serial,
        )
        names: dict[str, dict[int, str]] = {}
        for serial in sorted({device.serial for device in devices}):
            try:
                found = await self.session.async_pv_names(serial)
            except (MySigenConnectionError, MySigenPermissionError) as err:
                _LOGGER.debug("mySigen PV string name read failed: %s", err)
                continue
            if found is not None:
                names[serial] = found
        self.pv_name_counts = {serial: len(found) for serial, found in names.items()}
        if names:
            await async_apply_pv_names(self.hass, self.pv_name_store, devices, names)

    def publish_smart_load(self, load_id: int, state: SmartLoadState) -> None:
        self.smart_load_states = {**self.smart_load_states, load_id: state}
        self.async_update_listeners()

    async def _async_read_smart_loads(self) -> None:
        """Read the smart loads' states. A failing read clears them."""
        async with self.command_lock:
            if self.rate_limit_wait:
                return
            try:
                self.smart_load_states = await self.session.async_smart_load_states()
            except (MySigenConnectionError, MySigenPermissionError) as err:
                # mySigen no longer confirms the old states: show them as
                # unavailable rather than as current.
                _LOGGER.debug("mySigen smart load read failed: %s", err)
                self.smart_load_states = {}

    async def _async_read_charger(self) -> None:
        """Read the DC charger's settings. A failing read clears its value."""
        async with self.command_lock:
            if self.rate_limit_wait:
                return
            now = time.monotonic()
            self._charger_read_at = now
            reads = [
                ("dc_charge_mode", self.session.async_dc_charge_mode),
                ("dc_charge_setting", self.session.async_dc_charge_setting),
                ("v2x_enabled", self.session.async_v2x_enabled),
            ]
            if self.soc_range is None:
                reads.append(("soc_range", self.session.async_soc_range))
            failed = False
            for name, read in reads:
                try:
                    setattr(self, name, await read())
                except (MySigenConnectionError, MySigenPermissionError) as err:
                    # mySigen no longer confirms the old value: show it as
                    # unavailable rather than as current.
                    _LOGGER.debug("mySigen %s read failed: %s", name, err)
                    setattr(self, name, None)
                    failed = True
            if failed and not self._charger_retrying:
                # One early retry, a minute later; then the normal interval.
                self._charger_read_at = now - _SETTINGS_INTERVAL + _CHARGER_RETRY
            self._charger_retrying = failed and not self._charger_retrying

    async def _async_recheck_chargers(self) -> None:
        """Read the station's charger list again; reload when it changed.

        Holds the command lock, so a running command finishes against the
        charger it read.
        """
        async with self.command_lock:
            if self.rate_limit_wait:
                return
            self._station_reread_at = time.monotonic() + _CHARGER_RECHECK
            before = self.session.dc_serial
            storm_before = self.session.storm_watch_shown
            try:
                same_station = await self.session.async_read_station()
            except (MySigenConnectionError, MySigenPermissionError) as err:
                _LOGGER.debug("mySigen station read failed: %s", err)
                return
            if not same_station:
                # Not checked against the plant: block every change at once,
                # and let the reload check the new station from the start.
                self.link_problem = "station_changed"
                _LOGGER.warning("The mySigen account opens a different station now; reloading")
                self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
                return
            if self.session.dc_serial != before:
                _LOGGER.info("mySigen lists a different DC charger now; reloading")
                self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
                return
            if self.session.storm_watch_shown != storm_before:
                _LOGGER.info("mySigen shows Storm Watch differently now; reloading")
                self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
                return
            # Names set in the app since the last check.
            await self.async_update_pv_names()
            try:
                modules = await self.session.async_read_battery_modules()
            except (MySigenConnectionError, MySigenPermissionError) as err:
                _LOGGER.debug("mySigen device tree read failed: %s", err)
                return
            # An empty list is more likely a passing fault than every module
            # gone, so only a list with modules replaces the current one. A
            # module that moved keeps its serial but changes its position or
            # inverter, which its device shows, so whole records are compared.
            if modules and set(modules) != set(self.modules.modules):
                _LOGGER.info("mySigen lists different battery modules now; reloading")
                self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
                return
            if not self.allow_control:
                return
            try:
                loads = await self.session.async_smart_loads()
            except (MySigenConnectionError, MySigenPermissionError) as err:
                _LOGGER.debug("mySigen smart load list read failed: %s", err)
                return
            # The list comes with the generator and other devices, so an
            # answer that lists no smart load is still an answer.
            if set(loads) != set(self.smart_loads):
                _LOGGER.info("mySigen lists different smart loads now; reloading")
                self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)

    async def _async_read_module(self) -> None:
        """Read the battery module that is due, and name its device."""
        serial = await self.modules.async_read_next(self.session, time.monotonic())
        static = self.modules.data[INFO].get(serial) if serial else None
        if not static:
            return
        registry = dr.async_get(self.hass)
        # By the ID setup registered: identifiers are unique only per entry.
        placed = self.module_devices.get(serial)
        device = registry.async_get(placed.id) if placed is not None else None
        model, firmware = static.get("model"), static.get("firmware")
        if device is not None and (
            (model and device.model != model) or (firmware and device.sw_version != firmware)
        ):
            registry.async_update_device(
                device.id,
                model=model or device.model,
                sw_version=firmware or device.sw_version,
            )

    def publish_grid_connection(self, connection: GridConnection) -> None:
        self.grid_connection = connection
        self._grid_following = not connection.settled
        self.async_update_listeners()

    @property
    def _grid_changing(self) -> bool:
        """True while the plant is on its way off or back onto the grid."""
        return self.allow_grid_disconnect and self._grid_following

    async def _async_read_grid(self) -> None:
        """Read only the grid connection, while it is changing."""
        async with self.command_lock:
            if self.rate_limit_wait:
                return
            await self._async_read_grid_locked()

    async def _async_read_grid_locked(self) -> None:
        try:
            connection = await self.session.async_grid_connection()
        except (MySigenConnectionError, MySigenPermissionError) as err:
            _LOGGER.debug("mySigen grid connection read failed: %s", err)
            return
        self.grid_connection = connection
        if connection is not None:
            self._grid_following = not connection.settled

    async def _async_read_settings(self) -> None:
        """Read the settings step C shows. One failing read keeps its old value.

        Holds the command lock, so a read cannot overlap a change and then
        overwrite the value the change confirmed.
        """
        async with self.command_lock:
            # A change that held the lock may have run into a rate limit.
            # Skip this read; it is tried again at the next poll.
            if self.rate_limit_wait:
                return
            await self._async_read_settings_locked()

    async def _async_read_settings_locked(self) -> None:
        self._settings_read_at = time.monotonic()
        if not self.available_modes:
            try:
                self.available_modes = await self.session.async_available_modes()
            except (MySigenConnectionError, MySigenPermissionError) as err:
                _LOGGER.debug("mySigen mode list read failed: %s", err)
        for name, read in (
            ("operating_mode", self.session.async_operating_mode),
            ("battery_export", self.session.async_battery_export),
            ("peak_shaving", self.session.async_peak_shaving),
            ("grid_current_limit", self.session.async_grid_current_limit),
            ("ai_settings", self.session.async_ai_settings),
        ):
            try:
                setattr(self, name, await read())
            except (MySigenConnectionError, MySigenPermissionError) as err:
                _LOGGER.debug("mySigen %s read failed: %s", name, err)
        if self.allow_grid_disconnect:
            await self._async_read_grid_locked()

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
        self._published += 1
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
        if self.has_gateway is False and time.monotonic() < self._gateway_recheck_at:
            return None
        try:
            gateway = await self.session.async_gateway_readings()
        except MySigenPermissionError as err:
            _LOGGER.debug("mySigen Gateway read not allowed: %s", err)
            self._no_gateway_answer()
            return None
        except MySigenConnectionError as err:
            _LOGGER.debug("mySigen Gateway read failed: %s", err)
            if err.status is not None:
                # mySigen answered with an error. That may mean no Gateway or
                # a temporary fault, so it is asked again in an hour.
                self._no_gateway_answer()
            # Without an answer nothing is known yet; the next poll asks again.
            return None
        if gateway is not None:
            self.has_gateway = True
        else:
            self._no_gateway_answer()
        return gateway

    def _no_gateway_answer(self) -> None:
        """mySigen answered without Gateway readings: ask again in an hour."""
        if self.has_gateway is not True and not self.gateway_seen_before:
            self.has_gateway = False
            self._gateway_recheck_at = time.monotonic() + _GATEWAY_RECHECK

    async def _async_update_data(self) -> MySigenData:
        # Stay quiet while the service has asked for fewer requests.
        if self.rate_limit_wait:
            raise UpdateFailed("Waiting after a mySigen rate limit")
        published = self._published
        try:
            status = await self.session.async_override_status()
            gateway = await self._async_gateway()
            if (
                self.allow_control
                and time.monotonic() - self._settings_read_at >= _SETTINGS_INTERVAL
            ):
                await self._async_read_settings()
            elif self._grid_changing:
                await self._async_read_grid()
            if (
                self.has_dc_charger
                and time.monotonic() - self._charger_read_at >= _SETTINGS_INTERVAL
            ):
                await self._async_read_charger()
            if (
                self.session.storm_watch_shown
                and time.monotonic() - self._storm_read_at >= _SETTINGS_INTERVAL
            ):
                await self._async_read_storm_watch()
            if self.allow_control and self.smart_loads and self.watched_smart_loads:
                await self._async_read_smart_loads()
            if self.modules.modules:
                await self._async_read_module()
            if time.monotonic() >= self._station_reread_at:
                await self._async_recheck_chargers()
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
        if self._published != published and self.data is not None:
            # A command read and published a newer state while this poll was
            # under way. Keep that one; this reading predates it.
            status = self.data.override
        self._limits["streak"] = 0
        self.permission_denied = False
        self.last_success = dt_util.utcnow()
        return MySigenData(status, gateway)
