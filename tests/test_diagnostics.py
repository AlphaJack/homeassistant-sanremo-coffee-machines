"""Tests for the diagnostics download."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sanremo.api import SanremoConnectionError
from custom_components.sanremo.diagnostics import (
    async_get_config_entry_diagnostics,
)


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> MockConfigEntry:
    """Set up the integration."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


async def test_diagnostics_include_raw_registers(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The register dumps are the point of the download."""
    result = await async_get_config_entry_diagnostics(hass, setup_integration)

    assert result["profile"]["id"] == "cube"
    assert "boiler_setpoint" in result["profile"]["capabilities"]
    assert result["profile"]["supports_push"] is False

    # Both register files, verbatim.
    assert result["machine_state"]["raw_registers"]["151"][0] == 121
    assert result["machine_state"]["raw_registers"]["152"][0] == 1210
    assert result["raw_replies"]["151"]["registers"]
    assert result["raw_replies"]["152"]["registers"]


async def test_diagnostics_redact_the_network(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """SSID, MAC and addresses identify a household and are removed."""
    result = await async_get_config_entry_diagnostics(hass, setup_integration)

    assert result["device"]["mac"] == "**REDACTED**"
    assert result["device"]["ip_address"] == "**REDACTED**"
    assert result["machine_state"]["ssid"] == "**REDACTED**"
    assert result["raw_replies"]["105"]["mac"] == "**REDACTED**"
    assert result["raw_replies"]["105"]["currentGw"] == "**REDACTED**"

    # But the useful, non-identifying values stay.
    assert result["raw_replies"]["105"]["fwVer"] == "0.24.000"


async def test_diagnostics_serialise_times_and_schedule(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Times become strings so the download is valid JSON."""
    result = await async_get_config_entry_diagnostics(hass, setup_integration)

    schedule = result["machine_state"]["schedule"]
    assert len(schedule) == 21
    assert schedule[0]["on"] == "07:00:00"
    assert schedule[0]["off"] == "09:30:00"
    assert isinstance(result["machine_state"]["machine_time"], str)


async def test_diagnostics_report_a_failing_read(
    hass: HomeAssistant, mock_client: AsyncMock, setup_integration: MockConfigEntry
) -> None:
    """A key that errors is reported rather than aborting the download."""
    mock_client.get_read_write.side_effect = SanremoConnectionError("reset by peer")

    result = await async_get_config_entry_diagnostics(hass, setup_integration)

    assert "reset by peer" in result["raw_replies"]["152"]["error"]
    # And the rest of the download still arrives.
    assert result["raw_replies"]["151"]["registers"]
