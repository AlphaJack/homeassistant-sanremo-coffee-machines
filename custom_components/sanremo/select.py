"""Select entities for Sanremo coffee machines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SanremoConfigEntry
from .const import Capability, EnergySavingMode
from .coordinator import SanremoCoordinator
from .entity import SanremoEntity, SanremoEntityDescription

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class SanremoSelectDescription(SanremoEntityDescription, SelectEntityDescription):
    """Describes a Sanremo select."""


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SanremoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the select entities."""
    coordinator = entry.runtime_data
    if coordinator.supports(Capability.ENERGY_SAVING_MODE):
        async_add_entities([SanremoEnergySavingModeSelect(coordinator)])


class SanremoEnergySavingModeSelect(SanremoEntity, SelectEntity):
    """Chooses what the machine does when the idle timer expires."""

    _attr_options: ClassVar[list[str]] = [mode.value for mode in EnergySavingMode]

    def __init__(self, coordinator: SanremoCoordinator) -> None:
        super().__init__(
            coordinator,
            SanremoSelectDescription(
                key="energy_saving_mode",
                translation_key="energy_saving_mode",
                entity_category=EntityCategory.CONFIG,
                capability=Capability.ENERGY_SAVING_MODE,
            ),
        )

    @property
    def current_option(self) -> str | None:
        """Return the selected mode."""
        mode = self.machine.energy_saving_mode
        return None if mode is None else mode.value

    async def async_select_option(self, option: str) -> None:
        """Select ECO or STANDBY."""
        await self.coordinator.async_execute(
            self.coordinator.profile.async_set_energy_saving_mode(
                option == EnergySavingMode.ECO
            )
        )
