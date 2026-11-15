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
CONTROL_MODES: tuple[str, ...] = ("hold_battery",)
# The range the reference client enforces; wider ranges are unverified.
MIN_OVERRIDE_MINUTES = 30
MAX_OVERRIDE_MINUTES = 120

_TOKEN_PATH = "auth/oauth/token"
_STATION_PATH = "device/owner/station/home"
# The misspelling is the service's own path.
_OVERRIDE_PATH = "device/energy-profile/instant/manunal/{station_id}"
_TOPOLOGY_PATH = "device/devicetreepanel/topology"
_AIO_PATH = "device/aio/by/station"
_GATEWAY_PATH = "device/gateway/{station_id}"
_OVERRIDE_WRITE_PATH = "device/energy-profile/instant/manunal"
_LABEL_KEYS = ("paramKey", "key")
_NUMBER_WITH_UNIT = re.compile(r"(-?\d+(?:\.\d+)?)\s*([A-Za-z]*)")
# Label in the Gateway answer -> (reading, unit it must carry).
_GATEWAY_FIELDS: dict[str, tuple[str, str]] = {
    "phase a voltage": ("voltage_a", "V"),
    "phase b voltage": ("voltage_b", "V"),
    "phase c voltage": ("voltage_c", "V"),
    "phase a current": ("current_a", "A"),
    "phase b current": ("current_b", "A"),
    "phase c current": ("current_c", "A"),
    "voltage frequency": ("frequency", "Hz"),
}
_GATEWAY_CONTACTOR = "grid side contactor status"
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


@dataclass(frozen=True)
class GatewayReadings:
    """Grid-side readings of the Gateway. A missing key means no reading."""

    values: dict[str, float]
    contactor: str | None


def parse_measurement(text: Any, unit: str) -> float | None:
    """Read "231.40 V" as 231.4. Anything unexpected is no reading."""
    if not isinstance(text, str):
        return None
    match = _NUMBER_WITH_UNIT.fullmatch(text.strip())
    # A different unit (kV, mA) is refused instead of guessed at.
    if match is None or match.group(2).lower() != unit.lower():
        return None
    return float(match.group(1))


def parse_gateway(data: Any) -> GatewayReadings | None:
    """Pick the grid-side readings out of the Gateway answer.

    Returns None when the answer carries none of them, which is how a
    system without a Gateway shows.
    """
    rows = data.get("gridSideInfoList") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None
    values: dict[str, float] = {}
    contactor: str | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("paramKey") or "").strip().lower()
        value = row.get("paramValue")
        if label in _GATEWAY_FIELDS:
            key, unit = _GATEWAY_FIELDS[label]
            reading = parse_measurement(value, unit)
            if reading is not None:
                values[key] = reading
        elif label == _GATEWAY_CONTACTOR and isinstance(value, str) and value.strip():
            contactor = value.strip().lower().replace(" ", "_")
    if not values and contactor is None:
        return None
    return GatewayReadings(values, contactor)


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


def masked_shape(value: Any, depth: int = 0) -> Any:
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
            else masked_shape(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [masked_shape(item, depth + 1) for item in value[:40]]
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


def parse_override(data: Any) -> OverrideStatus:
    """Turn the override answer into a status, keeping missing apart from off."""
    if not isinstance(data, dict):
        raise MySigenConnectionError("Unexpected override answer")
    enabled = bool(data.get("enable"))
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
    return OverrideStatus(enabled, mode, ends_at)


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
        self, session: aiohttp.ClientSession, region: str, username: str, password: str
    ) -> None:
        self._session = session
        self._region = region
        self._username = username
        self._password = password
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at = 0.0
        self._station_id: str | None = None
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

    async def _async_get_once(self, path: str, params: dict[str, str] | None) -> Any:
        try:
            async with self._session.request(
                "GET",
                f"{REGIONS[self._region]}{path}",
                params=params,
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
        self, what: str, path: str, params: dict[str, str] | None = None
    ) -> Any:
        """GET one path, signing in again once if the session was dropped.

        `what` names the request in refusals, so a message says which read
        the account was refused.
        """
        try:
            await self._async_ensure_token()
            try:
                return await self._async_get_once(path, params)
            except MySigenAuthError:
                self._access_token = None
                await self._async_ensure_token()
                return await self._async_get_once(path, params)
        except (MySigenAuthError, MySigenPermissionError) as err:
            raise type(err)(f"{what}: {err}") from err

    async def async_station_id(self) -> str:
        """Return the account's station id, read once and then kept."""
        if self._station_id is None:
            data = await self._async_get("station", _STATION_PATH)
            station_id = data.get("stationId") if isinstance(data, dict) else None
            if station_id in (None, ""):
                raise MySigenConnectionError("The account has no station")
            self._station_id = str(station_id)
        return self._station_id

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
                serials |= parse(await self._async_get(what, path, params))
            except (MySigenConnectionError, MySigenPermissionError) as err:
                _LOGGER.debug("mySigen serial source unavailable: %s", err)
        return serials

    async def _async_put(self, what: str, path: str, body: dict[str, Any]) -> None:
        """Send one change. It is never repeated, not even after a sign-out."""
        try:
            await self._async_ensure_token()
        except MySigenConnectionError as err:
            raise MySigenRejectedError(f"{what}: not sent, {err}") from err
        try:
            async with self._session.request(
                "PUT",
                f"{REGIONS[self._region]}{path}",
                json=body,
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
        except (aiohttp.ClientError, TimeoutError) as err:
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
        if status >= 400 or (isinstance(payload, dict) and code not in _SUCCESS_CODES):
            raise MySigenRejectedError(detail)
        if not isinstance(payload, dict):
            raise MySigenUnknownOutcome(f"{what}: unreadable answer, HTTP {status}")

    async def _async_station_number(self) -> int | str:
        station_id = await self.async_station_id()
        return int(station_id) if station_id.isdigit() else station_id

    async def async_start_override(self, mode: str, minutes: int) -> None:
        """Ask the plant to start a timed override. Sent once."""
        if mode not in CONTROL_MODES:
            raise ValueError(f"Mode {mode!r} is not available for control")
        if not MIN_OVERRIDE_MINUTES <= minutes <= MAX_OVERRIDE_MINUTES:
            raise ValueError("Duration is outside the allowed range")
        code = next(key for key, name in OVERRIDE_MODES.items() if name == mode)
        await self._async_put("start battery override", _OVERRIDE_WRITE_PATH, {
            "enable": True,
            "stationId": await self._async_station_number(),
            "mode": code,
            "duration": str(minutes),
            "powerLimitation": "",
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
