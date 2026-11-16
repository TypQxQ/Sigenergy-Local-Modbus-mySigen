"""The override the Start battery override button will start."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import CONTROL_MODES
from .const import CONF_OVERRIDE_MODE
from .entity import MySigenEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the dropdown. Only set up while control is allowed."""
    async_add_entities(
        [OverrideToStart(entry.runtime_data, "battery_override_to_start")]
    )


class OverrideToStart(MySigenEntity, SelectEntity):
    """Stored in Home Assistant. Changing it sends nothing to the plant.

    The options are fixed keys; what the user reads comes from translations.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(CONTROL_MODES)

    @property
    def available(self) -> bool:
        # A local setting; it does not depend on mySigen being reachable.
        return True

    @property
    def current_option(self) -> str:
        return self.coordinator.override_mode

    async def async_select_option(self, option: str) -> None:
        self.coordinator.set_option(CONF_OVERRIDE_MODE, option)
        self.async_write_ha_state()
