"""Text entities for Sanremo coffee machines."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.text import TextEntity, TextEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SanremoConfigEntry
from .const import Capability
from .coordinator import SanremoCoordinator
from .entity import SanremoEntity, SanremoEntityDescription

PARALLEL_UPDATES = 1

#: The vendor's name field is a short free-text label shown in its device list.
NAME_MAX_LENGTH = 32


@dataclass(frozen=True, kw_only=True)
class SanremoTextDescription(SanremoEntityDescription, TextEntityDescription):
    """Describes a Sanremo text entity."""


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SanremoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the text entities."""
    coordinator = entry.runtime_data
    if coordinator.supports(Capability.RENAME):
        async_add_entities([SanremoMachineName(coordinator)])


class SanremoMachineName(SanremoEntity, TextEntity):
    """The machine's own name, as shown by the vendor app and its web page."""

    _attr_native_min = 1
    _attr_native_max = NAME_MAX_LENGTH

    def __init__(self, coordinator: SanremoCoordinator) -> None:
        super().__init__(
            coordinator,
            SanremoTextDescription(
                key="machine_name",
                translation_key="machine_name",
                entity_category=EntityCategory.CONFIG,
                capability=Capability.RENAME,
            ),
        )

    @property
    def native_value(self) -> str | None:
        """Return the machine's current name."""
        return self.machine.name or self.coordinator.device.name

    async def async_set_value(self, value: str) -> None:
        """Rename the machine."""
        await self.coordinator.async_execute(
            self.coordinator.client.set_name(value.strip())
        )
