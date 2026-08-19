"""Fallback profile for WiNET machines whose register map is unknown."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.util import dt as dt_util

from ..const import WIFI_STATUS_MAP, Capability, WifiStatus
from ..models import DeviceInfo, MachineState
from .base import MachineProfile
from .cube import _parse_machine_time, _registers

_LOGGER = logging.getLogger(__name__)


class GenericProfile(MachineProfile):
    """Model-agnostic WiNET support."""

    profile_id = "generic"
    model_name = "WiNET machine"

    def __init__(self, client) -> None:
        super().__init__(client)
        self._model_page: str | None = None
        self._has_read_write = False

    async def async_setup(self) -> DeviceInfo:
        """Probe whatever the module is willing to tell us."""
        info = await self.client.get_device_info()
        system = await self.client.get_system_params()

        self._model_page = await self.client.probe_model_page()

        try:
            await self.client.get_read_write()
        except Exception:  # noqa: BLE001 - absence is a valid answer
            self._has_read_write = False
        else:
            self._has_read_write = True

        # Only module-level capabilities. Nothing here writes to the machine.
        self._capabilities = {
            Capability.CLOCK,
            Capability.RENAME,
            Capability.REBOOT,
            Capability.FIRMWARE_CHECK,
        }

        _LOGGER.info(
            "Sanremo machine at %s is not a recognised model (page=%s). Running the "
            "generic profile: identity, network and raw registers only. Please open "
            "an issue with the diagnostics download to get it mapped",
            self.client.host,
            self._model_page or "unknown",
        )

        model = self.model_name
        if self._model_page:
            model = f"{self._model_page.upper()} (unmapped)"

        return DeviceInfo(
            name=str(info.get("name") or "Sanremo machine"),
            mac=str(info.get("mac") or ""),
            ip_address=info.get("currentIp"),
            wifi_firmware=info.get("fwVer"),
            board_firmware=_board_version(system.get("ver")),
            profile=self.profile_id,
            model=model,
        )

    async def async_poll(self) -> MachineState:
        """Read everything readable, decode only what is safe to decode."""
        state = MachineState()

        system = await self.client.get_system_params()
        state.name = system.get("name")
        state.ssid = system.get("ssid")
        state.rssi = _as_int(system.get("rssi"))
        state.firmware_update_available = _as_bool(system.get("ota"))
        state.cloud_connected = _as_bool(system.get("cloudConnection"))
        state.wifi_status = WIFI_STATUS_MAP.get(
            _as_int(system.get("status")) or -1, WifiStatus.UNKNOWN
        )
        state.board_firmware = _board_version(system.get("ver"))

        read_only = await self.client.get_read_only()
        state.raw_registers["151"] = _registers(read_only)
        state.machine_time, state.machine_weekday_known = _parse_machine_time(
            read_only.get("time")
        )
        if (rssi := _as_int(read_only.get("rssi"))) is not None:
            state.rssi = rssi

        if self._has_read_write:
            try:
                read_write = await self.client.get_read_write()
            except Exception as err:  # noqa: BLE001 - best effort
                _LOGGER.debug("Read/write poll failed: %s", err)
            else:
                state.raw_registers["152"] = _registers(read_write)

        return state

    async def async_sync_clock(self) -> None:
        """Write Home Assistant's local time to the machine."""
        now = dt_util.now()
        await self.client.set_clock(now.hour, now.minute, now.day, now.month, now.year)

    async def async_reboot(self) -> None:
        """Reboot the Wi-Fi module."""
        await self.client.reboot()

    async def async_check_firmware(self) -> None:
        """Ask the module to check for a firmware update."""
        await self.client.check_firmware()


def _board_version(value: Any) -> str | None:
    """Render the board firmware, if the machine reports one."""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    """Coerce to int, or None when that is not possible."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    """Coerce to bool, preserving None."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)
