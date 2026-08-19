"""Fixtures for the Sanremo integration tests.

Payloads are captures from a Cube R. The map was reverse engineered, so a
synthetic fixture would only confirm its author's assumptions.
"""

from __future__ import annotations

from collections.abc import Generator
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_HOST
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sanremo.const import DOMAIN

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    """Load a captured JSON payload."""
    return json.loads((FIXTURE_DIR / name).read_text())


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading of this custom integration in every test."""


@pytest.fixture
def cube_payloads() -> dict[str, dict[str, Any]]:
    """Return the four captured Cube replies, keyed by RPC key."""
    return {
        key: load_fixture(f"cube_{key}.json") for key in ("105", "150", "151", "152")
    }


@pytest.fixture
def mock_client(cube_payloads: dict[str, dict[str, Any]]) -> Generator[AsyncMock]:
    """Patch SanremoClient so no test ever touches the network."""
    with (
        patch("custom_components.sanremo.SanremoClient", autospec=True) as init_client,
        patch(
            "custom_components.sanremo.config_flow.SanremoClient", autospec=True
        ) as flow_client,
    ):
        client = init_client.return_value
        client.host = "192.168.1.10"
        client.base_url = "http://192.168.1.10"
        client.get_device_info = AsyncMock(return_value=cube_payloads["105"])
        client.get_system_params = AsyncMock(return_value=cube_payloads["150"])
        client.get_read_only = AsyncMock(return_value=cube_payloads["151"])
        client.get_read_write = AsyncMock(return_value=cube_payloads["152"])
        client.probe_model_page = AsyncMock(return_value="cube")
        client.set_value = AsyncMock()
        client.set_clock = AsyncMock()
        client.set_name = AsyncMock()
        client.set_scheduler_enabled = AsyncMock()
        client.set_scheduler_day_enabled = AsyncMock()
        client.save_scheduler_day = AsyncMock()
        client.reboot = AsyncMock()
        client.check_firmware = AsyncMock()
        client.get_json = AsyncMock(return_value={"result": True})
        flow_client.return_value = client
        yield client


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a config entry for the test machine."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="cube1 (Cube R)",
        data={CONF_HOST: "192.168.1.10"},
        unique_id="aa:bb:cc:dd:ee:ff",
    )
