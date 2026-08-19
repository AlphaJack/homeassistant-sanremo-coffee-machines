"""Transport for the WiNET RPC endpoint embedded in Sanremo machines."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

from .const import AJAX_PATH, INDEX_PATH, Key

_LOGGER = logging.getLogger(__name__)

#: The embedded HTTP server handles a single request at a time and the vendor app
_TIMEOUT = aiohttp.ClientTimeout(total=10)

#: Minimum gap between requests. Hammering the module causes connection resets.
_REQUEST_GAP = 0.25

_MAX_ATTEMPTS = 3
_RETRY_DELAY = 1.0


class SanremoError(Exception):
    """Base error for this integration."""


class SanremoConnectionError(SanremoError):
    """The machine could not be reached."""


class SanremoResponseError(SanremoError):
    """The machine replied with something unusable."""


class SanremoClient:
    """Serialised RPC client for one machine."""

    def __init__(self, host: str, session: aiohttp.ClientSession) -> None:
        self._host = host
        self._session = session
        self._lock = asyncio.Lock()

    @property
    def host(self) -> str:
        """Return the host this client talks to."""
        return self._host

    @property
    def base_url(self) -> str:
        """Return the machine's web UI URL."""
        return f"http://{self._host}"

    @property
    def session(self) -> aiohttp.ClientSession:
        """Return the shared aiohttp session, for WebSocket users."""
        return self._session

    # ── Low level ─────────────────────────────────────────────────────────────

    async def _request(self, payload: dict[str, str]) -> dict[str, Any]:
        """POST ``payload`` and decode the JSON reply."""
        url = f"{self.base_url}{AJAX_PATH}"
        last_error: Exception | None = None

        async with self._lock:
            for attempt in range(1, _MAX_ATTEMPTS + 1):
                try:
                    async with self._session.post(
                        url, data=payload, timeout=_TIMEOUT
                    ) as response:
                        response.raise_for_status()
                        # Never let aiohttp police the Content-Type; the module often gets it wrong.
                        raw = await response.text()
                except (aiohttp.ClientError, TimeoutError) as err:
                    last_error = err
                    if attempt < _MAX_ATTEMPTS:
                        _LOGGER.debug(
                            "Request %s to %s failed (attempt %s/%s): %s",
                            payload.get("key"),
                            self._host,
                            attempt,
                            _MAX_ATTEMPTS,
                            err,
                        )
                        await asyncio.sleep(_RETRY_DELAY)
                        continue
                    raise SanremoConnectionError(
                        f"Cannot reach Sanremo machine at {self._host}: {err}"
                    ) from err

                try:
                    decoded = json.loads(raw)
                except ValueError as err:
                    raise SanremoResponseError(
                        f"Malformed reply to key={payload.get('key')}: {raw[:120]!r}"
                    ) from err

                if not isinstance(decoded, dict):
                    raise SanremoResponseError(
                        f"Expected an object for key={payload.get('key')}, "
                        f"got {type(decoded).__name__}"
                    )

                await asyncio.sleep(_REQUEST_GAP)
                return decoded

        raise SanremoConnectionError(str(last_error))

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_device_info(self) -> dict[str, Any]:
        """Read device and network info (``key=105``)."""
        return await self._request({"key": Key.DEVICE_INFO})

    async def get_system_params(self) -> dict[str, Any]:
        """Read system parameters (``key=150``)."""
        return await self._request({"key": Key.SYSTEM_PARAMS})

    async def get_read_only(self) -> dict[str, Any]:
        """Read the read-only register file (``key=151``)."""
        return await self._request({"key": Key.READ_ONLY})

    async def get_read_write(self) -> dict[str, Any]:
        """Read the read/write register file (``key=152``)."""
        return await self._request({"key": Key.READ_WRITE})

    async def get_json(self, path: str) -> Any:
        """GET ``path`` and decode the JSON reply."""
        url = f"{self.base_url}{path}"
        try:
            async with self._session.get(url, timeout=_TIMEOUT) as response:
                response.raise_for_status()
                raw = await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SanremoConnectionError(f"GET {path} failed: {err}") from err

        try:
            return json.loads(raw)
        except ValueError as err:
            raise SanremoResponseError(
                f"Malformed reply from {path}: {raw[:120]!r}"
            ) from err

    async def probe_model_page(self) -> str | None:
        """Return the model page the landing page redirects to, e.g. ``cube``."""
        url = f"{self.base_url}{INDEX_PATH}"
        try:
            async with self._session.get(url, timeout=_TIMEOUT) as response:
                response.raise_for_status()
                body = await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("Could not read %s: %s", url, err)
            return None

        # The redirect is a literal GoToUrl('cube.html') call in an inline script.
        marker = "GoToUrl('"
        start = body.find(marker)
        if start == -1:
            return None
        start += len(marker)
        end = body.find("'", start)
        if end == -1:
            return None
        page = body[start:end].strip()
        return page.removesuffix(".html").lower() or None

    # ── Writes ────────────────────────────────────────────────────────────────

    async def set_value(self, param_id: int, value: int | None = None) -> None:
        """Send ``key=200`` for ``param_id``."""
        await self._request(
            {
                "key": Key.SET_VALUE,
                "id": str(param_id),
                "value": "" if value is None else str(value),
            }
        )

    async def set_clock(
        self, hour: int, minute: int, day: int, month: int, year: int
    ) -> None:
        """Set the machine clock (``key=249``). ``year`` may be 2 or 4 digits."""
        await self._request(
            {
                "key": Key.SET_CLOCK,
                "hh": str(hour),
                "mm": str(minute),
                "DD": str(day),
                "MM": str(month),
                "YY": str(year % 100),
            }
        )

    async def set_name(self, name: str) -> None:
        """Rename the machine (``key=251``)."""
        if not name:
            raise SanremoError("Machine name cannot be empty")
        await self._request({"key": Key.SET_NAME, "name": name})

    async def set_scheduler_enabled(self, enabled: bool) -> None:
        """Flip the scheduler master switch (``key=252``)."""
        await self._request(
            {"key": Key.SET_SCHEDULER_ENABLED, "enabled": "1" if enabled else "0"}
        )

    async def set_scheduler_day_enabled(self, dow: int, enabled: bool) -> None:
        """Enable one scheduler day (``key=250``)."""
        await self._request(
            {
                "key": Key.SET_SCHEDULER_DAY_ENABLED,
                "day": str(dow),
                "enabled": "1" if enabled else "0",
            }
        )

    async def save_scheduler_day(
        self, row: int, slots: list[dict[str, int]], copy_to: list[bool] | None = None
    ) -> None:
        """Write one day's three slots (``key=253``)."""
        if len(slots) != 3:
            raise SanremoError(f"Expected exactly 3 slots, got {len(slots)}")

        payload: dict[str, str] = {"key": Key.SAVE_SCHEDULER_DAY, "day": str(row)}
        for number, slot in enumerate(slots, start=1):
            payload[f"en{number}"] = str(slot["dow"])
            payload[f"on{number}H"] = str(slot["on_hour"])
            payload[f"on{number}M"] = str(slot["on_minute"])
            payload[f"off{number}H"] = str(slot["off_hour"])
            payload[f"off{number}M"] = str(slot["off_minute"])

        # The wire order for the copy flags is Monday first.
        names = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
        flags = copy_to or [False] * 7
        for name, flag in zip(names, flags, strict=True):
            payload[f"copy{name}"] = "1" if flag else "0"

        await self._request(payload)

    async def reboot(self) -> None:
        """Reboot the Wi-Fi module (``key=202``). Does not restart the machine."""
        await self._request({"key": Key.REBOOT})

    async def check_firmware(self) -> None:
        """Ask the module to look for a firmware update (``key=203``)."""
        await self._request({"key": Key.CHECK_FIRMWARE})
