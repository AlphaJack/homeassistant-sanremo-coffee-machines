"""Switches for Sanremo coffee machines."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SanremoConfigEntry
from .const import Capability
from .coordinator import SanremoCoordinator
from .entity import SanremoEntity, SanremoEntityDescription, async_supported
from .models import MachineState
from .profiles import MachineProfile

# Writes are serialised: the WiNET module handles one request at a time.
PARALLEL_UPDATES = 1

WEEKDAY_KEYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


@dataclass(frozen=True, kw_only=True)
class SanremoSwitchDescription(SanremoEntityDescription, SwitchEntityDescription):
    """Describes a Sanremo switch."""

    value_fn: Callable[[MachineState], bool | None]
    set_fn: Callable[[MachineProfile, bool], Awaitable[None]]


SWITCHES: tuple[SanremoSwitchDescription, ...] = (
    SanremoSwitchDescription(
        key="power",
        translation_key="power",
        device_class=SwitchDeviceClass.SWITCH,
        capability=Capability.POWER,
        value_fn=lambda state: state.is_on,
        set_fn=lambda profile, on: profile.async_set_power(on),
    ),
    SanremoSwitchDescription(
        key="steam_booster",
        translation_key="steam_booster",
        capability=Capability.STEAM_BOOSTER,
        value_fn=lambda state: state.steam_booster_enabled,
        set_fn=lambda profile, on: profile.async_set_steam_booster(on),
    ),
    SanremoSwitchDescription(
        key="standby_after_last_coffee",
        translation_key="standby_after_last_coffee",
        entity_category=EntityCategory.CONFIG,
        capability=Capability.STANDBY_AFTER_LAST_COFFEE,
        value_fn=lambda state: state.standby_after_last_coffee,
        set_fn=lambda profile, on: profile.async_set_standby_after_last_coffee(on),
    ),
    SanremoSwitchDescription(
        key="scheduler",
        translation_key="scheduler",
        entity_category=EntityCategory.CONFIG,
        capability=Capability.SCHEDULER,
        value_fn=lambda state: state.scheduler_enabled,
        set_fn=lambda profile, on: profile.async_set_scheduler_enabled(on),
    ),
    SanremoSwitchDescription(
        key="clock_12_hour",
        translation_key="clock_12_hour",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        capability=Capability.CLOCK_FORMAT,
        value_fn=lambda state: state.clock_12_hour,
        set_fn=lambda profile, on: profile.async_set_clock_12_hour(on),
    ),
    SanremoSwitchDescription(
        key="display_fahrenheit",
        translation_key="display_fahrenheit",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        capability=Capability.DISPLAY_TEMPERATURE_UNIT,
        value_fn=lambda state: state.display_temperature_fahrenheit,
        set_fn=lambda profile, on: profile.async_set_display_temperature_fahrenheit(on),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SanremoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switches."""
    coordinator = entry.runtime_data

    entities: list[SwitchEntity] = [
        SanremoSwitch(coordinator, description)
        for description in SWITCHES
        if async_supported(coordinator, description, description.value_fn)
    ]

    # Only where the machine models its schedule per weekday, unlike the YOU.
    if coordinator.supports(Capability.SCHEDULER) and len(
        coordinator.data.schedule_days_enabled
    ) == len(WEEKDAY_KEYS):
        entities.extend(
            SanremoSchedulerDaySwitch(coordinator, day)
            for day in range(len(WEEKDAY_KEYS))
        )

    async_add_entities(entities)


class SanremoSwitch(SanremoEntity, SwitchEntity):
    """A machine setting that is on or off."""

    entity_description: SanremoSwitchDescription

    @property
    def is_on(self) -> bool | None:
        """Return the switch state."""
        return self.entity_description.value_fn(self.machine)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the setting on."""
        await self.coordinator.async_execute(
            self.entity_description.set_fn(self.coordinator.profile, True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the setting off."""
        await self.coordinator.async_execute(
            self.entity_description.set_fn(self.coordinator.profile, False)
        )


class SanremoSchedulerDaySwitch(SanremoEntity, SwitchEntity):
    """Enables or disables one weekday of the schedule."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SanremoCoordinator, day: int) -> None:
        super().__init__(
            coordinator,
            SanremoSwitchDescription(
                key=f"scheduler_{WEEKDAY_KEYS[day]}",
                translation_key=f"scheduler_{WEEKDAY_KEYS[day]}",
                entity_category=EntityCategory.CONFIG,
                value_fn=lambda state, day=day: (
                    state.schedule_days_enabled[day]
                    if len(state.schedule_days_enabled) > day
                    else None
                ),
                set_fn=lambda profile, on, day=day: (
                    profile.async_set_scheduler_day_enabled(day, on)
                ),
            ),
        )
        self._day = day

    @property
    def is_on(self) -> bool | None:
        """Return whether this weekday is enabled."""
        days = self.machine.schedule_days_enabled
        return days[self._day] if len(days) > self._day else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """List this day's configured slots."""
        slots = [slot for slot in self.machine.schedule if slot.day == self._day]
        return {
            "slots": [
                {
                    "slot": slot.index + 1,
                    "enabled": slot.enabled,
                    "on": slot.on_time.strftime("%H:%M"),
                    "off": slot.off_time.strftime("%H:%M"),
                }
                for slot in slots
            ]
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable this weekday."""
        await self.coordinator.async_execute(
            self.coordinator.profile.async_set_scheduler_day_enabled(self._day, True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable this weekday."""
        await self.coordinator.async_execute(
            self.coordinator.profile.async_set_scheduler_day_enabled(self._day, False)
        )
