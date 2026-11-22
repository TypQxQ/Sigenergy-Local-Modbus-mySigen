"""PV string names from the mySigen app on Sigenergy ESS's PV string devices.

Sigenergy ESS names each PV string device "<inverter> PV<n>", and its
entities take their name from the device. The add-on sets the device's name
to the one set in the app, the way a user renames a device, and only where
the user has not:

* A device the user renamed is left alone.
* Each app name is applied once. When the user clears it again, the
  default name stays until the name in the app changes.
* When a name is removed in the app, the name the add-on set is cleared.

Entity IDs, unique IDs and the device's identity never change. A small
store keeps two things apart: the names the add-on actually set, the only
ones it ever changes or clears, and the app names it has already offered.
A name the user gave, also one that happens to match the app's, is never
counted as the add-on's. `plan_pv_names` holds the rules and does not
import Home Assistant.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
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


@dataclass
class PvNameMemory:
    """What the add-on knows from earlier runs, per device ID.

    `applied`: the name the add-on set and the device still had last time.
    `offered`: the app name last considered for the device, set or not.
    """

    applied: dict[str, str] = field(default_factory=dict)
    offered: dict[str, str] = field(default_factory=dict)


def plan_pv_names(
    devices: list[PvStringDevice],
    names: dict[str, dict[int, str]],
    memory: PvNameMemory,
) -> tuple[dict[str, str | None], PvNameMemory]:
    """Which device names to set, and what to remember afterwards.

    `names` holds, per inverter serial, the names set in the app. An
    inverter missing from it was not read this time: its devices are left
    as they are. Returns the new `name_by_user` per device to change (None
    restores the default name) and the new memory.
    """
    updates: dict[str, str | None] = {}
    applied, offered = dict(memory.applied), dict(memory.offered)
    for device in devices:
        if device.serial not in names:
            continue
        key, current = device.device_id, device.name_by_user
        wanted = names[device.serial].get(device.number)
        ours = applied.get(key)
        if ours is not None and current != ours:
            # The user renamed or cleared it since: no longer the add-on's.
            del applied[key]
            ours = None
        if wanted is None:
            # Removed in the app, or never set: take back only our own name.
            if ours is not None:
                updates[key] = None
                del applied[key]
            offered.pop(key, None)
            continue
        if wanted == offered.get(key):
            # Offered once already. A name the user changed or cleared stays.
            continue
        offered[key] = wanted
        if current is None or ours is not None:
            if current != wanted:
                updates[key] = wanted
            applied[key] = wanted
        # Otherwise the user named the device, perhaps the same as the app
        # does: it stays the user's and is never changed or cleared.
    # Forget devices that no longer exist.
    known = {device.device_id for device in devices}
    return updates, PvNameMemory(
        {key: value for key, value in applied.items() if key in known},
        {key: value for key, value in offered.items() if key in known},
    )


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
    """The add-on's PV name memory, kept across restarts."""

    def __init__(self, hass: Any, entry_id: str, domain: str) -> None:
        from homeassistant.helpers.storage import Store

        self._store = Store(hass, STORE_VERSION, f"{domain}.pv_names.{entry_id}")
        self.memory: PvNameMemory | None = None

    async def async_load(self) -> PvNameMemory:
        if self.memory is None:
            data = await self._store.async_load()
            data = data if isinstance(data, dict) else {}

            def names(key: str) -> dict[str, str]:
                value = data.get(key)
                return {
                    str(device): name for device, name in value.items()
                    if isinstance(name, str)
                } if isinstance(value, dict) else {}

            self.memory = PvNameMemory(names("applied"), names("offered"))
        return self.memory

    async def async_save(self, memory: PvNameMemory) -> None:
        if memory != self.memory:
            self.memory = PvNameMemory(dict(memory.applied), dict(memory.offered))
            await self._store.async_save(
                {"applied": self.memory.applied, "offered": self.memory.offered}
            )

    async def async_remove(self) -> None:
        await self._store.async_remove()


def current_names(registry: Any, devices: list[PvStringDevice]) -> list[PvStringDevice]:
    """The devices with their names as the registry has them now.

    A device that is gone is left out. Called after every await, right
    before planning, so a rename made meanwhile is never overwritten.
    """
    fresh: list[PvStringDevice] = []
    for device in devices:
        entry = registry.async_get(device.device_id)
        if entry is not None:
            fresh.append(replace(device, name_by_user=entry.name_by_user))
    return fresh


async def async_apply_pv_names(
    hass: Any,
    store: PvNameStore,
    devices: list[PvStringDevice],
    names: dict[str, dict[int, str]],
) -> None:
    """Set the planned device names and remember them."""
    from homeassistant.helpers import device_registry as dr

    memory = await store.async_load()
    registry = dr.async_get(hass)
    # Nothing is awaited from here until the names are set.
    updates, memory = plan_pv_names(current_names(registry, devices), names, memory)
    for device_id, name in updates.items():
        registry.async_update_device(device_id, name_by_user=name)
        _LOGGER.info(
            "PV string device %s: %s", device_id,
            f"named {name!r} as in the mySigen app" if name else "default name restored",
        )
    await store.async_save(memory)


async def async_restore_pv_names(hass: Any, store: PvNameStore) -> None:
    """Clear the names the add-on set that the devices still have."""
    from homeassistant.helpers import device_registry as dr

    registry = dr.async_get(hass)
    for device_id, name in (await store.async_load()).applied.items():
        device = registry.async_get(device_id)
        if device is not None and device.name_by_user == name:
            registry.async_update_device(device_id, name_by_user=None)
    await store.async_remove()
