"""Binary sensors for Sanremo coffee machines."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SanremoConfigEntry
from .coordinator import SanremoCoordinator
from .entity import SanremoEntity, SanremoEntityDescription, async_supported
from .models import MachineState

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SanremoBinarySensorDescription(
    SanremoEntityDescription, BinarySensorEntityDescription
):
    """Describes a Sanremo binary sensor."""

    value_fn: Callable[[MachineState], bool | None]


#: How far the machine's clock may differ from Home Assistant before it matters.
CLOCK_DRIFT_TOLERANCE = timedelta(minutes=5)


def _clock_drifted(state: MachineState) -> bool | None:
    """Return True if the machine's clock disagrees with Home Assistant's."""
    if state.machine_time is None:
        return None
    return abs(state.machine_time - dt_util.now()) > CLOCK_DRIFT_TOLERANCE


BINARY_SENSORS: tuple[SanremoBinarySensorDescription, ...] = (
    SanremoBinarySensorDescription(
        key="ready",
        translation_key="ready",
        value_fn=lambda state: state.ready,
    ),
    SanremoBinarySensorDescription(
        key="brewing",
        translation_key="brewing",
        device_class=BinarySensorDeviceClass.RUNNING,
        available_fn=lambda coordinator: coordinator.push_alive,
        # Only meaningful where live flow telemetry exists (the YOU's WebSocket).
        supported_fn=lambda state: state.realtime_flow is not None,
        value_fn=lambda state: (state.realtime_flow or 0) > 0,
    ),
    SanremoBinarySensorDescription(
        key="tank_level_ok",
        translation_key="tank_level_ok",
        device_class=BinarySensorDeviceClass.PROBLEM,
        # Inverted on purpose: "problem" is true when the tank is *not* ok.
        value_fn=lambda state: (
            None if state.tank_level_ok is None else not state.tank_level_ok
        ),
    ),
    SanremoBinarySensorDescription(
        key="tank_level_low_warning",
        translation_key="tank_level_low_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda state: state.tank_level_low_warning,
    ),
    SanremoBinarySensorDescription(
        key="boiler_level_ok",
        translation_key="boiler_level_ok",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda state: (
            None if state.boiler_level_ok is None else not state.boiler_level_ok
        ),
    ),
    SanremoBinarySensorDescription(
        key="filter_change_required",
        translation_key="filter_change_required",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda state: state.filter_change_required,
    ),
    SanremoBinarySensorDescription(
        key="steam_booster_heating",
        translation_key="steam_booster_heating",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=BinarySensorDeviceClass.HEAT,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.steam_booster_heating,
    ),
    SanremoBinarySensorDescription(
        key="steam_booster_ready",
        translation_key="steam_booster_ready",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.steam_booster_ready,
    ),
    SanremoBinarySensorDescription(
        key="pre_infusion",
        translation_key="pre_infusion",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.pre_infusion_enabled,
    ),
    SanremoBinarySensorDescription(
        key="firmware_update_available",
        translation_key="firmware_update_available",
        device_class=BinarySensorDeviceClass.UPDATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.firmware_update_available,
    ),
    SanremoBinarySensorDescription(
        key="cloud_connected",
        translation_key="cloud_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.cloud_connected,
    ),
    SanremoBinarySensorDescription(
        key="clock_drift",
        translation_key="clock_drift",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        # The on-board scheduler runs off this clock, so drift means wrong switch times.
        value_fn=_clock_drifted,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SanremoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    coordinator = entry.runtime_data

    entities: list[BinarySensorEntity] = [
        SanremoBinarySensor(coordinator, description)
        for description in BINARY_SENSORS
        if async_supported(coordinator, description, description.value_fn)
    ]

    # Alarm sets differ per profile, so build them from the profile's own dict.
    if coordinator.data.alarms:
        entities.append(SanremoAlarmSummary(coordinator))
        entities.extend(
            SanremoAlarm(coordinator, slug) for slug in sorted(coordinator.data.alarms)
        )

    async_add_entities(entities)


class SanremoBinarySensor(SanremoEntity, BinarySensorEntity):
    """A boolean read from the machine."""

    entity_description: SanremoBinarySensorDescription

    @property
    def is_on(self) -> bool | None:
        """Return the sensor state."""
        return self.entity_description.value_fn(self.machine)


class SanremoAlarmSummary(SanremoEntity, BinarySensorEntity):
    """True while any alarm is active, with the active list as an attribute."""

    def __init__(self, coordinator: SanremoCoordinator) -> None:
        super().__init__(
            coordinator,
            SanremoBinarySensorDescription(
                key="alarm",
                translation_key="alarm",
                device_class=BinarySensorDeviceClass.PROBLEM,
                value_fn=lambda state: state.has_active_alarm,
            ),
        )

    @property
    def is_on(self) -> bool:
        """Return True if any alarm is active."""
        return self.machine.has_active_alarm

    @property
    def extra_state_attributes(self) -> dict[str, list[str]]:
        """Expose the active alarm slugs for automations and templates."""
        return {"active_alarms": self.machine.active_alarms}


class SanremoAlarm(SanremoEntity, BinarySensorEntity):
    """One specific alarm bit. The aggregate sensor covers day-to-day use."""

    def __init__(self, coordinator: SanremoCoordinator, slug: str) -> None:
        super().__init__(
            coordinator,
            SanremoBinarySensorDescription(
                key=f"alarm_{slug}",
                translation_key=f"alarm_{slug}",
                device_class=BinarySensorDeviceClass.PROBLEM,
                entity_category=EntityCategory.DIAGNOSTIC,
                entity_registry_enabled_default=False,
                value_fn=lambda state, slug=slug: state.alarms.get(slug),
            ),
        )
        self._slug = slug

    @property
    def is_on(self) -> bool | None:
        """Return True while this alarm is active."""
        return self.machine.alarms.get(self._slug)
