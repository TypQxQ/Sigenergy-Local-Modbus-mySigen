"""Check that a mySigen account belongs to the linked Modbus plant.

The Modbus integration stores each inverter's serial number in the device
registry. The account is accepted only when mySigen lists at least one of
those serial numbers for its station.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .api import MySigenSession, normalize_serial
from .const import MODBUS_DOMAIN

LINK_OK = None
NO_LOCAL_SERIAL = "no_local_serial"
NO_CLOUD_SERIAL = "no_cloud_serial"
STATION_MISMATCH = "station_mismatch"


def match_station(local: set[str], cloud: set[str]) -> str | None:
    """Return None for a match, otherwise the reason the link is refused."""
    if not local:
        return NO_LOCAL_SERIAL
    if not cloud:
        return NO_CLOUD_SERIAL
    return LINK_OK if local & cloud else STATION_MISMATCH


def local_inverter_serials(hass: HomeAssistant, plant_entry_id: str) -> set[str]:
    """Serial numbers the Modbus integration read for this plant's inverters."""
    return {
        serial
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), plant_entry_id
        )
        if (serial := normalize_serial(device.serial_number))
    }


async def async_link_problem(
    hass: HomeAssistant, session: MySigenSession, plant_entry_id: str
) -> str | None:
    """Compare the plant with the account's station. Raises MySigenError."""
    local = local_inverter_serials(hass, plant_entry_id)
    if not local:
        # Nothing to compare with, so do not ask mySigen at all.
        return NO_LOCAL_SERIAL
    return match_station(local, await session.async_device_serials())


def remote_ems_state(hass: HomeAssistant, plant_entry_id: str) -> str | None:
    """Return "on" or "off" for the plant's Remote EMS switch, else None.

    None means the Modbus integration has no current reading, which must
    not be taken as off.
    """
    for entity in er.async_entries_for_config_entry(er.async_get(hass), plant_entry_id):
        if (
            entity.platform == MODBUS_DOMAIN
            and entity.domain == "switch"
            and entity.unique_id.endswith("_plant_remote_ems_enable")
        ):
            state = hass.states.get(entity.entity_id)
            return state.state if state and state.state in ("on", "off") else None
    return None
