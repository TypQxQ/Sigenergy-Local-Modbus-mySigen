"""Sensors for the mySigen cloud add-on."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import DC_CHARGE_MODES, OVERRIDE_MODES, BatteryModule
from .const import DOMAIN
from .control import RESULTS
from .coordinator import MySigenCoordinator
from .entity import MySigenEntity
from .modules import INFO, PACK, READINGS

_VOLT = (SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT, 1)
_AMPERE = (SensorDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE, 2)


@dataclass(frozen=True)
class _Measurement:
    """A numeric Gateway reading shown as a sensor."""

    reading: str
    kind: tuple[SensorDeviceClass, str, int]
    enabled: bool = True


@dataclass(frozen=True)
class _Text:
    """A text the Gateway reports, shown as mySigen words it."""

    reading: str
    enabled: bool = True
    diagnostic: bool = False


# Entity key -> what it shows. Keys are part of the unique IDs.
GATEWAY_MEASUREMENTS: dict[str, _Measurement] = {
    **{f"gateway_grid_voltage_{p}": _Measurement(f"voltage_{p}", _VOLT) for p in "abc"},
    **{f"gateway_grid_current_{p}": _Measurement(f"current_{p}", _AMPERE) for p in "abc"},
    # Short peaks and dips between two polls. Off until the user wants them.
    **{
        f"gateway_grid_voltage_max_{p}": _Measurement(f"voltage_max_{p}", _VOLT, False)
        for p in "abc"
    },
    **{
        f"gateway_grid_voltage_min_{p}": _Measurement(f"voltage_min_{p}", _VOLT, False)
        for p in "abc"
    },
    # mySigen reports generator values on every Gateway, also without a
    # generator, so these start disabled.
    **{
        f"generator_voltage_{p}": _Measurement(f"generator_voltage_{p}", _VOLT, False)
        for p in "abc"
    },
    **{
        f"generator_current_{p}": _Measurement(f"generator_current_{p}", _AMPERE, False)
        for p in "abc"
    },
    **{
        f"generator_power_{p}": _Measurement(
            f"generator_power_{p}", (SensorDeviceClass.POWER, UnitOfPower.KILO_WATT, 3), False
        )
        for p in "abc"
    },
    "generator_power": _Measurement(
        "generator_power", (SensorDeviceClass.POWER, UnitOfPower.KILO_WATT, 3), False
    ),
    "generator_frequency": _Measurement(
        "generator_frequency", (SensorDeviceClass.FREQUENCY, UnitOfFrequency.HERTZ, 2), False
    ),
}
GATEWAY_TEXTS: dict[str, _Text] = {
    "gateway_grid_phase_sequence": _Text("phase_sequence", enabled=False, diagnostic=True),
    "gateway_firmware": _Text("firmware", diagnostic=True),
    "gateway_model": _Text("model", diagnostic=True),
    "generator_contactor": _Text("generator_contactor", enabled=False),
}


@dataclass(frozen=True)
class _ModuleReading:
    """One reading of a battery module."""

    reading: str
    # Which of the module's three reads it comes from.
    source: str
    # (device class, unit, decimals); None for a text.
    kind: tuple[SensorDeviceClass, str, int] | None
    state_class: SensorStateClass | None = SensorStateClass.MEASUREMENT
    enabled: bool = True
    diagnostic: bool = False


# Entity key -> what it shows. The reading is part of the unique IDs.
MODULE_READINGS: dict[str, _ModuleReading] = {
    "battery_module_soc": _ModuleReading(
        "soc", READINGS, (SensorDeviceClass.BATTERY, PERCENTAGE, 1)
    ),
    "battery_module_discharged": _ModuleReading(
        "discharged", PACK,
        (SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR, 0),
        SensorStateClass.TOTAL_INCREASING,
    ),
    "battery_module_cell_temperature": _ModuleReading(
        "cell_temperature", PACK,
        (SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, 1),
    ),
    "battery_module_cell_voltage": _ModuleReading(
        "cell_voltage", PACK, (SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT, 3)
    ),
    "battery_module_power": _ModuleReading(
        "power", READINGS, (SensorDeviceClass.POWER, UnitOfPower.KILO_WATT, 3),
        enabled=False,
    ),
    "battery_module_pack_voltage": _ModuleReading(
        "pack_voltage", READINGS,
        (SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT, 1), enabled=False,
    ),
    "battery_module_heating": _ModuleReading(
        "heating", READINGS, None, None, enabled=False, diagnostic=True
    ),
    "battery_module_capacity": _ModuleReading(
        "rated_capacity", INFO,
        (SensorDeviceClass.ENERGY_STORAGE, UnitOfEnergy.KILO_WATT_HOUR, 2),
        None, diagnostic=True,
    ),
}

# Removed because the Modbus integration already provides them.
_REMOVED = ("gateway_grid_frequency", "gateway_grid_contactor")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the override sensors, the update time and the Gateway readings."""
    coordinator: MySigenCoordinator = entry.runtime_data
    registry = er.async_get(hass)
    for key in _REMOVED:
        if entity_id := registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_{key}"
        ):
            registry.async_remove(entity_id)

    entities: list[SensorEntity] = [
        BatteryOverrideMode(coordinator, "battery_override_mode"),
        BatteryOverrideEnd(coordinator, "battery_override_end"),
        LastUpdate(coordinator, "last_update"),
        LastCommand(coordinator, "battery_override_command"),
    ]
    if coordinator.has_dc_charger:
        entities += [
            DcChargeModeInUse(coordinator, "dc_charger_mode"),
            DcStopLevel(coordinator, "dc_charger_stop_level"),
        ]
    for module in coordinator.modules.modules:
        device = coordinator.module_devices.get(module.serial)
        if device is None:
            continue
        entities += [
            BatteryModuleSensor(coordinator, key, module, device) for key in MODULE_READINGS
        ]

    def gateway_entities() -> list[SensorEntity]:
        return [
            *(GatewayMeasurement(coordinator, key) for key in GATEWAY_MEASUREMENTS),
            *(GatewayText(coordinator, key) for key in GATEWAY_TEXTS),
        ]

    # Add Gateway sensors when mySigen gave Gateway readings now, or did on an
    # earlier start. A failed read at startup must not make them disappear.
    if coordinator.has_gateway or coordinator.gateway_seen_before:
        async_add_entities([*entities, *gateway_entities()])
        return
    async_add_entities(entities)

    # The first Gateway read may have had no answer. Add the sensors as soon
    # as a later poll finds the Gateway, without waiting for a reload.
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


class GatewayMeasurement(_GatewayEntity):
    """One numeric reading of the Gateway."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: MySigenCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        spec = GATEWAY_MEASUREMENTS[key]
        self._reading = spec.reading
        self._attr_device_class, unit, decimals = spec.kind
        self._attr_native_unit_of_measurement = unit
        self._attr_suggested_display_precision = decimals
        self._attr_entity_registry_enabled_default = spec.enabled

    @property
    def native_value(self) -> float | None:
        # A reading mySigen left out is unknown; a real zero stays zero.
        return self.coordinator.data.gateway.values.get(self._reading)


class GatewayText(_GatewayEntity):
    """One text the Gateway reports."""

    def __init__(self, coordinator: MySigenCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        spec = GATEWAY_TEXTS[key]
        self._reading = spec.reading
        self._attr_entity_registry_enabled_default = spec.enabled
        if spec.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.gateway.texts.get(self._reading)


class DcChargeModeInUse(MySigenEntity, SensorEntity):
    """How the DC charger decides when to charge."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(DC_CHARGE_MODES)

    @property
    def native_value(self) -> str | None:
        mode = self.coordinator.dc_charge_mode
        return mode.mode if mode is not None else None


class DcStopLevel(MySigenEntity, SensorEntity):
    """The vehicle charge level at which the DC charger stops charging."""

    _attr_native_unit_of_measurement = PERCENTAGE

    @property
    def native_value(self) -> int | None:
        setting = self.coordinator.dc_charge_setting
        return setting.stop_level if setting is not None else None


class BatteryModuleSensor(MySigenEntity, SensorEntity):
    """One reading of one battery module, on that module's own device."""

    def __init__(
        self,
        coordinator: MySigenCoordinator,
        key: str,
        module: BatteryModule,
        device: dr.DeviceEntry,
    ) -> None:
        spec = MODULE_READINGS[key]
        super().__init__(
            coordinator, key, unique_key=f"battery_{module.serial}_{spec.reading}"
        )
        # The module's own device, registered to the Modbus plant's entry.
        self.device_entry = device
        self._serial = module.serial
        self._spec = spec
        if spec.kind is not None:
            self._attr_device_class, unit, decimals = spec.kind
            self._attr_native_unit_of_measurement = unit
            self._attr_suggested_display_precision = decimals
        self._attr_state_class = spec.state_class
        self._attr_entity_registry_enabled_default = spec.enabled
        if spec.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.modules.available(
            self._serial, self._spec.source
        )

    @property
    def native_value(self) -> float | str | None:
        return self.coordinator.modules.value(self._serial, self._spec.reading)
