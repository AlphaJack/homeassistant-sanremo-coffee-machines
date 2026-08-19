"""Buttons for Sanremo coffee machines."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SanremoConfigEntry
from .const import Capability
from .entity import SanremoEntity, SanremoEntityDescription, async_supported
from .profiles import MachineProfile

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class SanremoButtonDescription(SanremoEntityDescription, ButtonEntityDescription):
    """Describes a Sanremo button."""

    press_fn: Callable[[MachineProfile], Awaitable[None]]


BUTTONS: tuple[SanremoButtonDescription, ...] = (
    SanremoButtonDescription(
        key="reset_filter",
        translation_key="reset_filter",
        entity_category=EntityCategory.CONFIG,
        capability=Capability.FILTER_MONITORING,
        press_fn=lambda profile: profile.async_reset_filter(),
    ),
    SanremoButtonDescription(
        key="sync_clock",
        translation_key="sync_clock",
        entity_category=EntityCategory.CONFIG,
        capability=Capability.CLOCK,
        press_fn=lambda profile: profile.async_sync_clock(),
    ),
    SanremoButtonDescription(
        key="check_firmware",
        translation_key="check_firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        capability=Capability.FIRMWARE_CHECK,
        press_fn=lambda profile: profile.async_check_firmware(),
    ),
    SanremoButtonDescription(
        key="reboot",
        translation_key="reboot",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.DIAGNOSTIC,
        # Drops connectivity for a while, so opt-in.
        entity_registry_enabled_default=False,
        capability=Capability.REBOOT,
        press_fn=lambda profile: profile.async_reboot(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SanremoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons."""
    coordinator = entry.runtime_data
    async_add_entities(
        SanremoButton(coordinator, description)
        for description in BUTTONS
        if async_supported(coordinator, description)
    )


class SanremoButton(SanremoEntity, ButtonEntity):
    """A one-shot machine command."""

    entity_description: SanremoButtonDescription

    async def async_press(self) -> None:
        """Send the command."""
        await self.coordinator.async_execute(
            self.entity_description.press_fn(self.coordinator.profile)
        )
