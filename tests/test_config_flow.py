"""Tests for the config and options flows."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sanremo.api import (
    SanremoConnectionError,
    SanremoResponseError,
)
from custom_components.sanremo.const import CONF_SCAN_INTERVAL, DOMAIN


async def _start(hass: HomeAssistant) -> dict:
    """Begin a user flow."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


async def test_user_flow_creates_an_entry(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """A reachable machine is identified and stored."""
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.10"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "cube1 (Cube R)"
    assert result["data"] == {CONF_HOST: "192.168.1.10"}
    # The MAC is the only durable identity the module offers.
    assert result["result"].unique_id == "aa:bb:cc:dd:ee:ff"


async def test_user_flow_accepts_a_pasted_url(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """The vendor app shows an http:// URL, so tolerate one being pasted in."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "http://192.168.1.10/"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: "192.168.1.10"}


async def test_user_flow_handles_an_unreachable_machine(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """An offline machine shows a recoverable error, not an abort."""
    mock_client.probe_model_page.side_effect = SanremoConnectionError("timeout")
    mock_client.get_read_only.side_effect = SanremoConnectionError("timeout")
    mock_client.get_system_params.side_effect = SanremoConnectionError("timeout")
    mock_client.get_device_info.side_effect = SanremoConnectionError("timeout")

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.10"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    # And the user can correct it without restarting the flow.
    mock_client.probe_model_page.side_effect = None
    mock_client.get_device_info.side_effect = None
    mock_client.get_system_params.side_effect = None
    mock_client.get_read_only.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.10"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_handles_a_non_sanremo_host(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """Something that answers but is not a WiNET module is reported clearly."""
    mock_client.probe_model_page.return_value = None
    mock_client.get_read_only.side_effect = SanremoResponseError("nope")
    mock_client.get_system_params.side_effect = SanremoResponseError("nope")
    mock_client.get_device_info.side_effect = SanremoResponseError("nope")

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.10"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_response"}


async def test_duplicate_machine_aborts(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The same machine cannot be added twice."""
    config_entry.add_to_hass(hass)

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.10"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_duplicate_machine_updates_the_host(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Re-adding a machine at a new address refreshes the stored host."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, data={CONF_HOST: "10.0.0.9"})

    result = await _start(hass)
    await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.10"}
    )

    assert config_entry.data[CONF_HOST] == "192.168.1.10"


async def test_reconfigure_updates_the_host(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The reconfigure flow moves an entry to a new address."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.200"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_HOST] == "192.168.1.200"


async def test_reconfigure_rejects_a_different_machine(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry: MockConfigEntry,
    cube_payloads: dict,
) -> None:
    """Pointing an entry at another machine is refused, not silently accepted."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_client.get_device_info.return_value = dict(cube_payloads["105"]) | {
        "mac": "112233445566"
    }

    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.201"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_machine"
    assert config_entry.data[CONF_HOST] == "192.168.1.10"


async def test_options_flow_sets_the_scan_interval(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """The polling interval is adjustable."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 30}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[CONF_SCAN_INTERVAL] == 30
