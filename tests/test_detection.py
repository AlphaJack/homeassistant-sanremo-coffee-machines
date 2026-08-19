"""Tests for profile detection."""

from __future__ import annotations

from unittest.mock import AsyncMock

from custom_components.sanremo.api import SanremoConnectionError
from custom_components.sanremo.const import Capability
from custom_components.sanremo.profiles import (
    CubeProfile,
    GenericProfile,
    YouProfile,
    async_detect_profile,
)


async def test_landing_page_identifies_a_cube(mock_client: AsyncMock) -> None:
    """cube.html is decisive."""
    mock_client.probe_model_page.return_value = "cube"
    assert isinstance(await async_detect_profile(mock_client), CubeProfile)


async def test_landing_page_identifies_a_you(mock_client: AsyncMock) -> None:
    """you.html is decisive too."""
    mock_client.probe_model_page.return_value = "you"
    assert isinstance(await async_detect_profile(mock_client), YouProfile)


async def test_register_reply_implies_the_cube_family(mock_client: AsyncMock) -> None:
    """Without a page hint, a registers array means the register protocol."""
    mock_client.probe_model_page.return_value = None
    assert isinstance(await async_detect_profile(mock_client), CubeProfile)


async def test_array_reply_implies_the_you_family(mock_client: AsyncMock) -> None:
    """Parallel status/settings arrays mean the YOU protocol."""
    mock_client.probe_model_page.return_value = None
    mock_client.get_read_only.return_value = {"key": 151, "rssi": -60}
    mock_client.get_system_params.return_value = {
        "key": 150,
        "status": [0] * 32,
        "settings": [1200, 900, 900, 12],
        "name": "YOU",
    }

    assert isinstance(await async_detect_profile(mock_client), YouProfile)


async def test_unknown_machine_falls_back_to_generic(mock_client: AsyncMock) -> None:
    """A WiNET module we do not recognise still gets a working entry."""
    mock_client.probe_model_page.return_value = "zoe"
    mock_client.get_read_only.return_value = {"key": 151, "rssi": -60}
    mock_client.get_system_params.return_value = {"key": 150, "name": "Zoe"}

    profile = await async_detect_profile(mock_client)
    assert isinstance(profile, GenericProfile)


async def test_detection_survives_partial_failures(mock_client: AsyncMock) -> None:
    """A machine that errors on some keys still resolves to a profile."""
    mock_client.probe_model_page.return_value = None
    mock_client.get_read_only.side_effect = SanremoConnectionError("reset")
    mock_client.get_system_params.side_effect = SanremoConnectionError("reset")

    assert isinstance(await async_detect_profile(mock_client), GenericProfile)


async def test_generic_profile_exposes_registers_not_guesses(
    mock_client: AsyncMock,
) -> None:
    """The generic profile publishes raw registers and no invented values."""
    mock_client.probe_model_page.return_value = "zoe"
    profile = GenericProfile(mock_client)
    device = await profile.async_setup()
    state = await profile.async_poll()

    assert device.model == "ZOE (unmapped)"
    assert "151" in state.raw_registers
    assert state.raw_registers["151"]

    # Identity and network are safe to read; machine semantics are not.
    assert state.name == "cube1"
    assert state.rssi is not None
    assert state.boiler_temperature is None
    assert state.coffees_total is None
    assert state.alarms == {}

    # No writes to the machine are offered.
    assert not profile.supports(Capability.POWER)
    assert not profile.supports(Capability.BOILER_SETPOINT)
    assert profile.supports(Capability.CLOCK)
