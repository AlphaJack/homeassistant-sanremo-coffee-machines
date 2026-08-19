"""Sensors for Sanremo coffee machines."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import SanremoConfigEntry
from .const import GENERIC_PROFILE_ID, WifiStatus
from .entity import SanremoEntity, SanremoEntityDescription, async_supported
from .models import MachineState

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SanremoSensorDescription(SanremoEntityDescription, SensorEntityDescription):
    """Describes a Sanremo sensor."""

    value_fn: Callable[[MachineState], StateType | datetime]


def _ml_to_litres(millilitres: int | None) -> float | None:
    """Convert to litres: mL is not a valid unit for the water device class."""
    return None if millilitres is None else millilitres / 1000


def _filter_life_percent(state: MachineState) -> StateType:
    """Return the filter's remaining life as a percentage."""
    if not state.filter_interval_days or state.filter_days_remaining is None:
        return None
    percent = state.filter_days_remaining / state.filter_interval_days * 100
    return min(100.0, max(0.0, percent))


SENSORS: tuple[SanremoSensorDescription, ...] = (
    # ── Temperatures ──────────────────────────────────────────────────────────
    SanremoSensorDescription(
        key="boiler_temperature",
        translation_key="boiler_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda state: state.boiler_temperature,
    ),
    SanremoSensorDescription(
        key="group_temperature",
        translation_key="group_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda state: state.group_temperature,
    ),
    SanremoSensorDescription(
        key="steam_temperature",
        translation_key="steam_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda state: state.steam_temperature,
    ),
    # ── Pressures ─────────────────────────────────────────────────────────────
    SanremoSensorDescription(
        key="pump_pressure",
        translation_key="pump_pressure",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.BAR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda state: state.pump_pressure,
    ),
    SanremoSensorDescription(
        key="steam_pressure",
        translation_key="steam_pressure",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.BAR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda state: state.steam_pressure,
    ),
    # ── Brewing ───────────────────────────────────────────────────────────────
    SanremoSensorDescription(
        key="last_shot_time",
        translation_key="last_shot_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_display_precision=1,
        value_fn=lambda state: state.last_shot_time,
    ),
    SanremoSensorDescription(
        key="realtime_flow",
        translation_key="realtime_flow",
        available_fn=lambda coordinator: coordinator.push_alive,
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.MILLILITERS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda state: state.realtime_flow,
    ),
    SanremoSensorDescription(
        key="shot_volume",
        translation_key="shot_volume",
        available_fn=lambda coordinator: coordinator.push_alive,
        # No device class: VOLUME forbids MEASUREMENT, and this reading is live.
        native_unit_of_measurement=UnitOfVolume.MILLILITERS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state: state.shot_volume,
    ),
    # ── Coffee counters: periodic ones reset, so only the total accumulates ───
    SanremoSensorDescription(
        key="coffees_today",
        translation_key="coffees_today",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state: state.coffees_today,
    ),
    SanremoSensorDescription(
        key="coffees_week",
        translation_key="coffees_week",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state: state.coffees_week,
    ),
    SanremoSensorDescription(
        key="coffees_month",
        translation_key="coffees_month",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state: state.coffees_month,
    ),
    SanremoSensorDescription(
        key="coffees_year",
        translation_key="coffees_year",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state: state.coffees_year,
    ),
    SanremoSensorDescription(
        key="coffees_total",
        translation_key="coffees_total",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda state: state.coffees_total,
    ),
    # ── Water counters ────────────────────────────────────────────────────────
    SanremoSensorDescription(
        key="water_dispensed",
        translation_key="water_dispensed",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=lambda state: _ml_to_litres(state.water_dispensed_ml),
    ),
    SanremoSensorDescription(
        key="water_to_boiler",
        translation_key="water_to_boiler",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=lambda state: _ml_to_litres(state.water_to_boiler_ml),
    ),
    SanremoSensorDescription(
        key="water_total",
        translation_key="water_total",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=lambda state: _ml_to_litres(state.water_total_ml),
    ),
    # ── Water filter ──────────────────────────────────────────────────────────
    SanremoSensorDescription(
        key="filter_days_remaining",
        translation_key="filter_days_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        # A machine with monitoring off also reports zero, so require an interval.
        supported_fn=lambda state: bool(state.filter_interval_days),
        value_fn=lambda state: state.filter_days_remaining,
    ),
    SanremoSensorDescription(
        key="filter_life",
        translation_key="filter_life",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        supported_fn=lambda state: bool(state.filter_interval_days),
        value_fn=_filter_life_percent,
    ),
    SanremoSensorDescription(
        key="estimated_brew_temperature",
        translation_key="estimated_brew_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda state: state.estimated_brew_temperature,
    ),
    # ── Energy saving ─────────────────────────────────────────────────────────
    SanremoSensorDescription(
        key="energy_saving_countdown",
        translation_key="energy_saving_countdown",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.energy_saving_countdown,
    ),
    # ── Diagnostics ───────────────────────────────────────────────────────────
    SanremoSensorDescription(
        key="wifi_signal",
        translation_key="wifi_signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dBm",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.rssi,
    ),
    SanremoSensorDescription(
        key="wifi_status",
        translation_key="wifi_status",
        device_class=SensorDeviceClass.ENUM,
        options=[status.value for status in WifiStatus],
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.wifi_status,
    ),
    SanremoSensorDescription(
        key="machine_time",
        translation_key="machine_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.machine_time,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SanremoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        SanremoSensor(coordinator, description)
        for description in SENSORS
        if async_supported(coordinator, description, description.value_fn)
    )
    # Only useful on an unmapped machine; diagnostics already carries them.
    if coordinator.device.profile == GENERIC_PROFILE_ID:
        async_add_entities(
            SanremoRegisterSensor(coordinator, key, index)
            for key, registers in coordinator.data.raw_registers.items()
            for index in range(len(registers))
        )


class SanremoSensor(SanremoEntity, SensorEntity):
    """A value read from the machine."""

    entity_description: SanremoSensorDescription

    @property
    def native_value(self) -> StateType | datetime:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.machine)


class SanremoRegisterSensor(SanremoEntity, SensorEntity):
    """One raw register, exposed for the values nobody has mapped yet."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, source: str, index: int) -> None:
        description = SanremoSensorDescription(
            key=f"register_{source}_{index}",
            translation_key="raw_register",
            translation_placeholders={"source": source, "index": str(index)},
            value_fn=lambda state: None,
        )
        super().__init__(coordinator, description)
        self._source = source
        self._index = index

    @property
    def native_value(self) -> StateType:
        """Return the register's current value."""
        registers = self.machine.raw_registers.get(self._source) or []
        if len(registers) <= self._index:
            return None
        return registers[self._index]
