"""PV string names from the mySigen app on Sigenergy ESS's PV string devices.

Sigenergy ESS names each PV string device "<inverter> PV<n>", and its
entities take their name from the device. The add-on sets the device's name
to the one set in the app, the way a user renames a device, and only where
the user has not:

* A device the user renamed is left alone.
* Each app name is applied once. When the user clears it again, the
  default name stays until the name in the app changes.
* When a name is removed in the app, the name the add-on set is cleared.

Entity IDs, unique IDs and the device's identity never change. What the
add-on set is kept in a small store, so it can tell its names from the
user's. `plan_pv_names` holds the rules and does not import Home Assistant.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1


@dataclass(frozen=True)
class PvStringDevice:
    """A PV string device of the plant, as the device registry has it."""

    device_id: str
    # The inverter's serial number and the string's number on it.
    serial: str
    number: int
    # The user's name for the device; None when it has the default name.
    name_by_user: str | None


def plan_pv_names(
    devices: list[PvStringDevice],
    names: dict[str, dict[int, str]],
    applied: dict[str, str],
) -> tuple[dict[str, str | None], dict[str, str]]:
    """Which device names to set, and what to remember afterwards.

    `names` holds, per inverter serial, the names set in the app. An
    inverter missing from it was not read this time: its devices are left
    as they are. `applied` maps a device ID to the app name the add-on last
    set or offered. Returns the new `name_by_user` per device to change
    (None restores the default name) and the new `applied`.
    """
    updates: dict[str, str | None] = {}
    remembered = dict(applied)
    for device in devices:
        if device.serial not in names:
            continue
        wanted = names[device.serial].get(device.number)
        ours = remembered.get(device.device_id)
        current = device.name_by_user
        if wanted is None:
            # Removed in the app, or never set: take back only our own name.
            if ours is not None:
                if current == ours:
                    updates[device.device_id] = None
                del remembered[device.device_id]
            continue
        if wanted == ours:
            # Already applied once. A name the user changed or cleared stays.
            continue
        if current is None or current == ours:
            if current != wanted:
                updates[device.device_id] = wanted
            remembered[device.device_id] = wanted
        else:
            # The user named the device; never overwrite that. Remember the
            # app name so it is not offered again until it changes.
            remembered[device.device_id] = wanted
    # Forget devices that no longer exist.
    known = {device.device_id for device in devices}
    remembered = {key: value for key, value in remembered.items() if key in known}
    return updates, remembered


def pv_string_number(identifier: str, plant_entry_id: str) -> int | None:
    """The string number in a Sigenergy ESS PV string device identifier."""
    if not identifier.startswith(f"{plant_entry_id}_"):
        return None
    match = re.search(r"_pv(\d+)$", identifier)
    return int(match.group(1)) if match else None


def find_pv_string_devices(
    registry: Any, plant_entry_id: str, modbus_domain: str, normalize: Any
) -> list[PvStringDevice]:
    """The plant's PV string devices, each under an inverter with a serial."""
    from homeassistant.helpers import device_registry as dr

    devices = dr.async_entries_for_config_entry(registry, plant_entry_id)
    serials = {
        device.id: serial
        for device in devices
        if (serial := normalize(device.serial_number))
    }
    found: list[PvStringDevice] = []
    for device in devices:
        serial = serials.get(device.via_device_id or "")
        if serial is None:
            continue
        number = next(
            (
                number
                for domain, identifier in device.identifiers
                if domain == modbus_domain
                and (number := pv_string_number(identifier, plant_entry_id)) is not None
            ),
            None,
        )
        if number is not None:
            found.append(PvStringDevice(device.id, serial, number, device.name_by_user))
    return found


class PvNameStore:
    """What the add-on set, per device ID, kept across restarts."""

    def __init__(self, hass: Any, entry_id: str, domain: str) -> None:
        from homeassistant.helpers.storage import Store

        self._store = Store(hass, STORE_VERSION, f"{domain}.pv_names.{entry_id}")
        self.applied: dict[str, str] | None = None

    async def async_load(self) -> dict[str, str]:
        if self.applied is None:
            data = await self._store.async_load()
            applied = data.get("applied") if isinstance(data, dict) else None
            self.applied = {
                str(key): value for key, value in (applied or {}).items()
                if isinstance(value, str)
            } if isinstance(applied, dict) else {}
        return self.applied

    async def async_save(self, applied: dict[str, str]) -> None:
        if applied != self.applied:
            self.applied = dict(applied)
            await self._store.async_save({"applied": self.applied})

    async def async_remove(self) -> None:
        await self._store.async_remove()


async def async_apply_pv_names(
    hass: Any,
    store: PvNameStore,
    devices: list[PvStringDevice],
    names: dict[str, dict[int, str]],
) -> None:
    """Set the planned device names and remember them."""
    from homeassistant.helpers import device_registry as dr

    applied = await store.async_load()
    updates, remembered = plan_pv_names(devices, names, applied)
    registry = dr.async_get(hass)
    for device_id, name in updates.items():
        if registry.async_get(device_id) is None:
            remembered.pop(device_id, None)
            continue
        registry.async_update_device(device_id, name_by_user=name)
        _LOGGER.info(
            "PV string device %s: %s", device_id,
            f"named {name!r} as in the mySigen app" if name else "default name restored",
        )
    await store.async_save(remembered)


async def async_restore_pv_names(hass: Any, store: PvNameStore) -> None:
    """Clear the names the add-on set and the user kept; forget the rest."""
    from homeassistant.helpers import device_registry as dr

    registry = dr.async_get(hass)
    for device_id, name in (await store.async_load()).items():
        device = registry.async_get(device_id)
        if device is not None and device.name_by_user == name:
            registry.async_update_device(device_id, name_by_user=None)
    await store.async_remove()
