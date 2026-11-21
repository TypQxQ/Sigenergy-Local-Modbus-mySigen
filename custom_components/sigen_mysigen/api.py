"""Small mySigen client.

Request formats follow solidfox/sigenergy-cloud (MIT License,
https://github.com/solidfox/sigenergy-cloud). It signs in and reads. The
only requests that change a plant start and cancel the temporary battery
override, and they are sent exactly once (see control.py for the rules).

Never log request bodies, response bodies, credentials or tokens.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import re
import time
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

REGIONS: dict[str, str] = {
    "eu": "https://api-eu.sigencloud.com/",
    "aus": "https://api-aus.sigencloud.com/",
    "apac": "https://api-apac.sigencloud.com/",
    "us": "https://api-us.sigencloud.com/",
    "cn": "https://api-cn.sigencloud.com/",
}

OVERRIDE_MODES: dict[str, str] = {
    "0": "charging",
    "1": "discharging",
    "2": "hold_battery",
    "3": "self_consumption",
}

# Modes that may be started from Home Assistant. Others are added only
# after each has been verified on hardware.
CONTROL_MODES: tuple[str, ...] = (
    "hold_battery", "charging", "discharging", "self_consumption",
)
# Operating modes that may be chosen from Home Assistant, by the code mySigen
# uses. Remote EMS (7) belongs to the Modbus integration and custom profiles
# (9) are left to the app.
OPERATING_MODES: dict[str, int] = {
    "sigen_ai": 1,
    "max_self_consumption": 0,
    "time_of_use": 2,
    "fully_fed_to_grid": 5,
}
MAX_PEAK_SHAVING_KW = 100.0
# Modes where a power limit applies.
POWER_MODES: tuple[str, ...] = ("charging", "discharging")
MAX_POWER_LIMIT_KW = 100.0
# What the app sends for "no limit" in the power modes.
_UNLIMITED_POWER = "4294967.295"
# The range the reference client enforces; wider ranges are unverified.
# The mySigen app accepts a custom override of 1 to 1440 minutes (24 hours).
MIN_OVERRIDE_MINUTES = 1
MAX_OVERRIDE_MINUTES = 1440

_TOKEN_PATH = "auth/oauth/token"
_STATION_PATH = "device/owner/station/home"
# The misspelling is the service's own path.
_OVERRIDE_PATH = "device/energy-profile/instant/manunal/{station_id}"
_TOPOLOGY_PATH = "device/devicetreepanel/topology"
_AIO_PATH = "device/aio/by/station"
# One battery module each. Request formats follow sigenergy2mqtt
# (MIT License, https://github.com/seud0nym/sigenergy2mqtt).
_MODULE_STATIC_PATH = "device/sigen/device/static/info"
_MODULE_LIVE_PATH = "device/sigen/device/dynamic/info"
_MODULE_PACK_PATH = "device/device/pack/queryPackMergeInfo"
# mySigen answers the readings read with code 1000 unless the request names
# the app package that sends it; any name is accepted. The add-on names
# itself rather than posing as a Sigenergy app.
_PACKAGE_HEADER = {"sg-pkg": "sigen_mysigen"}
_GATEWAY_PATH = "device/gateway/{station_id}"
_OVERRIDE_WRITE_PATH = "device/energy-profile/instant/manunal"
_MODES_PATH = "device/energy-profile/mode/all/{station_id}"
_MODE_PATH = "device/energy-profile/mode/current/{station_id}"
_MODE_WRITE_PATH = "device/energy-profile/mode"
_EXPORT_PATH = "device/energy-profile/battery/export/limitation/{station_id}"
_EXPORT_WRITE_PATH = "device/energy-profile/battery/export/limitation"
_PEAK_PATH = "device/dischargesetting/peak/shaving/{station_id}"
_PEAK_WRITE_PATH = "device/dischargesetting/peak/shaving"
# The app's Grid Settings > Max Grid Connection Current. Amperes per phase.
_GRID_CURRENT_PATH = "device/energy-profile/parallel/off/grid/{station_id}"
_GRID_CURRENT_WRITE_PATH = "device/energy-profile/parallel/off/grid"
_GRID_STATUS_PATH = "device/gateway/gateway-status"
_GRID_WRITE_PATH = "device/gateway/ongrid-state/update"
# DC charger (step E). Taken from the solidfox clients; not yet read on a
# system with a charger.
_DC_MODE_PATH = "device/charge/mode/dc"
_DC_SETTING_PATH = "device/dcevse/charge/setting"
_DC_V2X_PATH = "device/station-v2x/select"
_DC_V2X_WRITE_PATH = "device/station-v2x/open-close"
_SOC_RANGE_PATH = "device/charge/mode/soc/range"
_LABEL_KEYS = ("paramKey", "key")
_NUMBER_WITH_UNIT = re.compile(r"(-?\d+(?:\.\d+)?)\s*([A-Za-z]*)")
# Rows of the Gateway answer that become readings. Labels are compared in
# lower case; the value must carry the unit given here.
_PHASES = ("a", "b", "c")
# gridSideInfoList: one row per value.
_GRID_FIELDS: dict[str, tuple[str, str]] = {
    **{f"phase {p} voltage": (f"voltage_{p}", "V") for p in _PHASES},
    **{f"phase {p} current": (f"current_{p}", "A") for p in _PHASES},
    **{
        f"maximum phase {p} voltage in the past minute": (f"voltage_max_{p}", "V")
        for p in _PHASES
    },
    **{
        f"minimum phase {p} voltage in the past minute": (f"voltage_min_{p}", "V")
        for p in _PHASES
    },
}
# generatorRealTimeInfoVOList: one row per quantity, one column per phase.
_GENERATOR_PHASE_ROWS: dict[str, tuple[str, str]] = {
    "voltage": ("generator_voltage", "V"),
    "current": ("generator_current", "A"),
    "active power": ("generator_power", "kW"),
}
# generatorSideInfoList: one row per value.
_GENERATOR_FIELDS: dict[str, tuple[str, str]] = {
    "active power": ("generator_power", "kW"),
    "voltage frequency": ("generator_frequency", "Hz"),
}
_GENERATOR_CONTACTOR = "generator side contactor status"
_PHASE_SEQUENCE = "voltage phase sequence"
_MEASUREMENT = re.compile(r"-?[\d.,]+\s*[A-Za-z%°/]{0,6}")
_CLIENT_ID = "sigen"
_CLIENT_SECRET = "sigen"
_PASSWORD_KEY = b"sigensigensigenp"
_REQUEST_TIMEOUT = 20
_TOKEN_MARGIN = 60
_SUCCESS_CODES = (0, "0", None)
_RATE_LIMIT_CODES = (429, "429")


class MySigenError(Exception):
    """Base error for mySigen requests."""


class MySigenAuthError(MySigenError):
    """The service answered and refused these credentials."""


class MySigenPermissionError(MySigenError):
    """The account is signed in but may not make this request."""


class MySigenRejectedError(MySigenError):
    """The service answered a change with an error; nothing was changed."""


class MySigenUnknownOutcome(MySigenError):
    """A change was sent and no usable answer came back."""


class _ResendBlocked(Exception):
    """Raised inside aiohttp when it is about to send a change again."""


def single_send_trace_config() -> aiohttp.TraceConfig:
    """Return a hook that lets a request use one connection attempt only.

    aiohttp repeats PUT requests once by itself when the connection drops,
    because PUT counts as safe to repeat. A timed override is not: a repeat
    could restart its duration. Every attempt first takes a connection, so
    refusing the second one stops the repeat before anything is sent.
    """

    async def on_connection(_session: Any, context: Any, _params: Any) -> None:
        counter = context.trace_request_ctx
        if isinstance(counter, dict):
            counter["attempts"] = counter.get("attempts", 0) + 1
            if counter["attempts"] > 1:
                raise _ResendBlocked

    config = aiohttp.TraceConfig()
    config.on_connection_create_start.append(on_connection)
    config.on_connection_reuseconn.append(on_connection)
    return config


class MySigenConnectionError(MySigenError):
    """The service could not be reached or gave an unusable answer."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class MySigenRateLimitError(MySigenError):
    """The service asked for fewer requests."""

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


@dataclass(frozen=True)
class OverrideStatus:
    """The temporary battery override (Instant Manual Control)."""

    enabled: bool
    mode: str | None
    ends_at: datetime | None
    # False when mySigen did not state on or off in a recognisable way.
    # Such an answer is shown as off but is not proof that nothing runs.
    explicit: bool = True


@dataclass(frozen=True)
class GatewayReadings:
    """What the Gateway reports. A missing key means no reading."""

    values: dict[str, float]
    texts: dict[str, str]


def parse_measurement(text: Any, unit: str) -> float | None:
    """Read "231.40 V" as 231.4. Anything unexpected is no reading."""
    if not isinstance(text, str):
        return None
    match = _NUMBER_WITH_UNIT.fullmatch(text.strip())
    # A different unit (kV, mA) is refused instead of guessed at.
    if match is None or match.group(2).lower() != unit.lower():
        return None
    return float(match.group(1))


def _rows(data: dict[str, Any], name: str, label_key: str) -> list[tuple[str, dict]]:
    """Return (lower-case label, row) for the well-formed rows of one list."""
    rows = data.get(name)
    if not isinstance(rows, list):
        return []
    return [
        (str(row.get(label_key) or "").strip().lower(), row)
        for row in rows
        if isinstance(row, dict)
    ]


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def parse_gateway(data: Any) -> GatewayReadings | None:
    """Pick the readings the add-on uses out of the Gateway answer.

    Returns None when the answer has no grid-side voltage or current, which
    is how a system without a Gateway shows.
    """
    if not isinstance(data, dict):
        return None
    values: dict[str, float] = {}
    texts: dict[str, str] = {}

    def take(row_value: Any, key: str, unit: str) -> None:
        reading = parse_measurement(row_value, unit)
        if reading is not None:
            values[key] = reading

    for label, row in _rows(data, "gridSideInfoList", "paramKey"):
        if label in _GRID_FIELDS:
            take(row.get("paramValue"), *_GRID_FIELDS[label])
    if not values:
        return None

    for label, row in _rows(data, "newGridSideInfoList", "paramKey"):
        if label == _PHASE_SEQUENCE and (text := _text(row.get("paramValue"))):
            texts["phase_sequence"] = text
    for label, row in _rows(data, "generatorRealTimeInfoVOList", "key"):
        if label in _GENERATOR_PHASE_ROWS:
            key, unit = _GENERATOR_PHASE_ROWS[label]
            for phase in _PHASES:
                take(row.get(f"phase{phase.upper()}"), f"{key}_{phase}", unit)
    for label, row in _rows(data, "generatorSideInfoList", "paramKey"):
        if label in _GENERATOR_FIELDS:
            take(row.get("paramValue"), *_GENERATOR_FIELDS[label])
        elif label == _GENERATOR_CONTACTOR and (text := _text(row.get("paramValue"))):
            texts["generator_contactor"] = text.lower().replace(" ", "_")
    if firmware := _text(data.get("softVersion")):
        texts["firmware"] = firmware
    if model := _text(data.get("deviceModel")):
        texts["model"] = model
    return GatewayReadings(values, texts)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_available_modes(data: Any) -> frozenset[int]:
    rows = data.get("defaultWorkingModes") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return frozenset()
    return frozenset(
        code for row in rows
        if isinstance(row, dict) and (code := _int_or_none(row.get("value"))) is not None
    )


def parse_operating_mode(data: Any) -> int | None:
    return _int_or_none(data.get("currentMode")) if isinstance(data, dict) else None


@dataclass(frozen=True)
class BatteryExport:
    """Whether the battery may export to the grid."""

    owner: bool | None
    effective: bool | None
    # Sent back unchanged with every change.
    installer: Any = None


def parse_battery_export(data: Any) -> BatteryExport | None:
    if not isinstance(data, dict) or not isinstance(data.get("ownerSetEnable"), bool):
        return None
    effective = data.get("currentEnable")
    return BatteryExport(
        owner=data["ownerSetEnable"],
        effective=effective if isinstance(effective, bool) else None,
        installer=data.get("installerSetEnable"),
    )


@dataclass(frozen=True)
class PeakShaving:
    """The peak shaving schedule as mySigen returns it.

    Changes are made to a copy of the whole answer, so fields the add-on does
    not know about are written back unchanged.
    """

    raw: dict[str, Any]

    @property
    def enabled(self) -> bool | None:
        mode = _int_or_none(self.raw.get("controlMode"))
        return None if mode not in (0, 1) else mode == 1

    @property
    def slots(self) -> list[dict[str, Any]]:
        rows = self.raw.get("settingList")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    @property
    def single_limit(self) -> float | None:
        """The limit when the schedule has exactly one time slot."""
        if len(self.slots) != 1:
            return None
        try:
            return float(self.slots[0].get("peakPower"))
        except (TypeError, ValueError):
            return None

    def with_enabled(self, enabled: bool) -> dict[str, Any]:
        return {**self.raw, "controlMode": 1 if enabled else 0}

    def with_limit(self, limit_kw: float) -> dict[str, Any]:
        rows = list(self.raw["settingList"])
        index = next(i for i, row in enumerate(rows) if isinstance(row, dict))
        rows[index] = {**rows[index], "peakPower": round(float(limit_kw), 1)}
        return {**self.raw, "settingList": rows}


# The app's owner value for "no owner limit": follow the installer's.
_NO_OWNER_LIMIT = 2147483.647


_UNREADABLE = object()


def _amperes(value: Any) -> Any:
    """Read "25.0" or 25 as amperes.

    Empty and the app's "no limit" value are None. Anything else that is not
    a positive number is _UNREADABLE, so an odd answer is never taken as
    "no limit".
    """
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return _UNREADABLE
    try:
        amps = float(value)
    except (TypeError, ValueError):
        return _UNREADABLE
    if amps != amps or amps <= 0:
        return _UNREADABLE
    return None if amps >= _NO_OWNER_LIMIT - 1 else amps


@dataclass(frozen=True)
class GridCurrentLimit:
    """The most current the plant may draw from the grid, per phase.

    The installer sets a ceiling; the owner may set a lower limit. The
    effective value is the one in use.
    """

    enabled: bool | None
    effective: float | None
    owner: float | None
    installer: float | None


_GRID_CURRENT_KEYS = (
    "enable", "currentLimitation", "ownerSetLimitation", "installerSetLimitation",
)


def parse_grid_current_limit(data: Any) -> GridCurrentLimit | None:
    """Read a complete answer; anything else is None, so nothing is sent.

    Every field must be there: a missing installer limit must not pass for
    "no ceiling". A plant without a limit answers with empty values and
    enable false; that is a real answer: no limit.
    """
    if not isinstance(data, dict) or not all(key in data for key in _GRID_CURRENT_KEYS):
        return None
    enabled = data["enable"]
    values = [_amperes(data[key]) for key in _GRID_CURRENT_KEYS[1:]]
    if not isinstance(enabled, bool) or _UNREADABLE in values:
        return None
    effective, owner, installer = values
    return GridCurrentLimit(
        enabled=enabled, effective=effective, owner=owner, installer=installer,
    )


def parse_peak_shaving(data: Any) -> PeakShaving | None:
    if not isinstance(data, dict):
        return None
    schedule = PeakShaving(dict(data))
    return schedule if schedule.enabled is not None else None


# DC charger chargeMode codes as solidfox's integration observed them.
DC_CHARGE_MODES = {"fast_charging": 0, "solar_surplus": 1, "bidirectional": 2}
# The levels the mySigen app assumes when mySigen leaves an end out.
DEFAULT_LEVEL_RANGE = (0, 100)
DEFAULT_FLOOR_RANGE = (20, 100)


def _level_range(low: Any, high: Any, default: tuple[int, int]) -> tuple[int, int]:
    """A range of vehicle charge levels; a missing or odd end takes the default."""
    low_value = _int_or_none(low)
    high_value = _int_or_none(high)
    low_value = default[0] if low_value is None else low_value
    high_value = default[1] if high_value is None else high_value
    if not 0 <= low_value < high_value <= 100:
        return default
    return low_value, high_value
# Fields the app sends back with a mode change; written as they were read.
_DC_MODE_FIELDS = (
    "enableFromPack", "cutoffSocFromPack", "allowsDischargePower", "vehicleDischargeCutoffSoc",
)


@dataclass(frozen=True)
class DcChargeMode:
    """The DC charger's mode and the settings that go with it."""

    raw: dict[str, Any]

    @property
    def mode(self) -> str | None:
        code = _int_or_none(self.raw.get("chargeMode"))
        return next((name for name, value in DC_CHARGE_MODES.items() if value == code), None)

    @property
    def discharge_floor(self) -> int | None:
        """The car stops supplying power at this charge level."""
        return _int_or_none(self.raw.get("vehicleDischargeCutoffSoc"))

    @property
    def floor_range(self) -> tuple[int, int]:
        """The levels the V2X floor may take, as the charger reports them.

        A missing end is 20 or 100 %, as the mySigen app reads it.
        """
        return _level_range(
            self.raw.get("minimumDischargeCutoffSoc"),
            self.raw.get("maximumDischargeCutoffSoc"),
            DEFAULT_FLOOR_RANGE,
        )

    def body(self, mode: str | None = None, discharge_floor: int | None = None) -> dict[str, Any]:
        """A change: the mode code plus the read fields, one of them changed."""
        body: dict[str, Any] = {
            key: self.raw[key] for key in _DC_MODE_FIELDS if self.raw.get(key) is not None
        }
        if discharge_floor is not None:
            body["vehicleDischargeCutoffSoc"] = int(discharge_floor)
        body["chargeMode"] = DC_CHARGE_MODES[mode] if mode else self.raw["chargeMode"]
        return body


def parse_dc_charge_mode(data: Any) -> DcChargeMode | None:
    if not isinstance(data, dict) or _int_or_none(data.get("chargeMode")) is None:
        return None
    return DcChargeMode(dict(data))


@dataclass(frozen=True)
class DcChargeSetting:
    """Charging power allowed and the vehicle level charging stops at."""

    allowed_power: float
    stop_level: int


def parse_dc_charge_setting(data: Any) -> DcChargeSetting | None:
    if not isinstance(data, dict):
        return None
    level = _int_or_none(data.get("vehicleChargingCutoffSoc"))
    try:
        power = float(data.get("allowedChargePower"))
    except (TypeError, ValueError):
        return None
    if level is None or isinstance(data.get("allowedChargePower"), bool):
        return None
    return DcChargeSetting(allowed_power=power, stop_level=level)


def parse_v2x_enabled(data: Any) -> bool | None:
    """Whether the car may supply power (V2X)."""
    value = data.get("dischargeEnable") if isinstance(data, dict) else None
    if isinstance(value, bool):
        return value
    return {0: False, 1: True}.get(_int_or_none(value))


def parse_soc_range(data: Any) -> tuple[int, int] | None:
    """The charge levels the charge limit may take.

    A missing end is 0 or 100 %, as the mySigen app reads it; some plants
    leave both out. None only for an answer that is not a range at all.
    """
    if not isinstance(data, dict):
        return None
    return _level_range(data.get("socLowerLimit"), data.get("socUpperLimit"), DEFAULT_LEVEL_RANGE)


GRID_ON = "on_grid"
GRID_GOING_OFF = "going_off_grid"
GRID_OFF = "off_grid"
GRID_RETURNING = "returning_to_grid"
GRID_STATES = (GRID_ON, GRID_GOING_OFF, GRID_OFF, GRID_RETURNING)


@dataclass(frozen=True)
class GridConnection:
    """The Gateway's grid connection as mySigen reports it.

    onOffGridStatus: 0 on the grid, 1 returning to the grid, 2 off the grid.
    manualOffGridStatus: 1 while a manual disconnect is under way, 2 after a
    manual request has completed (it stays 2 after reconnecting).
    """

    status: int
    manual: int
    # mySigen only offers the switch in its app while this is true.
    offered: bool

    @property
    def state(self) -> str:
        if self.status == 0:
            return GRID_GOING_OFF if self.manual == 1 else GRID_ON
        return GRID_RETURNING if self.status == 1 else GRID_OFF

    @property
    def off_grid(self) -> bool:
        """True when off the grid or on the way there."""
        return self.state in (GRID_GOING_OFF, GRID_OFF)

    @property
    def settled(self) -> bool:
        return self.state in (GRID_ON, GRID_OFF)

    @property
    def manual_off_grid(self) -> bool:
        """Off the grid because someone asked for it, not because of an outage."""
        return self.status == 2 and self.manual == 2


def parse_grid_connection(data: Any) -> GridConnection | None:
    if not isinstance(data, dict):
        return None
    status = _int_or_none(data.get("onOffGridStatus"))
    manual = _int_or_none(data.get("manualOffGridStatus"))
    # Without the manual field, settled on the grid and on the way off look
    # the same, so such an answer is no answer.
    if status not in (0, 1, 2) or manual not in (0, 1, 2):
        return None
    return GridConnection(
        status=status,
        manual=manual,
        offered=data.get("showButton") is True,
    )


def encrypt_password(password: str) -> str:
    """Return the password in the form the mySigen token endpoint expects."""
    # Imported here so the module loads without the package in unit tests.
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(password.encode("utf-8")) + padder.finalize()
    encryptor = Cipher(algorithms.AES(_PASSWORD_KEY), modes.CBC(_PASSWORD_KEY)).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode("ascii")


def _retry_after(headers: Any) -> float | None:
    """Read a Retry-After given in seconds; ignore other forms."""
    try:
        value = float(headers.get("Retry-After"))
    except (AttributeError, TypeError, ValueError):
        return None
    return value if value > 0 else None


def masked_shape(value: Any, depth: int = 0, keep: frozenset[str] = frozenset()) -> Any:
    """Describe an answer's structure without its values.

    Field names are kept. Numbers become their type. A short number with a
    unit keeps the unit and has its digits replaced; any other text has its
    letters and digits replaced, so names and serial numbers cannot leak.
    """
    if depth > 8:
        return "…"
    if isinstance(value, dict):
        return {
            # Parameter labels such as "Phase A Voltage" are field names in
            # all but form, so they are kept.
            str(key): f"label:{item}"
            if key in _LABEL_KEYS and isinstance(item, str)
            # Fields named in `keep` hold codes, not personal data.
            else f"value:{item!r}"
            if key in keep and (item is None or isinstance(item, (bool, int, float, str)))
            else masked_shape(item, depth + 1, keep)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [masked_shape(item, depth + 1, keep) for item in value[:40]]
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return type(value).__name__
    text = str(value)
    if len(text) <= 16 and _MEASUREMENT.fullmatch(text):
        return "text:" + re.sub(r"\d", "9", text)
    return "text:" + re.sub(r"\d", "9", re.sub(r"[^\W\d_]", "a", text))[:40]


def normalize_serial(value: Any) -> str | None:
    """Return a serial number in a comparable form, or None when empty."""
    if value in (None, "") or isinstance(value, (dict, list)):
        return None
    text = str(value).strip().upper()
    return text or None


def topology_serials(data: Any) -> set[str]:
    """Collect every device serial number from the station's device tree."""
    found: set[str] = set()
    pending = [data]
    while pending:
        node = pending.pop()
        if isinstance(node, list):
            pending.extend(node)
        elif isinstance(node, dict):
            if serial := normalize_serial(node.get("snCode")):
                found.add(serial)
            pending.append(node.get("nodeList"))
    return found


def aio_serials(data: Any) -> set[str]:
    """Collect serial numbers from the station's inverter list."""
    if isinstance(data, dict):
        data = next(
            (data[key] for key in ("snList", "aioSnList", "list", "data")
             if isinstance(data.get(key), list)),
            [],
        )
    if not isinstance(data, list):
        return set()
    return {serial for item in data if (serial := normalize_serial(item))}


# Device tree node types.
_INVERTER_TYPES = (2, 3)  # All-in-one, inverter
BATTERY_DEVICE_TYPE = 4


@dataclass(frozen=True)
class BatteryModule:
    """One battery module as the device tree lists it."""

    serial: str
    # Where the module sits, as mySigen numbers it; None when not given.
    position: str | None
    # The inverter or all-in-one the module hangs under, when the tree says.
    inverter_serial: str | None


def _device_type(node: dict[str, Any]) -> int | None:
    value = node.get("deviceType")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _node_serial(node: dict[str, Any]) -> str | None:
    return normalize_serial(node.get("snCode")) or normalize_serial(node.get("showSnCode"))


def parse_battery_modules(data: Any) -> tuple[BatteryModule, ...]:
    """List the battery modules in the station's device tree, in tree order."""
    found: list[BatteryModule] = []
    seen: set[str] = set()
    pending: list[tuple[Any, str | None]] = [(data, None)]
    while pending:
        node, inverter = pending.pop(0)
        if isinstance(node, list):
            pending[:0] = [(item, inverter) for item in node]
            continue
        if not isinstance(node, dict):
            continue
        kind = _device_type(node)
        serial = _node_serial(node)
        if kind in _INVERTER_TYPES and serial:
            inverter = serial
        elif kind == BATTERY_DEVICE_TYPE and serial and serial not in seen:
            seen.add(serial)
            position = node.get("batPosition")
            found.append(BatteryModule(
                serial,
                None if position in (None, "") or isinstance(position, (dict, list, bool))
                else str(position).strip() or None,
                inverter,
            ))
        children = node.get("nodeList")
        if children is not None:
            pending.insert(0, (children, inverter))
    return tuple(found)


# Units the module readings are converted to.
_UNIT_FACTORS: dict[str, dict[str, float]] = {
    "kWh": {"wh": 0.001, "kwh": 1.0, "mwh": 1000.0},
    "kW": {"w": 0.001, "kw": 1.0},
    "V": {"mv": 0.001, "v": 1.0},
    "%": {"%": 1.0},
}
_CELSIUS = ("°c", "℃", "c")
_FAHRENHEIT = ("°f", "℉", "f")


def _param_rows(data: Any, list_name: str) -> dict[str, dict[str, Any]]:
    """Return the rows of one parameter list by lower-case label."""
    if not isinstance(data, dict):
        return {}
    return {label: row for label, row in _rows(data, list_name, "paramKey") if label}


def _param_number(row: dict[str, Any], unit: str) -> float | None:
    """Read a row as a number in `unit`. A unit it cannot convert is refused."""
    text = row.get("paramValueText")
    given = row.get("paramValueUnit")
    number: float | None = None
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        number = float(text)
    elif isinstance(text, str):
        try:
            number = float(text.strip().replace(",", "."))
        except ValueError:
            number = None
    if number is None or not isinstance(given, str) or not given.strip():
        # Fall back to the combined form, "2.96 MWh".
        value = row.get("paramValue")
        match = re.fullmatch(
            r"\s*(-?\d+(?:[.,]\d+)?)\s*([^\d\s]*)\s*", value
        ) if isinstance(value, str) else None
        if match is None:
            return None
        number = float(match.group(1).replace(",", "."))
        given = match.group(2)
    given = (given or "").strip().lower()
    if number != number or number in (float("inf"), float("-inf")):
        return None
    if unit == "°C":
        if given in _CELSIUS:
            return number
        if given in _FAHRENHEIT:
            return (number - 32) * 5 / 9
        return None
    factor = _UNIT_FACTORS[unit].get(given)
    return None if factor is None else number * factor


def _param_text(row: dict[str, Any]) -> str | None:
    return _text(row.get("paramValueText")) or _text(row.get("paramValue"))


@dataclass(frozen=True)
class _ModuleField:
    label: str
    unit: str | None  # None for a text


# Reading key -> the row it comes from. Keys are part of the unique IDs.
MODULE_STATIC_FIELDS = {
    "model": _ModuleField("device model", None),
    "firmware": _ModuleField("software version", None),
    "rated_capacity": _ModuleField("rated battery capacity", "kWh"),
}
MODULE_LIVE_FIELDS = {
    "soc": _ModuleField("battery soc", "%"),
    "power": _ModuleField("charging & discharging power", "kW"),
    "pack_voltage": _ModuleField("battery pack voltage", "V"),
    "heating": _ModuleField("heating status", None),
}
MODULE_PACK_FIELDS = {
    "discharged": _ModuleField("total discharge amount", "kWh"),
    "cell_voltage": _ModuleField("average cell voltage", "V"),
    "cell_temperature": _ModuleField("average cell temperature", "°C"),
}


def parse_module_values(
    data: Any, list_name: str, fields: dict[str, _ModuleField]
) -> dict[str, float | str]:
    """Pick the known readings out of one module answer. Missing means unknown."""
    rows = _param_rows(data, list_name)
    values: dict[str, float | str] = {}
    for key, field in fields.items():
        row = rows.get(field.label)
        if row is None:
            continue
        value = _param_text(row) if field.unit is None else _param_number(row, field.unit)
        if value is not None:
            values[key] = value
    return values


def parse_override(data: Any) -> OverrideStatus:
    """Turn the override answer into a status, keeping missing apart from off."""
    if not isinstance(data, dict):
        raise MySigenConnectionError("Unexpected override answer")
    raw = data.get("enable")
    if isinstance(raw, str) and raw.strip().lower() in ("true", "false", "0", "1"):
        raw = raw.strip().lower() in ("true", "1")
    explicit = isinstance(raw, bool) or (isinstance(raw, int) and raw in (0, 1))
    enabled = bool(raw)
    raw_mode = data.get("mode")
    mode = OVERRIDE_MODES.get(str(raw_mode)) if raw_mode not in (None, "") else None
    ends_at = None
    try:
        end = int(data.get("endTime") or 0)
    except (TypeError, ValueError):
        end = 0
    # The unit is undocumented. Accept epoch milliseconds or seconds only.
    if end > 10**11:
        ends_at = datetime.fromtimestamp(end / 1000, tz=timezone.utc)
    elif end > 10**9:
        ends_at = datetime.fromtimestamp(end, tz=timezone.utc)
    if not enabled:
        mode = ends_at = None
    return OverrideStatus(enabled, mode, ends_at, explicit)


async def _async_request_token(
    session: aiohttp.ClientSession, region: str, form: dict[str, str]
) -> dict[str, Any]:
    """Post one token request and return the token fields."""
    if region not in REGIONS:
        raise MySigenConnectionError(f"Unknown region {region!r}")
    payload: Any = None
    try:
        async with session.post(
            f"{REGIONS[region]}{_TOKEN_PATH}",
            data=form,
            auth=aiohttp.BasicAuth(_CLIENT_ID, _CLIENT_SECRET),
            timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT),
        ) as response:
            status = response.status
            retry_after = _retry_after(response.headers)
            try:
                payload = await response.json(content_type=None)
            except (ValueError, aiohttp.ClientError):
                payload = None
    except (aiohttp.ClientError, TimeoutError) as err:
        raise MySigenConnectionError(type(err).__name__) from err

    if status == 429:
        raise MySigenRateLimitError(retry_after)
    if status in (400, 401, 403):
        raise MySigenAuthError(f"HTTP {status}")
    if status != 200 or not isinstance(payload, dict):
        raise MySigenConnectionError(f"HTTP {status}", status)

    data = payload.get("data")
    tokens = data if isinstance(data, dict) else payload
    if tokens.get("access_token"):
        return tokens

    # A well-formed answer without a token, for example code 11003
    # "authentication failed". Log the code only, never the body.
    _LOGGER.debug("mySigen token request refused with code %s", payload.get("code"))
    raise MySigenAuthError(f"code {payload.get('code')}")


async def async_validate_login(
    session: aiohttp.ClientSession, region: str, username: str, password: str
) -> None:
    """Log in once and discard the result.

    Raises MySigenAuthError for refused credentials and
    MySigenConnectionError for everything else that prevents a login.
    """
    await _async_request_token(session, region, {
        "username": username,
        "password": encrypt_password(password),
        "grant_type": "password",
    })


class MySigenSession:
    """One signed-in account. Tokens are kept in memory only."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        region: str,
        username: str,
        password: str,
        write_session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._session = session
        # Changes go through a session built with single_send_trace_config().
        # Without one, nothing that changes the plant is sent.
        self._write_session = write_session
        self._region = region
        self._username = username
        self._password = password
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at = 0.0
        self._station_id: str | None = None
        # DC chargers mySigen lists for the station, read with the station id.
        self.dc_serials: tuple[str, ...] = ()
        # Battery modules from the last device tree read; None until read.
        self.battery_modules: tuple[BatteryModule, ...] | None = None
        self._logged_keys = False

    def _store(self, tokens: dict[str, Any]) -> None:
        self._access_token = str(tokens["access_token"])
        self._refresh_token = str(tokens.get("refresh_token") or "") or None
        try:
            lifetime = int(tokens.get("expires_in") or 0)
        except (TypeError, ValueError):
            lifetime = 0
        self._expires_at = time.monotonic() + max(0, lifetime - _TOKEN_MARGIN)

    async def _async_login(self) -> None:
        self._store(await _async_request_token(self._session, self._region, {
            "username": self._username,
            "password": encrypt_password(self._password),
            "grant_type": "password",
        }))

    async def _async_ensure_token(self) -> None:
        if self._access_token and time.monotonic() < self._expires_at:
            return
        if self._refresh_token:
            try:
                self._store(await _async_request_token(self._session, self._region, {
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                }))
                return
            except MySigenAuthError:
                # The refresh token is spent. Fall back to the password.
                self._refresh_token = None
        await self._async_login()

    async def _async_get_once(
        self,
        path: str,
        params: dict[str, str] | None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        try:
            async with self._session.request(
                "GET",
                f"{REGIONS[self._region]}{path}",
                params=params,
                headers={
                    **(headers or {}),
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT),
            ) as response:
                status = response.status
                retry_after = _retry_after(response.headers)
                try:
                    payload = await response.json(content_type=None)
                except (ValueError, aiohttp.ClientError):
                    payload = None
        except (aiohttp.ClientError, TimeoutError) as err:
            raise MySigenConnectionError(type(err).__name__) from err

        code = payload.get("code") if isinstance(payload, dict) else None
        if status == 429 or code in _RATE_LIMIT_CODES:
            raise MySigenRateLimitError(retry_after)
        detail = f"HTTP {status}, code {code}"
        if status == 401 or code in (401, "401"):
            raise MySigenAuthError(detail)
        if status == 403 or code in (403, "403"):
            raise MySigenPermissionError(detail)
        if status >= 400 or not isinstance(payload, dict):
            raise MySigenConnectionError(f"HTTP {status}", status)
        if code not in _SUCCESS_CODES:
            raise MySigenConnectionError(f"code {code}", status)
        return payload.get("data")

    async def _async_get(
        self,
        what: str,
        path: str,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """GET one path, signing in again once if the session was dropped.

        `what` names the request in refusals, so a message says which read
        the account was refused.
        """
        try:
            await self._async_ensure_token()
            try:
                return await self._async_get_once(path, params, headers)
            except MySigenAuthError:
                self._access_token = None
                await self._async_ensure_token()
                return await self._async_get_once(path, params, headers)
        except (MySigenAuthError, MySigenPermissionError) as err:
            raise type(err)(f"{what}: {err}") from err

    async def async_station_id(self) -> str:
        """Return the account's station id, read once and then kept."""
        if self._station_id is None:
            await self.async_read_station()
        return self._station_id

    async def async_read_station(self) -> bool:
        """Read the station: its id and the DC chargers listed for it.

        Returns False, and changes nothing, when the account now opens a
        different station than the one this session started with. Only a
        new session, whose station is checked against the plant again, may
        use it.
        """
        data = await self._async_get("station", _STATION_PATH)
        station_id = data.get("stationId") if isinstance(data, dict) else None
        if station_id in (None, ""):
            raise MySigenConnectionError("The account has no station")
        if self._station_id is not None and str(station_id) != self._station_id:
            return False
        self._station_id = str(station_id)
        serials = data.get("dcSnList")
        self.dc_serials = tuple(
            str(sn) for sn in serials if sn not in (None, "")
        ) if isinstance(serials, list) else ()
        return True

    @property
    def dc_serial(self) -> str | None:
        """The first DC charger mySigen lists for the station; None without one."""
        return self.dc_serials[0] if self.dc_serials else None

    async def _async_dc_get(self, what: str, path: str) -> Any:
        if self.dc_serial is None:
            raise MySigenConnectionError(f"{what}: no DC charger")
        return await self._async_get(what, path, {
            "stationId": await self.async_station_id(), "snCode": self.dc_serial,
        })

    async def _async_dc_post(self, what: str, path: str, body: dict[str, Any]) -> None:
        if self.dc_serial is None:
            raise MySigenRejectedError(f"{what}: not sent, no DC charger")
        await self._async_put(what, path, {
            "stationId": await self._async_station_number(), "snCode": self.dc_serial, **body,
        }, method="POST")

    async def async_dc_charge_mode(self) -> DcChargeMode | None:
        return parse_dc_charge_mode(await self._async_dc_get("charger mode", _DC_MODE_PATH))

    async def async_dc_charge_setting(self) -> DcChargeSetting | None:
        return parse_dc_charge_setting(
            await self._async_dc_get("charger setting", _DC_SETTING_PATH)
        )

    async def async_v2x_enabled(self) -> bool | None:
        return parse_v2x_enabled(await self._async_dc_get("vehicle discharging", _DC_V2X_PATH))

    async def async_soc_range(self) -> tuple[int, int] | None:
        return parse_soc_range(await self._async_get(
            "charge level range", _SOC_RANGE_PATH, {"stationId": await self.async_station_id()}
        ))

    async def async_set_dc_charge_mode(self, body: dict[str, Any]) -> None:
        """Change the charger mode or its settings. Sent once."""
        await self._async_dc_post("set charger mode", _DC_MODE_PATH, body)

    async def async_set_dc_stop_level(self, current: DcChargeSetting, level: int) -> None:
        """Change the stop level, keeping the allowed power. Sent once."""
        await self._async_dc_post("set charger stop level", _DC_SETTING_PATH, {
            "allowedChargePower": current.allowed_power,
            "vehicleChargingCutoffSoc": int(level),
        })

    async def async_set_v2x_enabled(self, enabled: bool) -> None:
        """Allow or block the car supplying power. Sent once."""
        await self._async_dc_post(
            "set vehicle discharging", _DC_V2X_WRITE_PATH, {"dischargeEnable": 1 if enabled else 0}
        )

    async def async_device_serials(self) -> set[str]:
        """Return the serial numbers mySigen lists for the account's station.

        Two sources are asked because plants differ in which one they fill.
        A source that fails on its own is skipped; sign-in and rate-limit
        errors are raised.
        """
        params = {"stationId": await self.async_station_id()}
        serials: set[str] = set()
        for what, path, parse in (
            ("device tree", _TOPOLOGY_PATH, topology_serials),
            ("inverter list", _AIO_PATH, aio_serials),
        ):
            try:
                data = await self._async_get(what, path, params)
            except (MySigenConnectionError, MySigenPermissionError) as err:
                _LOGGER.debug("mySigen serial source unavailable: %s", err)
                continue
            serials |= parse(data)
            if path == _TOPOLOGY_PATH:
                self.battery_modules = parse_battery_modules(data)
        return serials

    async def async_read_battery_modules(self) -> tuple[BatteryModule, ...]:
        """Read the device tree again and return its battery modules."""
        data = await self._async_get(
            "device tree", _TOPOLOGY_PATH, {"stationId": await self.async_station_id()}
        )
        self.battery_modules = parse_battery_modules(data)
        return self.battery_modules

    async def _async_module_get(
        self,
        what: str,
        path: str,
        serial: str,
        headers: dict[str, str] | None = None,
        **extra: str,
    ) -> Any:
        return await self._async_get(what, path, {
            "stationId": await self.async_station_id(), "snCode": serial, **extra,
        }, headers)

    async def async_module_static_raw(self, serial: str) -> Any:
        return await self._async_module_get(
            "battery module", _MODULE_STATIC_PATH, serial,
            deviceType=str(BATTERY_DEVICE_TYPE),
        )

    async def async_module_live_raw(self, serial: str) -> Any:
        return await self._async_module_get(
            "battery module readings", _MODULE_LIVE_PATH, serial, _PACKAGE_HEADER,
            deviceType=str(BATTERY_DEVICE_TYPE), refreshFlag="",
        )

    async def async_module_pack_raw(self, serial: str) -> Any:
        return await self._async_module_get("battery pack", _MODULE_PACK_PATH, serial)

    async def async_module_static(self, serial: str) -> dict[str, float | str]:
        """Model, firmware and rated capacity of one module."""
        return parse_module_values(
            await self.async_module_static_raw(serial), "paramInfoVOList", MODULE_STATIC_FIELDS
        )

    async def async_module_readings(self, serial: str) -> dict[str, float | str]:
        """Charge level, power, pack voltage and heating of one module."""
        return parse_module_values(
            await self.async_module_live_raw(serial), "realTimeInfo", MODULE_LIVE_FIELDS
        )

    async def async_module_pack(self, serial: str) -> dict[str, float | str]:
        """Lifetime discharge and average cell values of one module."""
        return parse_module_values(
            await self.async_module_pack_raw(serial), "paramInfoVOList", MODULE_PACK_FIELDS
        )

    async def _async_put(
        self, what: str, path: str, body: dict[str, Any], method: str = "PUT"
    ) -> None:
        """Send one change. It is never repeated, not even after a sign-out."""
        if self._write_session is None or self._write_session.closed:
            raise MySigenRejectedError(f"{what}: not sent, no single-send connection")
        try:
            await self._async_ensure_token()
        except MySigenConnectionError as err:
            raise MySigenRejectedError(f"{what}: not sent, {err}") from err
        try:
            async with self._write_session.request(
                method,
                f"{REGIONS[self._region]}{path}",
                json=body,
                allow_redirects=False,
                trace_request_ctx={},
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT),
            ) as response:
                status = response.status
                retry_after = _retry_after(response.headers)
                try:
                    payload = await response.json(content_type=None)
                except (ValueError, aiohttp.ClientError):
                    payload = None
        except _ResendBlocked as err:
            raise MySigenUnknownOutcome(
                f"{what}: connection dropped after sending"
            ) from err
        except Exception as err:  # noqa: BLE001 - any failure here is ambiguous
            # The request may have arrived. Only a later read can tell.
            raise MySigenUnknownOutcome(f"{what}: {type(err).__name__}") from err

        code = payload.get("code") if isinstance(payload, dict) else None
        if status == 429 or code in _RATE_LIMIT_CODES:
            raise MySigenRateLimitError(retry_after)
        detail = f"{what}: HTTP {status}, code {code}"
        if status == 401 or code in (401, "401"):
            raise MySigenAuthError(detail)
        if status == 403 or code in (403, "403"):
            raise MySigenPermissionError(detail)
        if status >= 500:
            # A server or gateway error can follow a change that was applied.
            raise MySigenUnknownOutcome(detail)
        if status >= 400 or (isinstance(payload, dict) and code not in _SUCCESS_CODES):
            raise MySigenRejectedError(detail)
        if not isinstance(payload, dict):
            raise MySigenUnknownOutcome(f"{what}: unreadable answer, HTTP {status}")

    async def _async_station_number(self) -> int | str:
        station_id = await self.async_station_id()
        return int(station_id) if station_id.isdigit() else station_id

    async def async_start_override(
        self, mode: str, minutes: int, power_kw: float | None = None
    ) -> None:
        """Ask the plant to start a timed override. Sent once.

        `power_kw` caps charging or discharging; None means no limit. It is
        ignored for the other modes, as the app does.
        """
        if mode not in CONTROL_MODES:
            raise ValueError(f"Mode {mode!r} is not available for control")
        if not MIN_OVERRIDE_MINUTES <= minutes <= MAX_OVERRIDE_MINUTES:
            raise ValueError("Duration is outside the allowed range")
        if power_kw is not None and not 0 < power_kw <= MAX_POWER_LIMIT_KW:
            raise ValueError("Power limit is outside the allowed range")
        code = next(key for key, name in OVERRIDE_MODES.items() if name == mode)
        if mode not in POWER_MODES:
            limit = ""
        elif power_kw is None:
            limit = _UNLIMITED_POWER
        else:
            limit = f"{power_kw:.3f}"
        await self._async_put("start battery override", _OVERRIDE_WRITE_PATH, {
            "enable": True,
            "stationId": await self._async_station_number(),
            "mode": code,
            "duration": str(minutes),
            "powerLimitation": limit,
        })

    async def async_cancel_override(self) -> None:
        """Ask the plant to end the running override. Sent once."""
        await self._async_put("cancel battery override", _OVERRIDE_WRITE_PATH, {
            "enable": False,
            "stationId": await self._async_station_number(),
            "mode": "",
            "duration": "",
            "powerLimitation": "",
        })

    async def _async_station_get(self, what: str, path: str) -> Any:
        return await self._async_get(
            what, path.format(station_id=await self.async_station_id())
        )

    async def async_available_modes(self) -> frozenset[int]:
        """The operating mode codes the plant offers."""
        return parse_available_modes(await self._async_station_get("modes", _MODES_PATH))

    async def async_operating_mode(self) -> int | None:
        """The operating mode code in use."""
        return parse_operating_mode(
            await self._async_station_get("operating mode", _MODE_PATH)
        )

    async def async_battery_export(self) -> BatteryExport | None:
        return parse_battery_export(
            await self._async_station_get("battery export", _EXPORT_PATH)
        )

    async def async_peak_shaving(self) -> PeakShaving | None:
        return parse_peak_shaving(
            await self._async_station_get("peak shaving", _PEAK_PATH)
        )

    async def async_set_operating_mode(self, code: int) -> None:
        """Choose the operating mode. Sent once."""
        if code not in OPERATING_MODES.values():
            raise ValueError(f"Operating mode {code!r} is not available for control")
        # The two reference clients name this field differently; both are sent.
        await self._async_put("set operating mode", _MODE_WRITE_PATH, {
            "stationId": await self._async_station_number(),
            "operationMode": code,
            "workingMode": code,
            "profileId": -1,
        })

    async def async_set_battery_export(self, current: BatteryExport, enabled: bool) -> None:
        """Allow or block battery export, keeping the installer's setting. Sent once."""
        await self._async_put("set battery export", _EXPORT_WRITE_PATH, {
            "stationId": await self._async_station_number(),
            "installerSetEnable": current.installer,
            "ownerSetEnable": enabled,
        })

    async def async_set_peak_shaving(self, schedule: dict[str, Any]) -> None:
        """Write the whole peak shaving schedule back. Sent once."""
        await self._async_put(
            "set peak shaving",
            _PEAK_WRITE_PATH,
            {**schedule, "stationId": await self._async_station_number()},
            method="POST",
        )

    async def async_grid_current_limit(self) -> GridCurrentLimit | None:
        """The max grid connection current, as the app's Grid Settings show it."""
        return parse_grid_current_limit(
            await self._async_station_get("grid connection current", _GRID_CURRENT_PATH)
        )

    async def async_set_grid_current_limit(self, amps: float) -> None:
        """Set the owner's max grid connection current, in amperes. Sent once.

        The limit is switched on with it, as the app does when you save one.
        The installer's ceiling is not touched.
        """
        await self._async_put("set grid connection current", _GRID_CURRENT_WRITE_PATH, {
            "stationId": await self._async_station_number(),
            "enable": True,
            "ownerSetLimitation": f"{float(amps):.1f}",
            "installerSetLimitation": None,
        })

    async def async_grid_connection(self) -> GridConnection | None:
        """Whether the Gateway holds the plant on or off the grid."""
        return parse_grid_connection(await self._async_get(
            "grid connection", _GRID_STATUS_PATH,
            {"stationId": await self.async_station_id()},
        ))

    async def async_set_off_grid(self, off_grid: bool) -> None:
        """Disconnect from the grid, or reconnect. Sent once."""
        await self._async_put(
            "disconnect from the grid" if off_grid else "reconnect to the grid",
            _GRID_WRITE_PATH,
            {"onGridState": 1 if off_grid else 0,
             "stationId": await self._async_station_number()},
            method="POST",
        )

    async def async_gateway(self) -> Any:
        """Read the Gateway's own readings as mySigen returns them."""
        station_id = await self.async_station_id()
        return await self._async_get(
            "gateway", _GATEWAY_PATH.format(station_id=station_id)
        )

    async def async_gateway_readings(self) -> GatewayReadings | None:
        """Read the Gateway's grid side; None when there are no readings."""
        return parse_gateway(await self.async_gateway())

    async def async_override_status(self) -> OverrideStatus:
        """Read the temporary battery override."""
        station_id = await self.async_station_id()
        data = await self._async_get(
            "battery override", _OVERRIDE_PATH.format(station_id=station_id)
        )
        if not self._logged_keys and isinstance(data, dict):
            # Field names only, to help verify the answer's shape.
            _LOGGER.debug("mySigen override answer keys: %s", sorted(data))
            self._logged_keys = True
        return parse_override(data)
