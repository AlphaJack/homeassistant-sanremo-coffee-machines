"""Tests for the YOU profile, against captured payloads."""

from __future__ import annotations

import copy
from datetime import time
import json
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_HOST, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sanremo.api import SanremoConnectionError
from custom_components.sanremo.const import DOMAIN, Capability
from custom_components.sanremo.profiles.you import (
    PARAM_COFFEE_BOILER_TEMP,
    PARAM_GROUP_TEMP,
    PARAM_STEAM_BOILER_PRESSURE,
    ST_ALARMS,
    ST_GROUP_STATUS,
    ST_LEVEL_SENSOR,
    ST_MACHINE_STATUS,
    ST_PUMP_PRESSURE,
    ST_REALTIME_FLOW,
    ST_WARNINGS,
    YouProfile,
    _parse_scheduler,
)


def _with_status(payload: dict[str, Any], registers: dict[int, int]) -> dict[str, Any]:
    """Return a copy of a ``key=150`` reply with some status registers changed."""
    changed = copy.deepcopy(payload)
    for index, value in registers.items():
        changed["status"][index] = value
    return changed


def _frame(payload: dict[str, Any], **overrides: Any) -> str:
    """Return a WebSocket frame as the machine sends it, with some keys changed."""
    return json.dumps({**payload, **overrides})


@pytest.fixture
def you_client(
    mock_client: AsyncMock, you_payloads: dict[str, dict[str, Any]]
) -> AsyncMock:
    """Reshape the mock client to answer like the captured YOU."""
    mock_client.probe_model_page.return_value = "you"
    mock_client.get_device_info.return_value = you_payloads["105"]
    mock_client.get_system_params.return_value = you_payloads["150"]
    mock_client.get_read_only.return_value = you_payloads["151"]
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

    assert device.name == "SANREMO-YOU"
    assert device.mac == "AABBCCDDEEFF"
    assert device.wifi_firmware == "0.14.000"
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


async def test_poll_decodes_the_captured_machine(profile: YouProfile) -> None:
    """Readings match what the machine itself showed at capture time.

    The WebSocket frame of the same second carries the two boiler temperatures
    already in °C (42.2 and 45.2) and the steam pressure as 1 hundredth of a bar.
    """
    state = await profile.async_poll()

    assert state.name == "SANREMO-YOU"
    assert state.is_on is False
    assert state.energy_saving_active is True

    assert state.group_temperature == 31.2
    assert state.boiler_temperature == 42.2
    assert state.steam_temperature == 45.2
    assert state.steam_pressure == 0.01
    assert state.pump_pressure == 0.0
    assert state.last_shot_time == 17.2

    assert state.coffees_today == 4


async def test_poll_and_push_agree_on_temperatures(
    profile: YouProfile, you_payloads: dict[str, dict[str, Any]]
) -> None:
    """status[16] and status[17] are tenths of what the WebSocket sends in °C."""
    polled = await profile.async_poll()

    received = []
    profile._push_callback = received.append
    profile._handle_frame(_frame(you_payloads["ws"]))

    assert received[0].boiler_temperature == polled.boiler_temperature
    assert received[0].steam_temperature == polled.steam_temperature
    assert received[0].steam_pressure == polled.steam_pressure


async def test_setpoints_follow_the_vendor_app(profile: YouProfile) -> None:
    """The coffee boiler setpoint is settings[1], not the steam boiler's [0].

    The vendor app showed 91.0 as the boiler temperature, 90.0 as the group
    temperature and 1.7 bar as the steam pressure for this capture.
    """
    state = await profile.async_poll()

    assert state.boiler_setpoint == 91.0
    assert state.group_setpoint == 90.0
    assert state.steam_temperature_setpoint == 128.0
    assert state.steam_pressure_setpoint == 1.7

    # Bounds of the vendor's own boiler control, not the Cube's 115-126.
    assert state.boiler_setpoint_min == 80.0
    assert state.boiler_setpoint_max == 100.0


async def test_flow_of_the_last_shot_is_not_a_running_shot(profile: YouProfile) -> None:
    """An idle machine still reports the last shot's flow; that is not brewing.

    The capture holds flow 22 and volume 640 from a shot pulled hours earlier on
    dose key 3, programmed to 64, with the group idle and the machine switched off.
    """
    state = await profile.async_poll()

    assert state.brewing is False
    assert state.realtime_flow == 0.0
    assert state.shot_volume == 64.0


async def test_running_shot_reports_flow_in_tenths(
    you_client: AsyncMock, you_payloads: dict[str, dict[str, Any]]
) -> None:
    """With the group active, flow is live and scaled like the volume."""
    you_client.get_system_params.return_value = _with_status(
        you_payloads["150"],
        {
            ST_MACHINE_STATUS: 1,
            ST_GROUP_STATUS: 2,
            ST_PUMP_PRESSURE: 90,
            ST_REALTIME_FLOW: 25,
        },
    )

    instance = YouProfile(you_client)
    await instance.async_setup()
    state = await instance.async_poll()

    assert state.brewing is True
    assert state.realtime_flow == 2.5
    assert state.pump_pressure == 9.0


async def test_coffees_total_leaves_out_steam_and_tea(profile: YouProfile) -> None:
    """Keys 6 + 182 + 12, continuous 8 and paddle 23; not steam 115 or tea 6."""
    state = await profile.async_poll()

    assert state.coffees_total == 231


@pytest.mark.parametrize(
    ("machine_status", "level", "expected"),
    [
        (1, 1, True),
        (1, 0, False),
        (0, 1, True),
        # Off, ECO and deep sleep: a 0 says nothing about the tank.
        (0, 0, None),
        (2, 0, None),
        (3, 0, None),
    ],
)
async def test_tank_level_only_counts_while_on(
    you_client: AsyncMock,
    you_payloads: dict[str, dict[str, Any]],
    machine_status: int,
    level: int,
    expected: bool | None,
) -> None:
    """The level sensor reads 0 whenever the machine is off."""
    you_client.get_system_params.return_value = _with_status(
        you_payloads["150"],
        {ST_MACHINE_STATUS: machine_status, ST_LEVEL_SENSOR: level},
    )

    instance = YouProfile(you_client)
    await instance.async_setup()
    state = await instance.async_poll()

    assert state.tank_level_ok is expected


@pytest.mark.parametrize("level_while_on", [0, 1])
async def test_tank_level_is_kept_across_power_off(
    you_client: AsyncMock,
    you_payloads: dict[str, dict[str, Any]],
    level_while_on: int,
) -> None:
    """Switching off must not turn a known tank state into an unknown one."""
    you_client.get_system_params.return_value = _with_status(
        you_payloads["150"],
        {ST_MACHINE_STATUS: 1, ST_LEVEL_SENSOR: level_while_on},
    )
    instance = YouProfile(you_client)
    await instance.async_setup()
    before = await instance.async_poll()

    # The capture as taken: off, level sensor at 0.
    you_client.get_system_params.return_value = you_payloads["150"]
    after = await instance.async_poll()

    assert after.is_on is False
    assert after.tank_level_ok is before.tank_level_ok is bool(level_while_on)


@pytest.mark.parametrize(
    ("machine_status", "is_on", "saving"),
    [(0, False, True), (1, True, False), (2, False, True), (3, False, True)],
)
async def test_machine_status_values(
    you_client: AsyncMock,
    you_payloads: dict[str, dict[str, Any]],
    machine_status: int,
    is_on: bool,
    saving: bool,
) -> None:
    """0 off, 1 on, 2 ECO, 3 deep sleep."""
    you_client.get_system_params.return_value = _with_status(
        you_payloads["150"], {ST_MACHINE_STATUS: machine_status}
    )

    instance = YouProfile(you_client)
    await instance.async_setup()
    state = await instance.async_poll()

    assert state.is_on is is_on
    assert state.energy_saving_active is saving


async def test_alarms_and_warnings_both_decode(
    you_client: AsyncMock, you_payloads: dict[str, dict[str, Any]]
) -> None:
    """Alarms and warnings live in different words but one alarm dict."""
    you_client.get_system_params.return_value = _with_status(
        you_payloads["150"],
        {
            ST_ALARMS: 1 << 1,  # no tank level
            ST_WARNINGS: 1 << 7,  # machine locked
        },
    )

    instance = YouProfile(you_client)
    await instance.async_setup()
    state = await instance.async_poll()

    assert state.alarms["no_tank_level"] is True
    assert state.alarms["machine_locked"] is True
    assert state.alarms["no_tank"] is False
    assert state.active_alarms == ["machine_locked", "no_tank_level"]


async def test_short_arrays_do_not_crash(
    you_client: AsyncMock, you_payloads: dict[str, dict[str, Any]]
) -> None:
    """A truncated payload decodes to Nones rather than raising."""
    you_client.get_system_params.return_value = {
        **you_payloads["150"],
        "status": [0, 1],
        "settings": [],
        "counters": [],
    }

    instance = YouProfile(you_client)
    await instance.async_setup()
    state = await instance.async_poll()

    assert state.boiler_temperature is None
    assert state.boiler_setpoint is None
    assert state.brewing is None
    assert state.tank_level_ok is None
    assert state.coffees_total is None


async def test_captured_schedule_decodes(profile: YouProfile) -> None:
    """Slot 0 runs on weekdays and slot 1 at the weekend, as set on the machine."""
    state = await profile.async_poll()

    assert state.scheduler_enabled is True
    assert {slot.day for slot in state.schedule if slot.index == 0} == {0, 1, 2, 3, 4}
    assert {slot.day for slot in state.schedule if slot.index == 1} == {5, 6}
    weekday = next(slot for slot in state.schedule if slot.index == 0)
    assert weekday.on_time == time(6, 20)
    assert weekday.off_time == time(15, 37)


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


async def test_push_frame_of_an_idle_machine(
    profile: YouProfile, you_payloads: dict[str, dict[str, Any]]
) -> None:
    """The captured frame: off, group idle, last shot's flow still on the wire."""
    await profile.async_poll()

    received = []
    profile._push_callback = received.append
    profile._handle_frame(_frame(you_payloads["ws"]))

    assert len(received) == 1
    state = received[0]
    assert state.boiler_temperature == 42.2
    assert state.steam_temperature == 45.2
    assert state.steam_pressure == 0.01
    assert state.brewing is False
    assert state.realtime_flow == 0.0
    assert state.shot_volume == 64.0


async def test_push_frame_of_a_running_shot(
    profile: YouProfile, you_payloads: dict[str, dict[str, Any]]
) -> None:
    """A frame with the group active updates the live fields, in tenths."""
    await profile.async_poll()

    received = []
    profile._push_callback = received.append
    profile._handle_frame(
        _frame(
            you_payloads["ws"],
            status=1,
            groupStatus=2,
            tempBoilerCoffe=90.8,
            tempBolierServices=128.1,
            pumpPress=85,
            realtimeFlow=25,
            counterVol=213,
        )
    )

    assert len(received) == 1
    state = received[0]
    assert state.boiler_temperature == 90.8
    assert state.steam_temperature == 128.1
    assert state.pump_pressure == 8.5
    assert state.brewing is True
    assert state.realtime_flow == 2.5
    assert state.shot_volume == 21.3


async def test_push_ignores_unrelated_frames(profile: YouProfile) -> None:
    """Frames that are not status broadcasts are dropped."""
    await profile.async_poll()

    received = []
    profile._push_callback = received.append
    profile._handle_frame('{"hello":"world"}')
    profile._handle_frame("not json at all")

    assert received == []


async def test_poll_keeps_push_only_fields(
    profile: YouProfile,
    you_client: AsyncMock,
    you_payloads: dict[str, dict[str, Any]],
) -> None:
    """A poll must not blank out values that only the WebSocket provides."""
    await profile.async_poll()
    profile._push_callback = lambda state: None
    profile._handle_frame(
        _frame(you_payloads["ws"], groupStatus=2, realtimeFlow=40, pumpPress=85)
    )

    # A reply that stops short of the pump, volume and flow registers.
    you_client.get_system_params.return_value = {
        **you_payloads["150"],
        "status": you_payloads["150"]["status"][:ST_PUMP_PRESSURE],
    }
    state = await profile.async_poll()

    assert state.realtime_flow == 4.0
    assert state.pump_pressure == 8.5
    assert state.shot_volume == 64.0


# ── Entities ───────────────────────────────────────────────────────────────────


async def test_entities_of_a_switched_off_you(
    hass: HomeAssistant, you_client: AsyncMock
) -> None:
    """End to end: the captured machine, set up while it is switched off."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="SANREMO-YOU (YOU)",
        data={CONF_HOST: "192.168.1.50"},
        unique_id="aa:bb:cc:dd:ee:ff",
    )
    entry.add_to_hass(hass)

    # No WebSocket in tests: the listener would otherwise retry for ever.
    with patch.object(YouProfile, "_listen", AsyncMock()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert hass.states.get("sensor.sanremo_you_boiler_temperature").state == "42.2"
    assert (
        hass.states.get("sensor.sanremo_you_steam_boiler_temperature").state == "45.2"
    )
    assert hass.states.get("sensor.sanremo_you_coffees_total").state == "231"

    boiler = hass.states.get("water_heater.sanremo_you")
    assert boiler.attributes["current_temperature"] == 42.2
    assert boiler.attributes["brew_setpoint"] == 91.0
    assert boiler.attributes["min_temp"] == 80.0
    assert boiler.attributes["max_temp"] == 100.0

    # Off at startup: the tank sensor exists, but has nothing to report yet.
    assert (
        hass.states.get("binary_sensor.sanremo_you_water_tank_empty").state
        == STATE_UNKNOWN
    )
    # Brewing needs live telemetry, and there is no WebSocket here.
    assert (
        hass.states.get("binary_sensor.sanremo_you_brewing").state == STATE_UNAVAILABLE
    )

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


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
    await profile.async_set_boiler_setpoint(91.0)
    you_client.set_value.assert_awaited_with(PARAM_COFFEE_BOILER_TEMP, 910)

    await profile.async_set_group_temperature(90.0)
    you_client.set_value.assert_awaited_with(PARAM_GROUP_TEMP, 900)

    await profile.async_set_steam_pressure(1.7)
    you_client.set_value.assert_awaited_with(PARAM_STEAM_BOILER_PRESSURE, 17)


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
