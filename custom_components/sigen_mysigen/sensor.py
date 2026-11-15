"""Sensors for the mySigen cloud add-on."""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfFrequency,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import OVERRIDE_MODES
from .const import DOMAIN
from .control import RESULTS
from .coordinator import MySigenCoordinator
from .entity import MySigenEntity

# Reading -> (device class, unit, decimals shown).
GATEWAY_SENSORS: dict[str, tuple[SensorDeviceClass, str, int]] = {
    **{
        f"voltage_{phase}": (SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT, 1)
        for phase in "abc"
    },
    **{
        f"current_{phase}": (SensorDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE, 2)
        for phase in "abc"
    },
    "frequency": (SensorDeviceClass.FREQUENCY, UnitOfFrequency.HERTZ, 2),
}
_GATEWAY_CONTACTOR = "gateway_grid_contactor"


def _gateway_key(reading: str) -> str:
    return f"gateway_grid_{reading}"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the override sensors, the update time and the Gateway readings."""
    coordinator: MySigenCoordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        BatteryOverrideMode(coordinator, "battery_override_mode"),
        BatteryOverrideEnd(coordinator, "battery_override_end"),
        LastUpdate(coordinator, "last_update"),
        LastCommand(coordinator, "battery_override_command"),
    ]

    # Add Gateway sensors when mySigen gave Gateway readings now, or did on an
    # earlier start. A failed read at startup must not make them disappear.
    registry = er.async_get(hass)
    known_before = any(
        registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}")
        for key in (*map(_gateway_key, GATEWAY_SENSORS), _GATEWAY_CONTACTOR)
    )
    def gateway_entities() -> list[SensorEntity]:
        return [
            *(GatewayReading(coordinator, reading) for reading in GATEWAY_SENSORS),
            GatewayContactor(coordinator, _GATEWAY_CONTACTOR),
        ]

    if coordinator.has_gateway or known_before:
        async_add_entities([*entities, *gateway_entities()])
        return
    async_add_entities(entities)

    # The first Gateway read may have failed. Add the sensors as soon as a
    # later poll finds the Gateway, without waiting for a reload.
    added = False

    @callback
    def add_when_detected() -> None:
        nonlocal added
        if not added and coordinator.has_gateway:
            added = True
            async_add_entities(gateway_entities())

    entry.async_on_unload(coordinator.async_add_listener(add_when_detected))


class BatteryOverrideMode(MySigenEntity, SensorEntity):
    """The mode of the running override; unknown when none is running."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(OVERRIDE_MODES.values())

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.override.mode


class BatteryOverrideEnd(MySigenEntity, SensorEntity):
    """When the running override ends; unknown when none is running."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.data.override.ends_at


class LastUpdate(MySigenEntity, SensorEntity):
    """When mySigen last answered, so old cloud values can be recognised."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        # Stays readable during an outage; that is when it matters.
        return True

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.last_success


class LastCommand(MySigenEntity, SensorEntity):
    """How the last start or cancel sent from Home Assistant ended."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(RESULTS)

    @property
    def available(self) -> bool:
        # An unknown outcome must stay visible during a cloud outage.
        return True

    @property
    def native_value(self) -> str | None:
        command = self.coordinator.last_command
        return command["result"] if command else None

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        command = self.coordinator.last_command
        return {"action": command["action"], "at": command["at"]} if command else None


class _GatewayEntity(MySigenEntity, SensorEntity):
    """Unavailable while the Gateway read fails, even if the override works."""

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.gateway is not None


class GatewayReading(_GatewayEntity):
    """One grid-side measurement of the Gateway."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: MySigenCoordinator, reading: str) -> None:
        super().__init__(coordinator, _gateway_key(reading))
        self._reading = reading
        device_class, unit, decimals = GATEWAY_SENSORS[reading]
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_suggested_display_precision = decimals

    @property
    def native_value(self) -> float | None:
        # A reading mySigen left out is unknown; a real zero stays zero.
        return self.coordinator.data.gateway.values.get(self._reading)


class GatewayContactor(_GatewayEntity):
    """The grid-side contactor state, as mySigen words it."""

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.gateway.contactor
