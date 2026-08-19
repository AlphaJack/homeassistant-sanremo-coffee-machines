"""Decoding tests for the Cube profile, against captured payloads."""

from __future__ import annotations

from datetime import time
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
import pytest

from custom_components.sanremo.const import Capability, EnergySavingMode, WaterSource
from custom_components.sanremo.profiles.cube import (
    PARAM_BOILER_SETPOINT,
    PARAM_ECO_SETPOINT,
    PARAM_POWER_ON,
    PARAM_POWER_STANDBY,
    CubeProfile,
    _dow_for_row,
    _quarters_to_time,
    _registers,
    _u32,
)


@pytest.fixture
async def profile(mock_client: AsyncMock) -> CubeProfile:
    """Return a set-up Cube profile."""
    instance = CubeProfile(mock_client)
    await instance.async_setup()
    return instance


# ── Pure decoding ──────────────────────────────────────────────────────────────


def test_registers_placed_by_index() -> None:
    """Registers are placed by their stated index, not by arrival order."""
    assert _registers({"registers": [[2, 30], [0, 10], [1, 20]]}) == [10, 20, 30]


def test_registers_handles_missing() -> None:
    """A reply without registers decodes to an empty list."""
    assert _registers({}) == []
    assert _registers({"registers": []}) == []


def test_u32_uses_a_shift_not_an_or() -> None:
    """32-bit counters combine as (hi << 16) | lo."""
    registers = [0] * 40
    registers[31], registers[32] = 3392, 3  # dispensed
    registers[33], registers[34] = 34464, 1  # into boiler
    registers[35], registers[36] = 37856, 4  # total

    dispensed = _u32(registers, 31, 32)
    to_boiler = _u32(registers, 33, 34)
    total = _u32(registers, 35, 36)

    assert dispensed == 200000
    assert to_boiler == 100000
    assert total == 300000
    assert dispensed + to_boiler == total


def test_u32_out_of_range() -> None:
    """A short register file yields None rather than an IndexError."""
    assert _u32([1, 2], 5, 6) is None


@pytest.mark.parametrize(
    ("quarters", "expected"),
    [
        (28, time(7, 0)),
        (38, time(9, 30)),
        (72, time(18, 0)),
        (90, time(22, 30)),
        (0, time(0, 0)),
    ],
)
def test_quarters_to_time(quarters: int, expected: time) -> None:
    """Quarter-hour counts decode to wall-clock times."""
    assert _quarters_to_time(quarters) == (expected, False)


def test_quarters_to_time_handles_24_00() -> None:
    """96 quarters means 24:00, which datetime.time cannot represent."""
    assert _quarters_to_time(96) == (time(0, 0), True)


def test_dow_for_row_bridges_the_two_conventions() -> None:
    """Rows are Monday-first; stored day values are Sunday-first."""
    assert _dow_for_row(0) == 1  # Monday row -> Monday value
    assert _dow_for_row(5) == 6  # Saturday
    assert _dow_for_row(6) == 0  # Sunday wraps to 0


# ── Setup and capabilities ─────────────────────────────────────────────────────


async def test_setup_identifies_a_cube_r(profile: CubeProfile) -> None:
    """The captured machine is a Cube R on board 1.26."""
    device = await profile.async_setup()

    assert device.name == "cube1"
    assert device.mac == "AABBCCDDEEFF"
    assert device.model == "Cube R"
    assert device.wifi_firmware == "0.24.000"
    assert device.board_firmware == "1.26"
    assert device.profile == "cube"


async def test_capabilities_for_board_1_26(profile: CubeProfile) -> None:
    """Board 1.26 with config 2 unlocks the full feature set."""
    assert profile.supports(Capability.POWER)
    assert profile.supports(Capability.BOILER_SETPOINT)  # config == 2
    assert profile.supports(Capability.ECO_SETPOINT)  # board > 1.14
    assert profile.supports(Capability.ENERGY_SAVING_MODE)
    assert profile.supports(Capability.ENERGY_SAVING_DELAY)
    assert profile.supports(Capability.STEAM_BOOSTER)
    assert profile.supports(Capability.SCHEDULER)


async def test_plain_cube_has_no_boiler_setpoint(
    mock_client: AsyncMock, cube_payloads: dict
) -> None:
    """A machine reporting config != 2 hides the boiler setpoint."""
    payload = dict(cube_payloads["151"])
    payload["config"] = 1
    mock_client.get_read_only.return_value = payload

    profile = CubeProfile(mock_client)
    device = await profile.async_setup()

    assert not profile.supports(Capability.BOILER_SETPOINT)
    assert device.model == "Cube"


async def test_old_board_hides_v2_features(
    mock_client: AsyncMock, cube_payloads: dict
) -> None:
    """Board firmware at or below 1.14 has no ECO setpoint registers."""
    payload = dict(cube_payloads["150"])
    payload["ver"] = 1.14
    mock_client.get_system_params.return_value = payload

    profile = CubeProfile(mock_client)
    await profile.async_setup()

    assert not profile.supports(Capability.ECO_SETPOINT)
    assert not profile.supports(Capability.STEAM_BOOSTER)
    assert profile.supports(Capability.POWER)


async def test_short_register_file_does_not_crash(
    mock_client: AsyncMock, cube_payloads: dict
) -> None:
    """A truncated reply degrades instead of raising."""
    payload = dict(cube_payloads["152"])
    payload["registers"] = payload["registers"][:20]
    mock_client.get_read_write.return_value = payload

    profile = CubeProfile(mock_client)
    await profile.async_setup()
    state = await profile.async_poll()

    assert state.eco_setpoint is None
    assert state.boiler_setpoint == 121.0


# ── Polling ────────────────────────────────────────────────────────────────────


async def test_poll_decodes_the_captured_machine(profile: CubeProfile) -> None:
    """Every mapped value matches what the machine reported."""
    state = await profile.async_poll()

    # Identity and connectivity
    assert state.name == "cube1"
    assert state.firmware_update_available is False

    # Temperatures
    assert state.boiler_temperature == 121.0
    assert state.boiler_setpoint == 121.0
    assert state.eco_setpoint == 95.0
    # The vendor's own table maps a 121 degree setpoint to 92.4 at the group.
    assert state.estimated_brew_temperature == 92.4
    assert state.boiler_setpoint_min == 115.0
    assert state.boiler_setpoint_max == 126.0

    # Status word 99 = tank ok | boiler ok | ready, energy saving clear
    assert state.is_on is True
    assert state.energy_saving_active is False
    assert state.ready is True
    assert state.tank_level_ok is True
    assert state.boiler_level_ok is True
    assert state.water_source is WaterSource.TANK

    # Brewing
    assert state.last_shot_time == 23.1

    # Counters
    assert state.coffees_today == 2
    assert state.coffees_week == 20
    assert state.coffees_month == 80
    assert state.coffees_year == 500
    assert state.coffees_total == 2500
    assert state.water_dispensed_ml == 200000
    assert state.water_to_boiler_ml == 100000
    assert state.water_total_ml == 300000

    # Setup flags: 152[17] == 16, so only the Wi-Fi circuit bit is set
    assert state.scheduler_enabled is False
    assert state.steam_booster_enabled is False
    assert state.standby_after_last_coffee is False
    assert state.display_temperature_fahrenheit is False
    assert state.energy_saving_mode is EnergySavingMode.STANDBY
    assert state.energy_saving_delay == 3600

    # No alarms were active
    assert state.has_active_alarm is False
    assert state.active_alarms == []


async def test_rssi_prefers_the_fresher_reply(
    mock_client: AsyncMock, cube_payloads: dict
) -> None:
    """key=151 is polled every cycle, so its RSSI wins over key=150's."""
    stale = dict(cube_payloads["150"]) | {"rssi": -99}
    fresh = dict(cube_payloads["151"]) | {"rssi": -42}
    mock_client.get_system_params.return_value = stale
    mock_client.get_read_only.return_value = fresh

    profile = CubeProfile(mock_client)
    await profile.async_setup()
    state = await profile.async_poll()

    assert state.rssi == -42


async def test_poll_decodes_the_schedule(profile: CubeProfile) -> None:
    """The 21 scheduler rows decode to the machine's real programme."""
    state = await profile.async_poll()

    assert len(state.schedule) == 21
    assert state.schedule_days_enabled == [True] * 7

    monday = [slot for slot in state.schedule if slot.day == 0]
    assert [(slot.enabled, slot.on_time, slot.off_time) for slot in monday] == [
        (True, time(7, 0), time(9, 30)),
        (True, time(18, 0), time(22, 30)),
        (False, time(18, 30), time(23, 0)),
    ]

    # Friday opens earlier, and the weekend runs straight through
    friday = [slot for slot in state.schedule if slot.day == 4]
    assert (friday[0].on_time, friday[0].off_time) == (time(6, 0), time(22, 30))
    sunday = [slot for slot in state.schedule if slot.day == 6]
    assert (sunday[0].on_time, sunday[0].off_time) == (time(7, 0), time(22, 30))


async def test_poll_parses_the_clock(profile: CubeProfile) -> None:
    """The RTC array decodes, and a zero weekday flags an unset clock."""
    state = await profile.async_poll()

    assert state.machine_time is not None
    assert state.machine_time.year == 2026
    assert state.machine_time.month == 1
    assert state.machine_weekday_known is True


async def test_alarms_decode_from_the_bitfield(
    mock_client: AsyncMock, cube_payloads: dict
) -> None:
    """Alarm bits map to their slugs, and the filter bit drives the flag."""
    payload = dict(cube_payloads["151"])
    registers = [list(pair) for pair in payload["registers"]]
    registers[14][1] = (1 << 7) | (1 << 2)  # filter required + filling timeout
    payload["registers"] = registers
    mock_client.get_read_only.return_value = payload

    profile = CubeProfile(mock_client)
    await profile.async_setup()
    state = await profile.async_poll()

    assert state.alarms["e08_filter_replacement_required"] is True
    assert state.alarms["e01_boiler_filling_timeout"] is True
    assert state.alarms["e06_eeprom_corrupted"] is False
    assert state.filter_change_required is True
    assert state.active_alarms == [
        "e01_boiler_filling_timeout",
        "e08_filter_replacement_required",
    ]


async def test_tank_prealarm_suppressed_by_hard_alarm(
    mock_client: AsyncMock, cube_payloads: dict
) -> None:
    """The pre-alarm is hidden once the hard low-water alarm fires."""
    payload = dict(cube_payloads["151"])
    registers = [list(pair) for pair in payload["registers"]]
    registers[12][1] = 0b100  # pre-alarm bit set
    registers[14][1] = 1 << 8  # water level low
    payload["registers"] = registers
    mock_client.get_read_only.return_value = payload

    profile = CubeProfile(mock_client)
    await profile.async_setup()
    state = await profile.async_poll()

    assert state.alarms["water_level_low"] is True
    assert state.tank_level_low_warning is False


# ── Commands ───────────────────────────────────────────────────────────────────


async def test_power_uses_the_verified_ids(
    profile: CubeProfile, mock_client: AsyncMock
) -> None:
    """ID 11 powers on and ID 12 goes to standby, as verified on hardware."""
    await profile.async_set_power(True)
    mock_client.set_value.assert_awaited_with(PARAM_POWER_ON, 1)

    await profile.async_set_power(False)
    mock_client.set_value.assert_awaited_with(PARAM_POWER_STANDBY, 1)


async def test_boiler_setpoint_sends_whole_degrees(
    profile: CubeProfile, mock_client: AsyncMock
) -> None:
    """ID 1 takes whole degrees even though it reads back in tenths."""
    await profile.async_set_boiler_setpoint(121.4)
    mock_client.set_value.assert_awaited_with(PARAM_BOILER_SETPOINT, 121)


async def test_boiler_setpoint_is_clamped(
    profile: CubeProfile, mock_client: AsyncMock
) -> None:
    """Out-of-range setpoints are clamped to the machine's limits."""
    await profile.async_set_boiler_setpoint(200)
    mock_client.set_value.assert_awaited_with(PARAM_BOILER_SETPOINT, 126)

    await profile.async_set_boiler_setpoint(20)
    mock_client.set_value.assert_awaited_with(PARAM_BOILER_SETPOINT, 115)


async def test_eco_setpoint_sends_tenths(
    profile: CubeProfile, mock_client: AsyncMock
) -> None:
    """ID 26 takes tenths of a degree -- the opposite of ID 1."""
    await profile.async_set_eco_setpoint(95.0)
    mock_client.set_value.assert_awaited_with(PARAM_ECO_SETPOINT, 950)


async def test_eco_setpoint_clamped_to_vendor_range(
    profile: CubeProfile, mock_client: AsyncMock
) -> None:
    """The vendor's own control allows 80-100 degrees, nothing wider."""
    await profile.async_set_eco_setpoint(200)
    mock_client.set_value.assert_awaited_with(PARAM_ECO_SETPOINT, 1000)

    await profile.async_set_eco_setpoint(20)
    mock_client.set_value.assert_awaited_with(PARAM_ECO_SETPOINT, 800)


async def test_energy_saving_mode_sends_no_value(
    profile: CubeProfile, mock_client: AsyncMock
) -> None:
    """IDs 8 and 9 take no argument."""
    await profile.async_set_energy_saving_mode(eco=True)
    mock_client.set_value.assert_awaited_with(8, None)

    await profile.async_set_energy_saving_mode(eco=False)
    mock_client.set_value.assert_awaited_with(9, None)


async def test_set_schedule_day_addresses_the_right_row(
    profile: CubeProfile, mock_client: AsyncMock
) -> None:
    """Tuesday's row index is 1 while its stored day value is 2."""
    await profile.async_set_schedule_day(
        1, [(True, time(7, 0), time(9, 30)), (True, time(18, 0), time(22, 30))]
    )

    row, slots, copy_to = mock_client.save_scheduler_day.await_args.args
    assert row == 1  # Tuesday's row
    assert slots[0]["dow"] == 2  # Tuesday's day-of-week value
    assert slots[0]["on_hour"] == 7
    assert slots[0]["off_minute"] == 30
    assert slots[2]["dow"] == 7  # the omitted third slot is disabled
    assert copy_to == [False] * 7


async def test_set_schedule_day_rounds_to_the_machine_grid(
    profile: CubeProfile, mock_client: AsyncMock
) -> None:
    """Minutes snap onto the machine's 15-minute resolution."""
    await profile.async_set_schedule_day(0, [(True, time(7, 7), time(9, 52))])

    _, slots, _ = mock_client.save_scheduler_day.await_args.args
    assert slots[0]["on_minute"] == 0
    assert slots[0]["off_minute"] == 45


async def test_set_schedule_day_rejects_a_bad_day(profile: CubeProfile) -> None:
    """An out-of-range weekday is a programming error, not a silent no-op."""
    with pytest.raises(ValueError, match=r"day must be 0\.\.6"):
        await profile.async_set_schedule_day(9, [])


async def test_scheduler_day_enabled_converts_the_day(
    profile: CubeProfile, mock_client: AsyncMock
) -> None:
    """key=250 uses the Sunday-first convention."""
    await profile.async_set_scheduler_day_enabled(6, True)  # Sunday
    mock_client.set_scheduler_day_enabled.assert_awaited_with(0, True)


async def test_sync_clock_writes_local_time(
    hass: HomeAssistant, profile: CubeProfile, mock_client: AsyncMock
) -> None:
    """The clock is set from Home Assistant's local time."""
    await profile.async_sync_clock()
    assert mock_client.set_clock.await_count == 1
    assert len(mock_client.set_clock.await_args.args) == 5
