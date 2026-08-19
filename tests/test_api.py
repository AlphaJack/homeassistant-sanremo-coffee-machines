"""Tests for the WiNET transport layer."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.sanremo.api import (
    SanremoClient,
    SanremoConnectionError,
    SanremoError,
    SanremoResponseError,
)

URL = "http://192.168.1.10/ajax/post"


@pytest.fixture
def client(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> SanremoClient:
    """Return a client wired to the mocked HTTP layer."""
    return SanremoClient("192.168.1.10", async_get_clientsession(hass))


async def test_decodes_a_reply_with_the_wrong_content_type(
    client: SanremoClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """The module often mislabels its JSON; decode it anyway."""
    aioclient_mock.post(
        URL,
        text='{"key":151,"registers":[[0,121]]}',
        headers={"Content-Type": "text/html"},
    )
    assert await client.get_read_only() == {"key": 151, "registers": [[0, 121]]}


async def test_malformed_json_raises_response_error(
    client: SanremoClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Garbage in the body is a response error, not a connection error."""
    aioclient_mock.post(URL, text="<html>not json</html>")
    with pytest.raises(SanremoResponseError):
        await client.get_read_only()


async def test_non_object_reply_raises(
    client: SanremoClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A JSON array where an object belongs is rejected."""
    aioclient_mock.post(URL, text="[1, 2, 3]")
    with pytest.raises(SanremoResponseError):
        await client.get_read_only()


async def test_connection_failure_raises_after_retries(
    client: SanremoClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """The module drops connections; give up only after retrying."""
    aioclient_mock.post(URL, exc=TimeoutError())
    # Patched: the retry backoff is real seconds and this test need not spend them.
    with (
        patch("custom_components.sanremo.api.asyncio.sleep", new=AsyncMock()),
        pytest.raises(SanremoConnectionError),
    ):
        await client.get_read_only()

    # One initial attempt plus two retries.
    assert aioclient_mock.call_count == 3


async def test_http_error_raises_connection_error(
    client: SanremoClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 500 is reported as a connection problem."""
    aioclient_mock.post(URL, status=500)
    with pytest.raises(SanremoConnectionError):
        await client.get_read_only()


async def test_set_value_omits_a_none_argument(
    client: SanremoClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """IDs 8 and 9 take no value, which serialises as an empty field."""
    aioclient_mock.post(URL, text='{"key":200,"result":true}')
    await client.set_value(8, None)

    body = aioclient_mock.mock_calls[0][2]
    assert body == {"key": "200", "id": "8", "value": ""}


async def test_set_clock_truncates_the_year(
    client: SanremoClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """key=249 wants a two-digit year."""
    aioclient_mock.post(URL, text='{"result":true}')
    await client.set_clock(11, 35, 19, 8, 2026)

    assert aioclient_mock.mock_calls[0][2]["YY"] == "26"


async def test_save_scheduler_day_builds_the_full_payload(
    client: SanremoClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """All three slots and all seven copy flags are always sent."""
    aioclient_mock.post(URL, text='{"result":true}')
    slots = [
        {"dow": 1, "on_hour": 7, "on_minute": 0, "off_hour": 9, "off_minute": 30},
        {"dow": 1, "on_hour": 18, "on_minute": 0, "off_hour": 22, "off_minute": 30},
        {"dow": 7, "on_hour": 0, "on_minute": 0, "off_hour": 0, "off_minute": 0},
    ]
    await client.save_scheduler_day(0, slots, [False] * 7)

    body = aioclient_mock.mock_calls[0][2]
    assert body["key"] == "253"
    assert body["day"] == "0"
    assert body["en1"] == "1"
    assert body["en3"] == "7"
    assert body["on2H"] == "18"
    assert body["copyMon"] == "0"
    assert body["copySun"] == "0"


async def test_save_scheduler_day_rejects_a_wrong_slot_count(
    client: SanremoClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """The machine stores exactly three slots; anything else is a bug."""
    with pytest.raises(SanremoError, match="exactly 3 slots"):
        await client.save_scheduler_day(0, [], [False] * 7)


async def test_set_name_rejects_an_empty_name(
    client: SanremoClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """The vendor app refuses an empty name and so do we."""
    with pytest.raises(SanremoError):
        await client.set_name("")


async def test_probe_model_page_reads_the_redirect(
    client: SanremoClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """The landing page's inline redirect names the model."""
    aioclient_mock.get(
        "http://192.168.1.10/index.html",
        text="<html><script>GoToUrl('cube.html');</script></html>",
    )
    assert await client.probe_model_page() == "cube"


async def test_probe_model_page_without_a_redirect(
    client: SanremoClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A page with no redirect yields None rather than a guess."""
    aioclient_mock.get("http://192.168.1.10/index.html", text="<html></html>")
    assert await client.probe_model_page() is None


async def test_probe_model_page_survives_a_dead_host(
    client: SanremoClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Probing is best-effort and must not raise."""
    aioclient_mock.get("http://192.168.1.10/index.html", exc=TimeoutError())
    assert await client.probe_model_page() is None
