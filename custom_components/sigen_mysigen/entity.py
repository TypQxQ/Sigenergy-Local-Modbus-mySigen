"""Base entity that places mySigen values on the Modbus plant device."""
from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_PLANT_ENTRY_ID, DOMAIN, MODBUS_DOMAIN
from .coordinator import MySigenCoordinator


class MySigenEntity(CoordinatorEntity[MySigenCoordinator]):
    """A mySigen value shown on the existing plant device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MySigenCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        # Link to the device the Modbus integration owns, the way helper
        # entities do. Passing device_info with its identifier instead makes
        # Home Assistant create a second device for this config entry.
        plant_entry_id = entry.data[CONF_PLANT_ENTRY_ID]
        identifier = (MODBUS_DOMAIN, f"{plant_entry_id}_plant")
        self.device_entry = next(
            (
                device
                for device in dr.async_entries_for_config_entry(
                    dr.async_get(coordinator.hass), plant_entry_id
                )
                if identifier in device.identifiers
            ),
            None,
        )

    async def async_prepare_to_add_to_hass(self) -> None:
        """Use the plant's entity name language, the way the Modbus entities do."""
        await super().async_prepare_to_add_to_hass()
        translations = self.coordinator.entity_name_translations
        if not translations:
            # Automatic: Home Assistant names the entity in its own language.
            return
        name = translations.get(
            f"component.{DOMAIN}.entity.{self.platform_data.domain}"
            f".{self.translation_key}.name"
        )
        if name is None:
            return
        # Keep Home Assistant's normal suggested ID. Setting the name alone
        # would also change how the entity ID is generated.
        if not hasattr(self, "_language_suggested_object_id"):
            self._language_suggested_object_id = super().suggested_object_id
        self._attr_name = name

    @property
    def suggested_object_id(self) -> str | None:
        """Keep the chosen display language out of entity ID generation."""
        if hasattr(self, "_language_suggested_object_id"):
            return self._language_suggested_object_id
        return super().suggested_object_id
