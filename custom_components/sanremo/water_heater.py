"""A water_heater entity summarising the coffee boiler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.water_heater import (
    STATE_ECO,
    STATE_PERFORMANCE,
    WaterHeaterEntity,
    WaterHeaterEntityDescription,
    WaterHeaterEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, STATE_OFF, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SanremoConfigEntry
from .const import (
    CUBE_BOILER_MAX_C,
    CUBE_BOILER_MIN_C,
    DOMAIN,
    Capability,
    EnergySavingMode,
)
from .coordinator import SanremoCoordinator
from .entity import SanremoEntity, SanremoEntityDescription

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class SanremoWaterHeaterDescription(
    SanremoEntityDescription, WaterHeaterEntityDescription
):
    """Describes the Sanremo boiler entity."""


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SanremoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the boiler entity."""
    coordinator = entry.runtime_data

    # Nothing thermostat-shaped to show without a readable boiler temperature.
    if (
        coordinator.supports(Capability.POWER)
        and coordinator.data.boiler_temperature is not None
    ):
        async_add_entities([SanremoBoiler(coordinator)])


class SanremoBoiler(SanremoEntity, WaterHeaterEntity):
    """The coffee boiler as a water heater."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_name = None

    def __init__(self, coordinator: SanremoCoordinator) -> None:
        super().__init__(
            coordinator,
            SanremoWaterHeaterDescription(key="boiler", capability=Capability.POWER),
        )

        features = WaterHeaterEntityFeature.ON_OFF
        if coordinator.supports(Capability.BOILER_SETPOINT):
            features |= WaterHeaterEntityFeature.TARGET_TEMPERATURE

        operations = [STATE_PERFORMANCE, STATE_OFF]
        if coordinator.supports(Capability.ENERGY_SAVING_MODE):
            features |= WaterHeaterEntityFeature.OPERATION_MODE
            operations.insert(1, STATE_ECO)

        self._attr_supported_features = features
        self._attr_operation_list = operations

    # ── State ─────────────────────────────────────────────────────────────────

    @property
    def current_temperature(self) -> float | None:
        """Return the measured boiler temperature."""
        return self.machine.boiler_temperature

    @property
    def target_temperature(self) -> float | None:
        """Return the active setpoint."""
        state = self.machine
        if (
            state.energy_saving_active
            and state.energy_saving_mode is EnergySavingMode.ECO
            and state.eco_setpoint is not None
        ):
            return state.eco_setpoint
        return state.boiler_setpoint

    @property
    def min_temp(self) -> float:
        """Return the lowest settable setpoint."""
        return self.machine.boiler_setpoint_min or CUBE_BOILER_MIN_C

    @property
    def max_temp(self) -> float:
        """Return the highest settable setpoint."""
        return self.machine.boiler_setpoint_max or CUBE_BOILER_MAX_C

    @property
    def current_operation(self) -> str | None:
        """Return the current operating state."""
        state = self.machine
        if state.is_on is None:
            return None
        if state.is_on:
            return STATE_PERFORMANCE
        if state.energy_saving_mode is EnergySavingMode.ECO:
            return STATE_ECO
        return STATE_OFF

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Surface the brew setpoint separately while ECO is regulating."""
        return {
            "brew_setpoint": self.machine.boiler_setpoint,
            "eco_setpoint": self.machine.eco_setpoint,
            "ready": self.machine.ready,
        }

    # ── Commands ──────────────────────────────────────────────────────────────

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the brew setpoint."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        await self.coordinator.async_execute(
            self.coordinator.profile.async_set_boiler_setpoint(float(temperature))
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Bring the machine up to its brew setpoint."""
        await self.coordinator.async_execute(
            self.coordinator.profile.async_set_power(True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Drop the machine into its low-power state."""
        await self.coordinator.async_execute(
            self.coordinator.profile.async_set_power(False)
        )

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Switch between running, ECO and off."""
        if operation_mode not in (self._attr_operation_list or ()):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_operation_mode",
                translation_placeholders={"mode": operation_mode},
            )

        profile = self.coordinator.profile

        if operation_mode == STATE_PERFORMANCE:
            await self.coordinator.async_execute(profile.async_set_power(True))
            return

        async def _enter_low_power(eco: bool) -> None:
            await profile.async_set_energy_saving_mode(eco)
            await profile.async_set_power(False)

        await self.coordinator.async_execute(
            _enter_low_power(operation_mode == STATE_ECO)
        )
