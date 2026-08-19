"""Profile interface shared by every supported machine family."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import time

from ..api import SanremoClient
from ..const import Capability
from ..models import DeviceInfo, MachineState


class MachineProfile(ABC):
    """Turns one machine family's wire format into a :class:`MachineState`."""

    profile_id: str = "generic"
    model_name: str = "Coffee machine"

    def __init__(self, client: SanremoClient) -> None:
        self.client = client
        self._capabilities: set[Capability] = set()

    @property
    def capabilities(self) -> set[Capability]:
        """Return the capabilities detected for this specific machine."""
        return self._capabilities

    def supports(self, capability: Capability) -> bool:
        """Return True if this machine supports ``capability``."""
        return capability in self._capabilities

    @abstractmethod
    async def async_setup(self) -> DeviceInfo:
        """Probe the machine once and return its identity."""

    @abstractmethod
    async def async_poll(self) -> MachineState:
        """Read the machine and return a normalised snapshot."""

    def supports_push(self) -> bool:
        """Return True if this machine pushes state instead of only polling."""
        return False

    @property
    def push_alive(self) -> bool:
        """Return True if pushed telemetry is current."""
        return False

    async def async_start_push(self, callback: Callable[[MachineState], None]) -> None:
        """Begin pushing state updates through ``callback``."""

    async def async_stop_push(self) -> None:
        """Stop pushing state updates."""

    # ── Commands: a profile implements only what its machine can do, and the
    # ── platforms build entities from `capabilities`, so unsupported ones never
    # ── get called. Temperatures are °C, delays seconds, days 0 = Monday.

    async def async_set_power(self, on: bool) -> None:  # noqa: D102
        raise NotImplementedError

    async def async_set_boiler_setpoint(self, celsius: float) -> None:  # noqa: D102
        raise NotImplementedError

    async def async_set_eco_setpoint(self, celsius: float) -> None:  # noqa: D102
        raise NotImplementedError

    async def async_set_energy_saving_delay(self, seconds: int) -> None:  # noqa: D102
        raise NotImplementedError

    async def async_set_energy_saving_mode(self, eco: bool) -> None:  # noqa: D102
        raise NotImplementedError

    async def async_set_standby_after_last_coffee(self, enabled: bool) -> None:  # noqa: D102
        raise NotImplementedError

    async def async_set_steam_booster(self, enabled: bool) -> None:  # noqa: D102
        raise NotImplementedError

    async def async_set_filter_interval_months(self, months: int) -> None:
        """Zero disables filter monitoring."""
        raise NotImplementedError

    async def async_reset_filter(self) -> None:  # noqa: D102
        raise NotImplementedError

    async def async_set_display_temperature_fahrenheit(self, enabled: bool) -> None:  # noqa: D102
        raise NotImplementedError

    async def async_set_clock_12_hour(self, enabled: bool) -> None:  # noqa: D102
        raise NotImplementedError

    async def async_set_scheduler_enabled(self, enabled: bool) -> None:  # noqa: D102
        raise NotImplementedError

    async def async_set_scheduler_day_enabled(self, day: int, enabled: bool) -> None:  # noqa: D102
        raise NotImplementedError

    async def async_set_schedule_day(
        self,
        day: int,
        slots: list[tuple[bool, time, time]],
        copy_to: list[int] | None = None,
    ) -> None:
        """Replace one day's slots with ``(enabled, on_time, off_time)`` tuples."""
        raise NotImplementedError

    async def async_sync_clock(self) -> None:  # noqa: D102
        raise NotImplementedError

    async def async_reboot(self) -> None:  # noqa: D102
        raise NotImplementedError

    async def async_check_firmware(self) -> None:  # noqa: D102
        raise NotImplementedError
