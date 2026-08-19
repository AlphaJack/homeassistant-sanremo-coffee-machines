"""Shared entity plumbing for Sanremo coffee machines."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceInfo,
    format_mac,
)
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, Capability
from .coordinator import SanremoCoordinator
from .models import MachineState


@dataclass(frozen=True, kw_only=True)
class SanremoEntityDescription(EntityDescription):
    """Common extras every Sanremo entity description carries."""

    capability: Capability | None = None
    supported_fn: Callable[[MachineState], bool] | None = None
    #: Extra availability condition, on top of the coordinator's own.
    available_fn: Callable[[SanremoCoordinator], bool] | None = None


class SanremoEntity(CoordinatorEntity[SanremoCoordinator]):
    """Base class binding an entity to one machine."""

    _attr_has_entity_name = True
    entity_description: SanremoEntityDescription

    def __init__(
        self,
        coordinator: SanremoCoordinator,
        description: SanremoEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.device_id}_{description.key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device this entity belongs to."""
        device = self.coordinator.device
        info = DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device_id)},
            manufacturer=MANUFACTURER,
            model=device.model,
            name=device.name,
            sw_version=device.wifi_firmware,
            hw_version=device.board_firmware,
            configuration_url=self.coordinator.client.base_url,
        )
        if device.mac:
            info["connections"] = {(CONNECTION_NETWORK_MAC, format_mac(device.mac))}
        return info

    @property
    def machine(self) -> MachineState:
        """Return the current machine state."""
        return self.coordinator.data

    @property
    def available(self) -> bool:
        """Return whether the entity has trustworthy data behind it."""
        if not super().available:
            return False
        if (extra := self.entity_description.available_fn) is not None:
            return extra(self.coordinator)
        return True


def async_supported(
    coordinator: SanremoCoordinator,
    description: SanremoEntityDescription,
    value_fn: Callable[[MachineState], Any] | None = None,
) -> bool:
    """Return True if ``description`` fits the machine behind ``coordinator``."""
    if description.capability is not None and not coordinator.supports(
        description.capability
    ):
        return False

    state = coordinator.data
    if description.supported_fn is not None:
        return description.supported_fn(state)

    # Read-only entities are supported when the machine reports a value.
    if value_fn is not None:
        return value_fn(state) is not None

    return True


def build_entities[T](
    coordinator: SanremoCoordinator,
    descriptions: Iterable[SanremoEntityDescription],
    factory: Callable[[SanremoCoordinator, Any], T],
    value_getter: Callable[[Any], Callable[[MachineState], Any] | None] | None = None,
) -> list[T]:
    """Instantiate every description the machine supports."""
    entities: list[T] = []
    for description in descriptions:
        value_fn = value_getter(description) if value_getter else None
        if async_supported(coordinator, description, value_fn):
            entities.append(factory(coordinator, description))
    return entities
