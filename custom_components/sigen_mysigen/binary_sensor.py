"""Binary sensors for the mySigen cloud add-on."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .entity import MySigenEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the override state."""
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [
        BatteryOverrideActive(coordinator, "battery_override"),
        PeakShavingOn(coordinator, "peak_shaving"),
    ]
    if coordinator.has_dc_charger:
        entities.append(V2xAllowed(coordinator, "dc_charger_v2x"))
    if coordinator.has_ac_charger:
        entities.append(AcBatteryBoost(coordinator, "ac_charger_battery_boost"))
    if coordinator.session.storm_watch_shown:
        entities.append(StormWatchActive(coordinator, "storm_watch"))
    async_add_entities(entities)


class BatteryOverrideActive(MySigenEntity, BinarySensorEntity):
    """On while a temporary battery override is running."""

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.override.enabled


class PeakShavingOn(MySigenEntity, BinarySensorEntity):
    """On when peak shaving is switched on in mySigen. Read-only.

    Shown with control off too, so a schedule set in the app can be seen.
    The attributes give the reserved battery level and each time slot.
    """

    @property
    def available(self) -> bool:
        peak = self.coordinator.peak_shaving
        return super().available and peak is not None and peak.enabled is not None

    @property
    def is_on(self) -> bool | None:
        peak = self.coordinator.peak_shaving
        return peak.enabled if peak is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        peak = self.coordinator.peak_shaving
        if peak is None:
            return None
        slots = peak.slot_summaries()
        return {
            "reserved_soc": peak.reserved_soc,
            "time_slots": len(slots),
            "slots": slots,
        }


class V2xAllowed(MySigenEntity, BinarySensorEntity):
    """On when the car may supply power through the DC charger."""

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.v2x_enabled


class StormWatchActive(MySigenEntity, BinarySensorEntity):
    """On while Storm Watch lists a weather alert that has not ended.

    Storm Watch then charges the battery to full and holds it there. Only
    added where mySigen shows Storm Watch for the plant.
    """

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.storm_watch is not None

    @property
    def is_on(self) -> bool | None:
        watch = self.coordinator.storm_watch
        return bool(watch.current(dt_util.utcnow())) if watch is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        watch = self.coordinator.storm_watch
        if watch is None:
            return None
        alerts = watch.current(dt_util.utcnow())
        # The alert that started first; the others are counted.
        first = min(
            alerts,
            key=lambda alert: alert.charging_start or alert.onset or dt_util.utcnow(),
            default=None,
        )

        def when(value):
            return value.isoformat() if value is not None else None

        return {
            "storm_watch_enabled": watch.enabled,
            "alerts": len(alerts),
            "weather": first.weather if first else None,
            "charging_start": when(first.charging_start) if first else None,
            "onset": when(first.onset) if first else None,
            "end": when(first.end) if first else None,
            "charged_full": watch.charged_full,
        }


class AcBatteryBoost(MySigenEntity, BinarySensorEntity):
    """On when the home battery helps charge the car (battery boost)."""

    @property
    def is_on(self) -> bool | None:
        mode = self.coordinator.ac_charge_mode
        return mode.boost if mode is not None else None
