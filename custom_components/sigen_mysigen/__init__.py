"""Sigenergy ESS mySigen cloud add-on.

Reads mySigen for a plant that the Sigenergy Local Modbus integration
already provides. Changes are sent only while the user has allowed control.
The one thing it changes in Home Assistant itself is the name of a PV
string device, as set in the app, where the user has not renamed it.
"""
from __future__ import annotations

import logging

import probatio

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
    ServiceValidationError,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.translation import async_get_translations

from .api import (
    CONTROL_MODES,
    MAX_OVERRIDE_MINUTES,
    MAX_POWER_LIMIT_KW,
    MIN_OVERRIDE_MINUTES,
    MySigenAuthError,
    MySigenConnectionError,
    MySigenError,
    MySigenPermissionError,
    MySigenRateLimitError,
    normalize_serial,
)
from .const import (
    CONF_ENTITY_NAME_LANGUAGE,
    CONF_PLANT_ENTRY_ID,
    ENTITY_NAME_LANGUAGE_PLANT,
    DOMAIN,
    ENTITY_NAME_LANGUAGES,
    MODBUS_CONF_ENTITY_NAME_LANGUAGE,
    MODBUS_DOMAIN,
    name_language,
)
from .commands import async_run_command
from .coordinator import MySigenCoordinator
from .link import STATION_MISMATCH, async_link_problem
from .modules import INFO, ModuleReader, module_identifier
from .pv_names import PvNameStore, async_restore_pv_names

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]
# Only set up while the user has switched control on.
CONTROL_PLATFORMS = [Platform.BUTTON, Platform.NUMBER, Platform.SELECT, Platform.SWITCH]

NO_PERMISSION = "no_permission"
_ISSUES = ("plant_missing", STATION_MISMATCH, NO_PERMISSION)


SERVICE_START = "start_battery_override"
SERVICE_CANCEL = "cancel_battery_override"
ATTR_ENTRY_ID = "config_entry_id"
ATTR_MODE = "mode"
ATTR_DURATION = "duration"
ATTR_POWER = "power_limit"

_CANCEL_SCHEMA = probatio.Schema({probatio.Optional(ATTR_ENTRY_ID): cv.string})
_START_SCHEMA = _CANCEL_SCHEMA.extend({
    probatio.Required(ATTR_MODE): probatio.In(CONTROL_MODES),
    probatio.Required(ATTR_DURATION): probatio.All(
        probatio.Coerce(int), probatio.Range(MIN_OVERRIDE_MINUTES, MAX_OVERRIDE_MINUTES)
    ),
    probatio.Optional(ATTR_POWER): probatio.All(
        probatio.Coerce(float), probatio.Range(min=0, max=MAX_POWER_LIMIT_KW, min_included=False)
    ),
})


def _target_coordinator(hass: HomeAssistant, call: ServiceCall) -> MySigenCoordinator:
    """Find the connection an action is for."""
    loaded = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if entry_id := call.data.get(ATTR_ENTRY_ID):
        loaded = [entry for entry in loaded if entry.entry_id == entry_id]
        if not loaded:
            raise ServiceValidationError("That mySigen connection is not loaded.")
    elif len(loaded) != 1:
        raise ServiceValidationError(
            "Choose which mySigen connection to use."
            if loaded
            else "No mySigen connection is loaded."
        )
    return loaded[0].runtime_data


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the actions once; each call picks its connection."""

    async def handle(call: ServiceCall) -> None:
        coordinator = _target_coordinator(hass, call)
        if call.service == SERVICE_START:
            await async_run_command(
                hass,
                coordinator,
                mode=call.data[ATTR_MODE],
                minutes=call.data[ATTR_DURATION],
                power_kw=call.data.get(ATTR_POWER),
            )
        else:
            await async_run_command(hass, coordinator)

    hass.services.async_register(DOMAIN, SERVICE_START, handle, schema=_START_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CANCEL, handle, schema=_CANCEL_SCHEMA)
    return True


def _issue_id(key: str, entry: ConfigEntry) -> str:
    return f"{key}_{entry.entry_id}"


def _raise_link_issue(hass: HomeAssistant, entry: ConfigEntry, key: str) -> None:
    """Show a repair notice and stop setting up this connection."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(key, entry),
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=key,
        translation_placeholders={"name": entry.title},
    )
    raise ConfigEntryError(f"mySigen connection stopped: {key}")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a mySigen connection linked to one Modbus plant."""
    plant_entry_id = entry.data[CONF_PLANT_ENTRY_ID]
    plant_entry = hass.config_entries.async_get_entry(plant_entry_id)
    if plant_entry is None or plant_entry.domain != MODBUS_DOMAIN:
        _raise_link_issue(hass, entry, "plant_missing")

    coordinator = MySigenCoordinator(hass, entry)

    # Send nothing while mySigen has asked for fewer requests. The wait is
    # kept across setup attempts, so a retry lands here until it is over.
    if wait := coordinator.rate_limit_wait:
        raise ConfigEntryNotReady(f"Waiting {wait:.0f} s after a mySigen rate limit")

    # Refuse an account whose station does not contain this plant's inverter.
    # When the comparison cannot be made, keep running and say so.
    try:
        problem = await async_link_problem(hass, coordinator.session, plant_entry_id)
    except MySigenAuthError as err:
        raise ConfigEntryAuthFailed(
            f"mySigen refused the stored account ({err})"
        ) from err
    except MySigenRateLimitError as err:
        wait = coordinator.note_rate_limit(err)
        raise ConfigEntryNotReady(
            f"mySigen rate limit, waiting {wait:.0f} s"
        ) from err
    except MySigenError as err:
        problem = str(err)
    if problem == STATION_MISMATCH:
        _raise_link_issue(hass, entry, STATION_MISMATCH)
    coordinator.link_problem = problem
    if problem is not None:
        _LOGGER.warning(
            "Could not verify the mySigen station for %s: %s", entry.title, problem
        )
    for key in _ISSUES:
        ir.async_delete_issue(hass, DOMAIN, _issue_id(key, entry))

    # Battery modules, from the device tree the station check just read.
    modules = coordinator.session.battery_modules
    if modules is None:
        try:
            modules = await coordinator.session.async_read_battery_modules()
        except MySigenAuthError as err:
            raise ConfigEntryAuthFailed(
                f"mySigen refused the stored account ({err})"
            ) from err
        except MySigenRateLimitError as err:
            wait = coordinator.note_rate_limit(err)
            raise ConfigEntryNotReady(f"mySigen rate limit, waiting {wait:.0f} s") from err
        except (MySigenConnectionError, MySigenPermissionError) as err:
            # Readings without modules; the hourly check reads the list again.
            _LOGGER.debug("mySigen device tree read failed: %s", err)
            modules = ()
    coordinator.modules = ModuleReader(modules)
    _remove_old_devices(hass, entry, plant_entry_id, modules)

    # Smart loads get switches, so they are only listed while control is on.
    smart_loads_known = False
    if coordinator.allow_control:
        try:
            coordinator.smart_loads = await coordinator.session.async_smart_loads()
            smart_loads_known = True
        except MySigenAuthError as err:
            raise ConfigEntryAuthFailed(
                f"mySigen refused the stored account ({err})"
            ) from err
        except MySigenRateLimitError as err:
            wait = coordinator.note_rate_limit(err)
            raise ConfigEntryNotReady(f"mySigen rate limit, waiting {wait:.0f} s") from err
        except (MySigenConnectionError, MySigenPermissionError) as err:
            # No switches this time; the hourly check reads the list again.
            _LOGGER.debug("mySigen smart load list read failed: %s", err)

    # A failure here only delays this entry; the Modbus plant is unaffected.
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        if coordinator.permission_denied:
            # Retrying cannot help; the account needs more access.
            _raise_link_issue(hass, entry, NO_PERMISSION)
        raise
    # Entity names follow the language chosen under Reconfigure. By default
    # that is the Modbus plant's choice, so the entities of both
    # integrations read the same on the shared device.
    choice = entry.options.get(CONF_ENTITY_NAME_LANGUAGE, ENTITY_NAME_LANGUAGE_PLANT)
    plant_language = plant_entry.options.get(MODBUS_CONF_ENTITY_NAME_LANGUAGE)
    language = name_language(choice, plant_language)
    coordinator.entity_name_language = plant_language

    async def plant_changed(hass: HomeAssistant, plant: ConfigEntry) -> None:
        """Reload when the plant's name language changes, so names match again."""
        if (
            name_language(choice, plant.options.get(MODBUS_CONF_ENTITY_NAME_LANGUAGE))
            != language
        ):
            hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(plant_entry.add_update_listener(plant_changed))
    if language in ENTITY_NAME_LANGUAGES:
        coordinator.entity_name_translations = await async_get_translations(
            hass, language, "entity", {DOMAIN}
        )
    await _async_place_module_devices(
        hass, entry, coordinator,
        language if language in ENTITY_NAME_LANGUAGES else hass.config.language,
    )

    coordinator.platforms = list(PLATFORMS)
    if coordinator.allow_control:
        coordinator.platforms += CONTROL_PLATFORMS
    # Control or grid disconnecting was switched off: those entities must
    # not linger unavailable.
    registry = er.async_get(hass)
    smart_load_prefix = f"{entry.entry_id}_smart_load_"
    listed = {f"{smart_load_prefix}{load.load_id}" for load in coordinator.smart_loads}
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (entity.domain in CONTROL_PLATFORMS and not coordinator.allow_control) or (
            entity.unique_id == f"{entry.entry_id}_grid_disconnect"
            and not coordinator.allow_grid_disconnect
        ) or (
            # A smart load mySigen no longer lists, known from a list that
            # was read this time.
            smart_loads_known
            and entity.unique_id.startswith(smart_load_prefix)
            and entity.unique_id not in listed
        ):
            registry.async_remove(entity.entity_id)

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, coordinator.platforms)

    # PV string names from the app. Not needed for anything else, so a
    # failure only waits for the hourly check.
    try:
        await coordinator.async_update_pv_names()
    except MySigenRateLimitError as err:
        coordinator.note_rate_limit(err)
        _LOGGER.debug("mySigen PV string names not read: %s", err)
    except MySigenError as err:
        _LOGGER.debug("mySigen PV string names not read: %s", err)
    return True


def _module_identifiers(entry: ConfigEntry, device: dr.DeviceEntry) -> set[str]:
    """This entry's battery module identifiers on a device."""
    prefix = module_identifier(entry.entry_id, "")
    return {
        ident for domain, ident in device.identifiers
        if domain == DOMAIN and ident.startswith(prefix)
    }


def _remove_old_devices(
    hass: HomeAssistant, entry: ConfigEntry, plant_entry_id: str, modules: tuple
) -> None:
    """Remove devices this entry no longer provides.

    This add-on owns no devices: everything, the battery module devices
    included, is registered to the Modbus plant's entry. Any device of this
    entry is left over from an earlier version. A module device is removed
    only when mySigen lists modules and not this one: an empty list is more
    likely a passing fault.
    """
    keep = {module_identifier(entry.entry_id, module.serial) for module in modules}
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        registry.async_remove_device(device.id)
    if not modules:
        return
    for device in dr.async_entries_for_config_entry(registry, plant_entry_id):
        ours = _module_identifiers(entry, device)
        if ours and not ours & keep:
            registry.async_remove_device(device.id)


async def _async_place_module_devices(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: MySigenCoordinator, language: str
) -> None:
    """Register each battery module's device to the Modbus plant's entry.

    The module devices then show with the plant and its inverters under
    Sigenergy ESS, like every other device the add-on's entities are on.
    Home Assistant translates device names only for the integration that
    creates the device, so the name is taken from this add-on's own
    translations here.
    """
    plant_entry_id = entry.data[CONF_PLANT_ENTRY_ID]
    names = await async_get_translations(hass, language, "device", {DOMAIN})
    template = names.get(
        f"component.{DOMAIN}.device.battery_module.name", "Sigen battery module {position}"
    )
    registry = dr.async_get(hass)
    plant_devices = dr.async_entries_for_config_entry(registry, plant_entry_id)
    plant_device = next(
        (
            device.id for device in plant_devices
            if (MODBUS_DOMAIN, f"{plant_entry_id}_plant") in device.identifiers
        ),
        None,
    )
    for index, module in enumerate(coordinator.modules.modules, start=1):
        # Under its inverter, or the plant when the inverter is not found.
        parent = next(
            (
                device.id for device in plant_devices
                if module.inverter_serial
                and normalize_serial(device.serial_number) == module.inverter_serial
            ),
            plant_device,
        )
        static = coordinator.modules.data[INFO].get(module.serial) or {}
        known = {
            key: value
            for key, value in (("model", static.get("model")), ("sw_version", static.get("firmware")))
            if value
        }
        coordinator.module_devices[module.serial] = registry.async_get_or_create(
            config_entry_id=plant_entry_id,
            identifiers={(DOMAIN, module_identifier(entry.entry_id, module.serial))},
            name=template.replace("{position}", module.position or str(index)),
            manufacturer="Sigenergy",
            serial_number=module.serial,
            via_device_id=parent,
            **known,
        )


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Forget the state kept for a removed connection.

    The battery module devices belong to the Modbus plant's entry, so Home
    Assistant does not remove them with this one.
    """
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    registry = dr.async_get(hass)
    # Iterate the devices; using them as a mapping is deprecated and stops
    # working in Home Assistant 2027.9.
    for device in list(registry.devices):
        if _module_identifiers(entry, device):
            registry.async_remove_device(device.id)
    # PV string devices belong to Sigenergy ESS: give back their default
    # names where the name is still the one this add-on set.
    await async_restore_pv_names(hass, PvNameStore(hass, entry.entry_id, DOMAIN))
    for key in _ISSUES:
        ir.async_delete_issue(hass, DOMAIN, _issue_id(key, entry))


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a mySigen connection."""
    return await hass.config_entries.async_unload_platforms(
        entry, entry.runtime_data.platforms
    )
