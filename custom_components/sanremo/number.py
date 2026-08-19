"""Number entities for Sanremo coffee machines."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SanremoConfigEntry
from .const import (
    CUBE_BOILER_MAX_C,
    CUBE_BOILER_MIN_C,
    CUBE_ECO_MAX_C,
    CUBE_ECO_MIN_C,
    CUBE_ENERGY_SAVING_DELAY_MAX_MIN,
    CUBE_ENERGY_SAVING_DELAY_MIN_MIN,
    CUBE_FILTER_MONTHS_MAX,
    CUBE_FILTER_MONTHS_MIN,
    Capability,
)
from .entity import SanremoEntity, SanremoEntityDescription, async_supported
from .models import MachineState
from .profiles import MachineProfile

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class SanremoNumberDescription(SanremoEntityDescription, NumberEntityDescription):
    """Describes a Sanremo number entity."""

    value_fn: Callable[[MachineState], float | None]
    set_fn: Callable[[MachineProfile, float], Awaitable[None]]
    #: Optional per-machine bounds, when the machine reports its own.
    min_fn: Callable[[MachineState], float | None] | None = None
    max_fn: Callable[[MachineState], float | None] | None = None


NUMBERS: tuple[SanremoNumberDescription, ...] = (
    SanremoNumberDescription(
        key="boiler_setpoint",
        translation_key="boiler_setpoint",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=CUBE_BOILER_MIN_C,
        native_max_value=CUBE_BOILER_MAX_C,
        native_step=1,
        mode=NumberMode.BOX,
        capability=Capability.BOILER_SETPOINT,
        value_fn=lambda state: state.boiler_setpoint,
        set_fn=lambda profile, value: profile.async_set_boiler_setpoint(value),
        min_fn=lambda state: state.boiler_setpoint_min,
        max_fn=lambda state: state.boiler_setpoint_max,
    ),
    SanremoNumberDescription(
        key="eco_setpoint",
        translation_key="eco_setpoint",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=CUBE_ECO_MIN_C,
        native_max_value=CUBE_ECO_MAX_C,
        # Whole degrees: the vendor's control has no fractional step.
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        capability=Capability.ECO_SETPOINT,
        value_fn=lambda state: state.eco_setpoint,
        set_fn=lambda profile, value: profile.async_set_eco_setpoint(value),
    ),
    SanremoNumberDescription(
        key="energy_saving_delay",
        translation_key="energy_saving_delay",
        device_class=NumberDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        native_min_value=CUBE_ENERGY_SAVING_DELAY_MIN_MIN,
        native_max_value=CUBE_ENERGY_SAVING_DELAY_MAX_MIN,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        capability=Capability.ENERGY_SAVING_DELAY,
        # Stored in seconds on the wire; minutes is what the vendor UI shows.
        value_fn=lambda state: (
            None
            if state.energy_saving_delay is None
            else state.energy_saving_delay / 60
        ),
        set_fn=lambda profile, value: profile.async_set_energy_saving_delay(
            int(value * 60)
        ),
    ),
    SanremoNumberDescription(
        key="filter_interval",
        translation_key="filter_interval",
        native_unit_of_measurement="mo",
        native_min_value=CUBE_FILTER_MONTHS_MIN,
        native_max_value=CUBE_FILTER_MONTHS_MAX,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        capability=Capability.FILTER_MONITORING,
        value_fn=lambda state: state.filter_interval_months,
        set_fn=lambda profile, value: profile.async_set_filter_interval_months(
            int(value)
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SanremoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the number entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        SanremoNumber(coordinator, description)
        for description in NUMBERS
        if async_supported(coordinator, description, description.value_fn)
    )


class SanremoNumber(SanremoEntity, NumberEntity):
    """A machine setting expressed as a number."""

    entity_description: SanremoNumberDescription

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        return self.entity_description.value_fn(self.machine)

    @property
    def native_min_value(self) -> float:
        """Return the lower bound, preferring what the machine reports."""
        if (
            self.entity_description.min_fn is not None
            and (value := self.entity_description.min_fn(self.machine)) is not None
        ):
            return value
        return self.entity_description.native_min_value or 0

    @property
    def native_max_value(self) -> float:
        """Return the upper bound, preferring what the machine reports."""
        if (
            self.entity_description.max_fn is not None
            and (value := self.entity_description.max_fn(self.machine)) is not None
        ):
            return value
        return self.entity_description.native_max_value or 100

    async def async_set_native_value(self, value: float) -> None:
        """Write a new value to the machine."""
        await self.coordinator.async_execute(
            self.entity_description.set_fn(self.coordinator.profile, value)
        )
