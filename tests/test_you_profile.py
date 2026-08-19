"""Tests for the YOU profile."""

from __future__ import annotations

from datetime import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.sanremo.api import SanremoConnectionError
from custom_components.sanremo.const import Capability
from custom_components.sanremo.profiles.you import (
    PARAM_COFFEE_BOILER_TEMP,
    PARAM_GROUP_TEMP,
    PARAM_STEAM_BOILER_PRESSURE,
    YouProfile,
    _parse_scheduler,
)


def _status() -> list[int]:
    """Build a status array with each documented index set to a known value."""
    status = [0] * 32
    status[10] = 2  # status phase
    status[12] = 1  # machine status: on
    status[13] = 0  # alarms
    status[14] = 0  # warnings
    status[15] = 935  # group temperature, tenths
    status[16] = 1210  # heater temperature, tenths
    status[17] = 124  # service heater temperature
    status[18] = 130  # service heater pressure, hundredths
    status[19] = 90  # pump pressure, tenths
    status[20] = 36  # counter volume
    status[22] = 271  # dose time, tenths
    status[23] = 1  # level sensor
    status[30] = 2  # realtime flow
    return status


def _payload(**overrides: Any) -> dict[str, Any]:
    """Build a key=150 reply for a YOU."""
    payload: dict[str, Any] = {
        "key": 150,
        "name": "Kitchen YOU",
        "ssid": "TestNet",
        "rssi": -55,
        "ota": False,
        "status": _status(),
        "settings": [1210, 935, 900, 12],
        "counters": [0, 100, 0, 50, 0, 25, 0, 10, 0, 5, 0, 3, 0, 2],
        "dailyCoffee": 4,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def you_client(mock_client: AsyncMock) -> AsyncMock:
    """Reshape the mock client to answer like a YOU."""
    mock_client.probe_model_page.return_value = "you"
    mock_client.get_system_params.return_value = _payload()
    mock_client.get_read_only.return_value = {"key": 151, "rssi": -55, "ota": False}
    mock_client.get_device_info.return_value = {
        "key": 105,
        "name": "Kitchen YOU",
        "mac": "AABBCCDDEEFF",
        "fwVer": "0.13.000",
        "currentIp": "192.168.1.50",
    }
    return mock_client


@pytest.fixture
async def profile(you_client: AsyncMock) -> YouProfile:
    """Return a set-up YOU profile."""
    instance = YouProfile(you_client)
    await instance.async_setup()
    return instance


# ── Setup ──────────────────────────────────────────────────────────────────────


async def test_setup_identifies_a_you(profile: YouProfile) -> None:
    """Identity comes from key=105."""
    device = await profile.async_setup()

    assert device.name == "Kitchen YOU"
    assert device.mac == "AABBCCDDEEFF"
    assert device.model == "YOU"
    assert device.profile == "you"


async def test_rest_api_detected_when_present(profile: YouProfile) -> None:
    """Firmware 0.12+ answers /api/counters, unlocking shot control."""
    assert profile._has_rest_api is True


async def test_rest_api_absence_is_not_an_error(you_client: AsyncMock) -> None:
    """Older firmware has no /api surface, which is normal."""
    you_client.get_json.side_effect = SanremoConnectionError("404")

    instance = YouProfile(you_client)
    await instance.async_setup()

    assert instance._has_rest_api is False
    assert instance.supports(Capability.POWER)


async def test_you_supports_push(profile: YouProfile) -> None:
    """The YOU has a WebSocket; the Cube does not."""
    assert profile.supports_push() is True


# ── Decoding ───────────────────────────────────────────────────────────────────


async def test_poll_decodes_status_and_settings(profile: YouProfile) -> None:
    """Each documented index lands in the right normalised field."""
    state = await profile.async_poll()

    assert state.name == "Kitchen YOU"
    assert state.is_on is True
    assert state.energy_saving_active is False

    assert state.group_temperature == 93.5
    assert state.boiler_temperature == 121.0
    assert state.steam_temperature == 124.0
    assert state.steam_pressure == 1.3
    assert state.pump_pressure == 9.0
    assert state.last_shot_time == 27.1
    assert state.shot_volume == 36
    assert state.realtime_flow == 2.0
    assert state.tank_level_ok is True

    assert state.boiler_setpoint == 121.0
    assert state.group_setpoint == 93.5
    assert state.filter_holder_setpoint == 90.0
    assert state.steam_pressure_setpoint == 1.2

    assert state.coffees_today == 4


@pytest.mark.parametrize(
    ("machine_status", "is_on", "saving"),
    [(0, False, True), (1, True, False), (2, False, True), (3, False, True)],
)
async def test_machine_status_values(
    you_client: AsyncMock, machine_status: int, is_on: bool, saving: bool
) -> None:
    """0 off, 1 on, 2 ECO, 3 deep sleep."""
    status = _status()
    status[12] = machine_status
    you_client.get_system_params.return_value = _payload(status=status)

    instance = YouProfile(you_client)
    await instance.async_setup()
    state = await instance.async_poll()

    assert state.is_on is is_on
    assert state.energy_saving_active is saving


async def test_alarms_and_warnings_both_decode(you_client: AsyncMock) -> None:
    """Alarms and warnings live in different words but one alarm dict."""
    status = _status()
    status[13] = 1 << 1  # no tank level
    status[14] = 1 << 7  # machine locked
    you_client.get_system_params.return_value = _payload(status=status)

    instance = YouProfile(you_client)
    await instance.async_setup()
    state = await instance.async_poll()

    assert state.alarms["no_tank_level"] is True
    assert state.alarms["machine_locked"] is True
    assert state.alarms["no_tank"] is False
    assert state.active_alarms == ["machine_locked", "no_tank_level"]


async def test_short_arrays_do_not_crash(you_client: AsyncMock) -> None:
    """A truncated payload decodes to Nones rather than raising."""
    you_client.get_system_params.return_value = _payload(status=[0, 1], settings=[])

    instance = YouProfile(you_client)
    await instance.async_setup()
    state = await instance.async_poll()

    assert state.boiler_temperature is None
    assert state.boiler_setpoint is None


def test_scheduler_expands_slots_per_weekday() -> None:
    """A YOU slot carries a weekday mask, so it fans out into several days."""
    raw = [0] * 17
    raw[1] = 1  # scheduler enabled
    raw[2] = (7 << 8) | 0  # slot 0 on at 07:00
    raw[3] = (9 << 8) | 30  # slot 0 off at 09:30
    raw[14] = 0b0000011  # slot 0 active Monday and Tuesday

    enabled, slots = _parse_scheduler(raw)

    assert enabled is True
    assert len(slots) == 2
    assert {slot.day for slot in slots} == {0, 1}
    assert slots[0].on_time == time(7, 0)
    assert slots[0].off_time == time(9, 30)


def test_scheduler_rejects_a_short_array() -> None:
    """Too little data yields no schedule instead of an IndexError."""
    assert _parse_scheduler([0, 1]) == (False, [])


# ── Push ───────────────────────────────────────────────────────────────────────


async def test_push_frame_merges_into_state(profile: YouProfile) -> None:
    """A WebSocket frame updates live fields on the last polled state."""
    await profile.async_poll()

    received = []
    profile._push_callback = received.append
    profile._handle_frame(
        '{"tempBoilerCoffe":122.5,"pumpPress":85,"realtimeFlow":3,"counterVol":21}'
    )

    assert len(received) == 1
    state = received[0]
    assert state.boiler_temperature == 122.5
    assert state.pump_pressure == 8.5
    assert state.realtime_flow == 3.0
    assert state.shot_volume == 21


async def test_push_ignores_unrelated_frames(profile: YouProfile) -> None:
    """Frames that are not status broadcasts are dropped."""
    await profile.async_poll()

    received = []
    profile._push_callback = received.append
    profile._handle_frame('{"hello":"world"}')
    profile._handle_frame("not json at all")

    assert received == []


async def test_poll_keeps_push_only_fields(
    profile: YouProfile, you_client: AsyncMock
) -> None:
    """A poll must not blank out values that only the WebSocket provides."""
    await profile.async_poll()
    profile._push_callback = lambda state: None
    profile._handle_frame('{"tempBoilerCoffe":121.0,"realtimeFlow":4}')

    status = _status()
    status[30] = 0  # the poll reports no flow
    you_client.get_system_params.return_value = _payload(status=status)
    state = await profile.async_poll()

    assert state.realtime_flow is not None


# ── Commands ───────────────────────────────────────────────────────────────────


async def test_power_prefers_the_rest_endpoint(
    profile: YouProfile, you_client: AsyncMock
) -> None:
    """With /api available, power goes through it."""
    await profile.async_set_power(True)
    you_client.get_json.assert_awaited_with("/api/action/on")

    await profile.async_set_power(False)
    you_client.get_json.assert_awaited_with("/api/action/standby")


async def test_power_falls_back_to_set_value(you_client: AsyncMock) -> None:
    """Without /api, power falls back to key=200."""
    you_client.get_json.side_effect = SanremoConnectionError("404")
    instance = YouProfile(you_client)
    await instance.async_setup()

    you_client.get_json.side_effect = None
    await instance.async_set_power(True)

    you_client.set_value.assert_awaited_with(11, 1)


async def test_setpoints_send_tenths(
    profile: YouProfile, you_client: AsyncMock
) -> None:
    """Unlike the Cube's ID 1, the YOU's setpoints are all in tenths."""
    await profile.async_set_boiler_setpoint(121.0)
    you_client.set_value.assert_awaited_with(PARAM_COFFEE_BOILER_TEMP, 1210)

    await profile.async_set_group_temperature(93.5)
    you_client.set_value.assert_awaited_with(PARAM_GROUP_TEMP, 935)

    await profile.async_set_steam_pressure(1.2)
    you_client.set_value.assert_awaited_with(PARAM_STEAM_BOILER_PRESSURE, 12)


async def test_shot_control(profile: YouProfile, you_client: AsyncMock) -> None:
    """Shot profiles map onto the REST action paths."""
    await profile.async_start_shot(1)
    you_client.get_json.assert_awaited_with("/api/action/p1/start")

    await profile.async_stop_shot(4)
    you_client.get_json.assert_awaited_with("/api/action/man/stop")


async def test_shot_control_rejects_a_bad_mode(profile: YouProfile) -> None:
    """An unknown profile number is an error, not a silent no-op."""
    with pytest.raises(ValueError, match="Unknown shot mode"):
        await profile.async_start_shot(9)


async def test_shot_control_needs_the_rest_api(you_client: AsyncMock) -> None:
    """Older firmware cannot start shots and says so."""
    you_client.get_json.side_effect = SanremoConnectionError("404")
    instance = YouProfile(you_client)
    await instance.async_setup()

    with pytest.raises(NotImplementedError, match=r"firmware 0\.12"):
        await instance.async_start_shot(1)
