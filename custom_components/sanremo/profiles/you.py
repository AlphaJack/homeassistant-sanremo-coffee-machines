"""Profile for the Sanremo YOU (the array protocol)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import replace
from datetime import time
import json
import logging
import time as time_module
from typing import Any, Final

import aiohttp
from homeassistant.util import dt as dt_util

from ..const import Capability, WaterSource
from ..models import DeviceInfo, MachineState, ScheduleSlot
from .base import MachineProfile

_LOGGER = logging.getLogger(__name__)

# ── key=200 parameter IDs (note: these take tenths, unlike the Cube's ID 1) ────
PARAM_COFFEE_BOILER_TEMP: Final = 1
PARAM_STEAM_BOILER_PRESSURE: Final = 2
PARAM_GROUP_TEMP: Final = 3

# ── status[] indices ───────────────────────────────────────────────────────────
ST_STATUS_PHASE: Final = 10
ST_MACHINE_STATUS: Final = 12
ST_ALARMS: Final = 13
ST_WARNINGS: Final = 14
ST_GROUP_TEMP: Final = 15
ST_HEATER_TEMP: Final = 16
ST_SERVICE_HEATER_TEMP: Final = 17
ST_SERVICE_HEATER_PRESSURE: Final = 18
ST_PUMP_PRESSURE: Final = 19
ST_COUNTER_VOL: Final = 20
ST_DOSE_TIME: Final = 22
ST_LEVEL_SENSOR: Final = 23
ST_DEEP_SLEEP: Final = 29
ST_REALTIME_FLOW: Final = 30
ST_SET_PRESS_PADDLE: Final = 31

# ── settings[] indices ─────────────────────────────────────────────────────────
SET_STEAM_HEATER: Final = 0
SET_GROUP_TEMP: Final = 1
SET_FILTER_HOLDER_TEMP: Final = 2
SET_STEAM_PRESSURE: Final = 3

MACHINE_STATUS_OFF: Final = 0
MACHINE_STATUS_ON: Final = 1
MACHINE_STATUS_ECO: Final = 2
MACHINE_STATUS_DEEP_SLEEP: Final = 3

ALARM_BITS: Final[dict[str, int]] = {
    "no_flowmeter_pulses": 1 << 0,
    "no_tank_level": 1 << 1,
    "filling_timeout": 1 << 2,
    "no_tank": 1 << 3,
    "ntc_boiler_short_circuit": 1 << 4,
    "ntc_boiler_open": 1 << 5,
    "ntc_coffee_short_circuit": 1 << 6,
    "ntc_coffee_open": 1 << 7,
    "ntc_group_short_circuit": 1 << 8,
    "ntc_group_open": 1 << 9,
    "lever_transducer_short_circuit": 1 << 10,
    "lever_transducer_open": 1 << 11,
    "pump_pressure_transducer_short_circuit": 1 << 12,
    "pump_pressure_transducer_open": 1 << 13,
    "boiler_pressure_transducer_short_circuit": 1 << 14,
    "boiler_pressure_transducer_open": 1 << 15,
}

WARNING_BITS: Final[dict[str, int]] = {
    "tank_filling": 1 << 0,
    "first_tank_filling": 1 << 5,
    "first_boiler_filling": 1 << 6,
    "machine_locked": 1 << 7,
    "low_boiler_level": 1 << 8,
}

SHOT_MODES: Final[dict[int, str]] = {1: "p1", 2: "p2", 3: "p3", 4: "man"}

#: Seven counter categories, each split across a high and a low register.
COUNTER_PAIRS: Final = 7

SCHEDULER_SLOTS: Final = 6
SCHEDULER_IDX_ENABLED: Final = 1
SCHEDULER_IDX_TIMES: Final = 2
SCHEDULER_IDX_DAYS: Final = 14
SCHEDULER_ECO_BIT: Final = 7

API_ACTION_ON: Final = "/api/action/on"
API_ACTION_STANDBY: Final = "/api/action/standby"
API_ACTION_PREFIX: Final = "/api/action"
API_COUNTERS: Final = "/api/counters"
API_DOSES: Final = "/api/doses"

WS_PORT: Final = 81
WS_THROTTLE_SECONDS: Final = 1.0
WS_RECONNECT_SECONDS: Final = 5
#: Telemetry older than this is treated as stale rather than current.
PUSH_STALE_SECONDS: Final = 30


class YouProfile(MachineProfile):
    """Sanremo YOU."""

    profile_id = "you"
    model_name = "YOU"

    def __init__(self, client) -> None:
        super().__init__(client)
        self._has_rest_api = False
        self._push_task: asyncio.Task | None = None
        self._push_callback: Callable[[MachineState], None] | None = None
        self._last_state: MachineState | None = None
        self._last_push = 0.0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def async_setup(self) -> DeviceInfo:
        """Probe the machine and detect the optional REST surface."""
        info = await self.client.get_device_info()
        payload = await self.client.get_system_params()

        caps = {
            Capability.POWER,
            Capability.CLOCK,
            Capability.RENAME,
            Capability.REBOOT,
            Capability.FIRMWARE_CHECK,
            Capability.SCHEDULER,
        }

        # No BOILER_SETPOINT: the write target is unverified and unbounded here.
        settings = payload.get("settings") or []
        if len(settings) > SET_STEAM_HEATER:
            _LOGGER.debug(
                "YOU at %s reports %s setpoints; the setpoint control stays "
                "disabled until its write path is verified against hardware",
                self.client.host,
                len(settings),
            )

        # Firmware 0.12+ adds /api/*. Its absence is normal, not an error.
        self._has_rest_api = await self._probe_rest_api()

        self._capabilities = caps

        return DeviceInfo(
            name=str(info.get("name") or "Sanremo YOU"),
            mac=str(info.get("mac") or ""),
            ip_address=info.get("currentIp"),
            wifi_firmware=info.get("fwVer"),
            profile=self.profile_id,
            model=self.model_name,
        )

    async def _probe_rest_api(self) -> bool:
        """Return True if the newer REST endpoints answer."""
        try:
            await self.client.get_json(API_COUNTERS)
        except Exception:  # noqa: BLE001 - absence is an expected outcome
            _LOGGER.debug("YOU at %s has no /api surface", self.client.host)
            return False
        return True

    async def async_poll(self) -> MachineState:
        """Read ``key=150`` and normalise it."""
        payload = await self.client.get_system_params()
        state = MachineState()
        self._apply_system_params(state, payload)

        try:
            lightweight = await self.client.get_read_only()
        except Exception as err:  # noqa: BLE001 - best effort
            _LOGGER.debug("Lightweight poll failed: %s", err)
        else:
            if (rssi := lightweight.get("rssi")) is not None:
                state.rssi = int(rssi)
            if (ota := lightweight.get("ota")) is not None:
                state.firmware_update_available = bool(ota)

        # Carry over the push-only fields so a poll does not blank them out.
        if self._last_state is not None:
            for field in (
                "realtime_flow",
                "shot_volume",
                "pump_pressure",
            ):
                if getattr(state, field) is None:
                    setattr(state, field, getattr(self._last_state, field))

        self._last_state = state
        return state

    def _apply_system_params(
        self, state: MachineState, payload: dict[str, Any]
    ) -> None:
        """Decode the ``key=150`` arrays."""
        state.name = payload.get("name")
        state.ssid = payload.get("ssid")
        state.rssi = _as_int(payload.get("rssi"))
        state.firmware_update_available = _as_bool(payload.get("ota"))

        status = payload.get("status") or []
        settings = payload.get("settings") or []

        _apply_status(state, status)
        _apply_settings(state, settings)
        _apply_counters(state, payload)

        scheduler = payload.get("scheduler") or []
        if scheduler:
            state.scheduler_enabled, state.schedule = _parse_scheduler(scheduler)

        state.raw_registers["status"] = list(status)
        state.raw_registers["settings"] = list(settings)

    # ── Push (WebSocket) ──────────────────────────────────────────────────────

    def supports_push(self) -> bool:
        """Return True: the YOU broadcasts telemetry on port 81."""
        return True

    @property
    def push_alive(self) -> bool:
        """Return True if telemetry arrived recently enough to be trusted."""
        if self._push_task is None or self._push_task.done():
            return False
        if not self._last_push:
            return False
        return (time_module.monotonic() - self._last_push) < PUSH_STALE_SECONDS

    async def async_start_push(self, callback: Callable[[MachineState], None]) -> None:
        """Begin listening for pushed telemetry."""
        self._push_callback = callback
        if self._push_task is None or self._push_task.done():
            self._push_task = asyncio.create_task(self._listen())

    async def async_stop_push(self) -> None:
        """Stop the listener."""
        if self._push_task and not self._push_task.done():
            self._push_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._push_task
        self._push_task = None
        self._push_callback = None

    async def _listen(self) -> None:
        """Consume the WebSocket, reconnecting on failure."""
        url = f"ws://{self.client.host}:{WS_PORT}/"
        while True:
            try:
                async with self.client.session.ws_connect(url, heartbeat=20) as socket:
                    _LOGGER.debug("Connected to %s", url)
                    async for message in socket:
                        if message.type is not aiohttp.WSMsgType.TEXT:
                            break
                        now = time_module.monotonic()
                        if now - self._last_push < WS_THROTTLE_SECONDS:
                            continue
                        self._last_push = now
                        self._handle_frame(message.data)
            except asyncio.CancelledError:
                return
            except Exception as err:  # noqa: BLE001 - reconnect on anything
                _LOGGER.debug("WebSocket to %s failed: %s", url, err)

            try:
                await asyncio.sleep(WS_RECONNECT_SECONDS)
            except asyncio.CancelledError:
                return

    def _handle_frame(self, raw: str) -> None:
        """Merge one telemetry frame into the last known state."""
        try:
            frame = json.loads(raw)
        except ValueError:
            return
        if not isinstance(frame, dict) or "tempBoilerCoffe" not in frame:
            return
        if self._last_state is None or self._push_callback is None:
            return

        # Copy, not mutate: this instance is the one already published.
        state = replace(self._last_state)
        if (value := frame.get("tempBoilerCoffe")) is not None:
            state.boiler_temperature = float(value)
        if (value := frame.get("pumpPress")) is not None:
            state.pump_pressure = value / 10
        if (value := frame.get("pumpServicesPress")) is not None:
            state.steam_pressure = value / 100
        if (value := frame.get("realtimeFlow")) is not None:
            state.realtime_flow = float(value)
        if (value := frame.get("counterVol")) is not None:
            state.shot_volume = int(value)
        if (value := frame.get("alarms")) is not None:
            state.alarms.update(
                {slug: bool(value & mask) for slug, mask in ALARM_BITS.items()}
            )

        self._last_state = state
        self._push_callback(state)

    # ── Commands ──────────────────────────────────────────────────────────────

    async def async_set_power(self, on: bool) -> None:
        """Switch on or into standby, preferring the REST endpoint."""
        if self._has_rest_api:
            await self.client.get_json(API_ACTION_ON if on else API_ACTION_STANDBY)
            return
        await self.client.set_value(11 if on else 12, 1)

    async def async_start_shot(self, mode: int) -> None:
        """Start a shot in profile ``mode`` (1-3) or manual (4)."""
        if not self._has_rest_api:
            raise NotImplementedError("Shot control needs firmware 0.12 or newer")
        if (name := SHOT_MODES.get(mode)) is None:
            raise ValueError(f"Unknown shot mode {mode}")
        await self.client.get_json(f"{API_ACTION_PREFIX}/{name}/start")

    async def async_stop_shot(self, mode: int) -> None:
        """Stop a shot in profile ``mode``."""
        if not self._has_rest_api:
            raise NotImplementedError("Shot control needs firmware 0.12 or newer")
        if (name := SHOT_MODES.get(mode)) is None:
            raise ValueError(f"Unknown shot mode {mode}")
        await self.client.get_json(f"{API_ACTION_PREFIX}/{name}/stop")

    async def async_set_boiler_setpoint(self, celsius: float) -> None:
        """Set the coffee boiler setpoint. The YOU takes tenths here."""
        await self.client.set_value(PARAM_COFFEE_BOILER_TEMP, round(celsius * 10))

    async def async_set_group_temperature(self, celsius: float) -> None:
        """Set the group / filter-holder setpoint."""
        await self.client.set_value(PARAM_GROUP_TEMP, round(celsius * 10))

    async def async_set_steam_pressure(self, bar: float) -> None:
        """Set the steam boiler pressure setpoint."""
        await self.client.set_value(PARAM_STEAM_BOILER_PRESSURE, round(bar * 10))

    async def async_set_scheduler_enabled(self, enabled: bool) -> None:
        """Flip the scheduler master switch."""
        await self.client.set_scheduler_enabled(enabled)

    async def async_set_scheduler_day_enabled(self, day: int, enabled: bool) -> None:
        """Enable one scheduler slot. The YOU indexes slots, not weekdays."""
        await self.client.set_scheduler_day_enabled(day, enabled)

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


def _apply_status(state: MachineState, status: list[int]) -> None:
    """Decode the ``status[]`` array."""

    def st(index: int) -> int | None:
        return status[index] if len(status) > index else None

    if (value := st(ST_MACHINE_STATUS)) is not None:
        state.is_on = value == MACHINE_STATUS_ON
        state.energy_saving_active = value in (
            MACHINE_STATUS_OFF,
            MACHINE_STATUS_ECO,
            MACHINE_STATUS_DEEP_SLEEP,
        )

    if (value := st(ST_ALARMS)) is not None:
        state.alarms = {slug: bool(value & mask) for slug, mask in ALARM_BITS.items()}
    if (value := st(ST_WARNINGS)) is not None:
        state.alarms.update(
            {slug: bool(value & mask) for slug, mask in WARNING_BITS.items()}
        )

    if (value := st(ST_GROUP_TEMP)) is not None:
        state.group_temperature = value / 10
    if (value := st(ST_HEATER_TEMP)) is not None:
        state.boiler_temperature = value / 10
    if (value := st(ST_SERVICE_HEATER_TEMP)) is not None:
        state.steam_temperature = float(value)
    if (value := st(ST_SERVICE_HEATER_PRESSURE)) is not None:
        state.steam_pressure = value / 100
    if (value := st(ST_PUMP_PRESSURE)) is not None:
        state.pump_pressure = value / 10
    if (value := st(ST_DOSE_TIME)) is not None:
        state.last_shot_time = value / 10
    if (value := st(ST_COUNTER_VOL)) is not None:
        state.shot_volume = value
    if (value := st(ST_REALTIME_FLOW)) is not None:
        state.realtime_flow = float(value)
    if (value := st(ST_LEVEL_SENSOR)) is not None:
        state.tank_level_ok = bool(value)
        state.water_source = WaterSource.TANK


def _apply_settings(state: MachineState, settings: list[int]) -> None:
    """Decode the ``settings[]`` setpoint array."""
    if len(settings) > SET_STEAM_HEATER:
        state.boiler_setpoint = settings[SET_STEAM_HEATER] / 10
    if len(settings) > SET_GROUP_TEMP:
        state.group_setpoint = settings[SET_GROUP_TEMP] / 10
    if len(settings) > SET_FILTER_HOLDER_TEMP:
        state.filter_holder_setpoint = settings[SET_FILTER_HOLDER_TEMP] / 10
    if len(settings) > SET_STEAM_PRESSURE:
        state.steam_pressure_setpoint = settings[SET_STEAM_PRESSURE] / 10


def _apply_counters(state: MachineState, payload: dict[str, Any]) -> None:
    """Decode the coffee counters."""
    state.coffees_today = _as_int(payload.get("dailyCoffee"))

    counters = payload.get("counters") or []
    if len(counters) >= COUNTER_PAIRS * 2:
        # The upstream project reads these high word first.
        state.coffees_total = sum(
            (counters[index] << 16) | counters[index + 1]
            for index in range(0, COUNTER_PAIRS * 2, 2)
        )


def _parse_scheduler(raw: list[int]) -> tuple[bool, list[ScheduleSlot]]:
    """Decode the YOU's 6-slot scheduler array."""
    if len(raw) < 17:
        return False, []

    enabled = bool(raw[SCHEDULER_IDX_ENABLED])
    slots: list[ScheduleSlot] = []

    for index in range(SCHEDULER_SLOTS):
        on_raw = raw[SCHEDULER_IDX_TIMES + index * 2]
        off_raw = raw[SCHEDULER_IDX_TIMES + index * 2 + 1]
        on_time = time(hour=min((on_raw >> 8) & 0xFF, 23), minute=on_raw & 0xFF)
        off_time = time(hour=min((off_raw >> 8) & 0xFF, 23), minute=off_raw & 0xFF)

        packed = raw[SCHEDULER_IDX_DAYS + index // 2]
        days = (packed >> 8) & 0xFF if index % 2 else packed & 0xFF

        for day in range(7):
            if days & (1 << day):
                slots.append(
                    ScheduleSlot(
                        day=day,
                        index=index,
                        enabled=True,
                        on_time=on_time,
                        off_time=off_time,
                    )
                )

    return enabled, slots


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
