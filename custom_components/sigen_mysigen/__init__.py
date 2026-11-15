"""Sigenergy ESS mySigen cloud add-on.

Reads mySigen for a plant that the Sigenergy Local Modbus integration
already provides. It sends no commands.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import CONF_PLANT_ENTRY_ID, DOMAIN, MODBUS_DOMAIN
from .coordinator import MySigenCoordinator

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a mySigen connection linked to one Modbus plant."""
    plant_entry = hass.config_entries.async_get_entry(entry.data[CONF_PLANT_ENTRY_ID])
    if plant_entry is None or plant_entry.domain != MODBUS_DOMAIN:
        raise ConfigEntryError(
            "The linked Sigenergy Modbus plant no longer exists. "
            "Remove this mySigen connection and add it again."
        )
    # This add-on owns no devices. Drop any that an earlier version created
    # for this entry; its entities live on the Modbus plant device.
    device_registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        device_registry.async_remove_device(device.id)
    coordinator = MySigenCoordinator(hass, entry)
    # A failure here only delays this entry; the Modbus plant is unaffected.
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a mySigen connection."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
