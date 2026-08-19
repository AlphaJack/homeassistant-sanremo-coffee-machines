"""Update coordinator for Sanremo coffee machines."""

from __future__ import annotations

from collections.abc import Coroutine
from datetime import timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SanremoClient, SanremoError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, Capability
from .models import DeviceInfo, MachineState
from .profiles import MachineProfile

if TYPE_CHECKING:
    from . import SanremoConfigEntry

_LOGGER = logging.getLogger(__name__)


class SanremoCoordinator(DataUpdateCoordinator[MachineState]):
    """Polls one machine and, where supported, accepts pushed telemetry."""

    config_entry: SanremoConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: SanremoClient,
        profile: MachineProfile,
        device_info: DeviceInfo,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} {device_info.name}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.profile = profile
        self.device = device_info

    # ── Introspection used by the platforms ───────────────────────────────────

    @property
    def device_id(self) -> str:
        """Return a stable identifier for this machine."""
        return (
            format_mac(self.device.mac)
            if self.device.mac
            else self.config_entry.entry_id
        )

    def supports(self, capability: Capability) -> bool:
        """Return True if the machine supports ``capability``."""
        return self.profile.supports(capability)

    @property
    def push_alive(self) -> bool:
        """Return True if pushed telemetry is current."""
        if not self.profile.supports_push():
            return True
        return self.profile.push_alive

    # ── Polling ───────────────────────────────────────────────────────────────

    async def _async_update_data(self) -> MachineState:
        """Poll the machine."""
        try:
            return await self.profile.async_poll()
        except SanremoError as err:
            raise UpdateFailed(str(err)) from err

    # ── Push ──────────────────────────────────────────────────────────────────

    async def async_start_push(self) -> None:
        """Start the profile's push listener, if it has one."""
        if not self.profile.supports_push():
            return
        await self.profile.async_start_push(self._handle_push)

    async def async_stop_push(self) -> None:
        """Stop the profile's push listener."""
        if self.profile.supports_push():
            await self.profile.async_stop_push()

    def _handle_push(self, state: MachineState) -> None:
        """Publish a pushed state update to the entities."""
        self.async_set_updated_data(state)

    # ── Writes ────────────────────────────────────────────────────────────────

    async def async_execute(self, coro: Coroutine[Any, Any, None]) -> None:
        """Run a profile command, then refresh so entities reflect reality."""
        try:
            await coro
        except SanremoError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        # Not async_request_refresh: its 10 s debounce makes controls snap back.
        await self.async_refresh()
