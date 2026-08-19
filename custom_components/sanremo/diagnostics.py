"""Diagnostics for Sanremo coffee machines."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import SanremoConfigEntry

# The machine name is user chosen and the network details identify a household.
TO_REDACT = {"ssid", "mac", "ip_address", "currentIp", "currentGw", "currentMask"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SanremoConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    state = coordinator.data

    machine = asdict(state)
    # Enum values serialise as their string form; times and dates need help.
    machine["schedule"] = [
        {
            "day": slot.day,
            "index": slot.index,
            "enabled": slot.enabled,
            "on": slot.on_time.isoformat(),
            "off": slot.off_time.isoformat(),
        }
        for slot in state.schedule
    ]
    machine["machine_time"] = (
        state.machine_time.isoformat() if state.machine_time else None
    )

    raw_replies: dict[str, Any] = {}
    for name, getter in (
        ("105", coordinator.client.get_device_info),
        ("150", coordinator.client.get_system_params),
        ("151", coordinator.client.get_read_only),
        ("152", coordinator.client.get_read_write),
    ):
        try:
            raw_replies[name] = await getter()
        except Exception as err:  # noqa: BLE001 - report the failure, keep going
            raw_replies[name] = {"error": str(err)}

    return {
        "entry": {
            "options": dict(entry.options),
        },
        "device": async_redact_data(asdict(coordinator.device), TO_REDACT),
        "profile": {
            "id": coordinator.profile.profile_id,
            "capabilities": sorted(coordinator.profile.capabilities),
            "supports_push": coordinator.profile.supports_push(),
        },
        "machine_state": async_redact_data(machine, TO_REDACT),
        "raw_replies": async_redact_data(raw_replies, TO_REDACT),
    }
