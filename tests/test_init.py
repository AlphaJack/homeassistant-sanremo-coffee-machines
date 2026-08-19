"""End-to-end setup, entity and command tests."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

from homeassistant.components.calendar import DOMAIN as CALENDAR_DOMAIN
from homeassistant.components.water_heater import (
    ATTR_OPERATION_MODE,
    DOMAIN as WATER_HEATER_DOMAIN,
    SERVICE_SET_OPERATION_MODE,
    STATE_ECO,
    STATE_PERFORMANCE,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.sanremo.api import SanremoConnectionError
from custom_components.sanremo.const import DOMAIN, SERVICE_SET_SCHEDULE_DAY
from custom_components.sanremo.profiles.cube import (
    PARAM_BOILER_SETPOINT,
    PARAM_POWER_ON,
    PARAM_POWER_STANDBY,
    PARAM_SELECT_ECO,
    PARAM_SELECT_STANDBY,
)


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> MockConfigEntry:
    """Set up the integration against the captured Cube."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


# ── Lifecycle ──────────────────────────────────────────────────────────────────


async def test_setup_and_unload(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The entry loads and unloads cleanly."""
    assert setup_integration.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(setup_integration.entry_id)
    await hass.async_block_till_done()
    assert setup_integration.state is ConfigEntryState.NOT_LOADED


async def test_setup_retries_when_offline(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """An unreachable machine leaves the entry in retry, not failed."""
    mock_client.probe_model_page.side_effect = SanremoConnectionError("timeout")
    mock_client.get_device_info.side_effect = SanremoConnectionError("timeout")
    mock_client.get_read_only.side_effect = SanremoConnectionError("timeout")
    mock_client.get_system_params.side_effect = SanremoConnectionError("timeout")

    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_device_registry_entry(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The machine is registered as one device with both firmware versions."""
    registry = dr.async_get(hass)
    device = registry.async_get_device(identifiers={(DOMAIN, "aa:bb:cc:dd:ee:ff")})

    assert device is not None
    assert device.name == "cube1"
    assert device.manufacturer == "Sanremo"
    assert device.model == "Cube R"
    assert device.sw_version == "0.24.000"  # Wi-Fi module
    assert device.hw_version == "1.26"  # machine board
    assert device.configuration_url == "http://192.168.1.10"
    assert (dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff") in device.connections


async def test_entities_become_unavailable_when_polling_fails(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    setup_integration: MockConfigEntry,
    freezer,
) -> None:
    """A machine that stops answering marks its entities unavailable."""
    assert hass.states.get("sensor.cube1_boiler_temperature").state == "121.0"

    mock_client.get_read_only.side_effect = SanremoConnectionError("gone")
    freezer.tick(timedelta(seconds=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.cube1_boiler_temperature").state == STATE_UNAVAILABLE


# ── Entity surface ─────────────────────────────────────────────────────────────


async def test_expected_entities_exist(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The Cube R exposes its full mapped feature set."""
    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, setup_integration.entry_id)
    keys = {entry.unique_id.removeprefix("aa:bb:cc:dd:ee:ff_") for entry in entries}

    # Controls
    assert {"power", "steam_booster", "scheduler", "standby_after_last_coffee"} <= keys
    assert {"boiler_setpoint", "eco_setpoint", "energy_saving_delay"} <= keys
    assert {"energy_saving_mode"} <= keys
    assert {"reset_filter", "sync_clock", "check_firmware", "reboot"} <= keys
    assert {"boiler", "schedule"} <= keys

    # Readings
    assert {
        "boiler_temperature",
        "last_shot_time",
        "coffees_today",
        "coffees_total",
    } <= keys
    assert {"water_dispensed", "water_total"} <= keys
    assert {"ready", "alarm", "tank_level_ok"} <= keys

    # Per-weekday schedule switches
    assert {f"scheduler_{day}" for day in ("monday", "sunday")} <= keys


async def test_filter_entities_hidden_when_monitoring_is_off(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """With no filter interval set, the derived filter sensors are not created."""
    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, setup_integration.entry_id)
    keys = {entry.unique_id.removeprefix("aa:bb:cc:dd:ee:ff_") for entry in entries}

    assert "filter_days_remaining" not in keys
    assert "filter_life" not in keys
    # The interval control and reset button remain, so it can be turned on.
    assert "filter_interval" in keys
    assert "reset_filter" in keys


async def test_no_raw_registers_on_a_mapped_machine(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """A machine whose registers are mapped does not get 100+ raw sensors."""
    registry = er.async_get(hass)
    entry = registry.async_get_entity_id(
        Platform.SENSOR, DOMAIN, "aa:bb:cc:dd:ee:ff_register_151_17"
    )
    assert entry is None


async def test_sensor_values(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Sensor states match the captured machine."""
    assert hass.states.get("sensor.cube1_boiler_temperature").state == "121.0"
    assert hass.states.get("sensor.cube1_last_shot_time").state == "23.1"
    assert hass.states.get("sensor.cube1_coffees_today").state == "2"
    assert hass.states.get("sensor.cube1_coffees_total").state == "2500"
    # Millilitres are stored natively but litres is the sensible display unit.
    dispensed = hass.states.get("sensor.cube1_water_dispensed")
    assert dispensed.state == "200.0"
    assert dispensed.attributes["unit_of_measurement"] == "L"
    assert dispensed.attributes["device_class"] == "water"


async def test_binary_sensor_polarity(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Problem-style sensors are off when the machine is healthy."""
    assert hass.states.get("binary_sensor.cube1_ready_to_brew").state == STATE_ON
    assert hass.states.get("binary_sensor.cube1_alarm").state == STATE_OFF
    # The tank is fine, so the "empty" problem sensor must read off.
    assert hass.states.get("binary_sensor.cube1_water_tank_empty").state == STATE_OFF


async def test_alarm_summary_lists_active_alarms(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The aggregate alarm carries the active slugs as an attribute."""
    state = hass.states.get("binary_sensor.cube1_alarm")
    assert state.attributes["active_alarms"] == []


# ── Commands ───────────────────────────────────────────────────────────────────


async def test_power_switch_writes_and_refreshes(
    hass: HomeAssistant, mock_client: AsyncMock, setup_integration: MockConfigEntry
) -> None:
    """Toggling power sends the verified IDs and re-reads the machine."""
    assert hass.states.get("switch.cube1_power").state == STATE_ON

    reads_before = mock_client.get_read_only.await_count

    await hass.services.async_call(
        "switch",
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: "switch.cube1_power"},
        blocking=True,
    )
    mock_client.set_value.assert_awaited_with(PARAM_POWER_STANDBY, 1)
    # State is re-read rather than assumed, because several keys report
    assert mock_client.get_read_only.await_count > reads_before

    await hass.services.async_call(
        "switch", SERVICE_TURN_ON, {ATTR_ENTITY_ID: "switch.cube1_power"}, blocking=True
    )
    mock_client.set_value.assert_awaited_with(PARAM_POWER_ON, 1)


async def test_number_writes_the_setpoint(
    hass: HomeAssistant, mock_client: AsyncMock, setup_integration: MockConfigEntry
) -> None:
    """The boiler setpoint number sends whole degrees."""
    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: "number.cube1_boiler_setpoint", "value": 124},
        blocking=True,
    )
    mock_client.set_value.assert_awaited_with(PARAM_BOILER_SETPOINT, 124)


async def test_number_respects_machine_bounds(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The setpoint is bounded to the Cube's documented range."""
    state = hass.states.get("number.cube1_boiler_setpoint")
    assert state.attributes["min"] == 115.0
    assert state.attributes["max"] == 126.0


async def test_button_press(
    hass: HomeAssistant, mock_client: AsyncMock, setup_integration: MockConfigEntry
) -> None:
    """The clock sync button reaches the machine."""
    await hass.services.async_call(
        "button",
        "press",
        {ATTR_ENTITY_ID: "button.cube1_sync_clock"},
        blocking=True,
    )
    assert mock_client.set_clock.await_count == 1


async def test_command_failure_raises_for_the_user(
    hass: HomeAssistant, mock_client: AsyncMock, setup_integration: MockConfigEntry
) -> None:
    """A failed write surfaces as an error instead of failing silently."""
    mock_client.set_value.side_effect = SanremoConnectionError("machine went away")

    with pytest.raises(HomeAssistantError, match="machine went away"):
        await hass.services.async_call(
            "switch",
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: "switch.cube1_power"},
            blocking=True,
        )


async def test_select_reports_and_writes_the_energy_saving_mode(
    hass: HomeAssistant, mock_client: AsyncMock, setup_integration: MockConfigEntry
) -> None:
    """The energy-saving mode select round-trips."""
    state = hass.states.get("select.cube1_energy_saving")
    # 152[17] bit 6 is clear on the captured machine.
    assert state.state == "standby"
    assert set(state.attributes["options"]) == {"eco", "standby"}

    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: "select.cube1_energy_saving", "option": "eco"},
        blocking=True,
    )
    mock_client.set_value.assert_awaited_with(PARAM_SELECT_ECO, None)

    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: "select.cube1_energy_saving", "option": "standby"},
        blocking=True,
    )
    mock_client.set_value.assert_awaited_with(PARAM_SELECT_STANDBY, None)


async def test_text_renames_the_machine(
    hass: HomeAssistant, mock_client: AsyncMock, setup_integration: MockConfigEntry
) -> None:
    """The machine name is editable, and writes go to key=251."""
    state = hass.states.get("text.cube1_machine_name")
    assert state.state == "cube1"

    await hass.services.async_call(
        "text",
        "set_value",
        {ATTR_ENTITY_ID: "text.cube1_machine_name", "value": "Kitchen"},
        blocking=True,
    )
    mock_client.set_name.assert_awaited_with("Kitchen")


# ── water_heater ───────────────────────────────────────────────────────────────


async def test_water_heater_reflects_the_boiler(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The boiler entity mirrors temperature, setpoint and mode."""
    state = hass.states.get("water_heater.cube1")

    assert state.state == STATE_PERFORMANCE
    assert state.attributes["current_temperature"] == 121.0
    assert state.attributes["temperature"] == 121.0
    assert state.attributes["brew_setpoint"] == 121.0
    assert state.attributes["eco_setpoint"] == 95.0
    assert STATE_ECO in state.attributes["operation_list"]


async def test_water_heater_set_temperature(
    hass: HomeAssistant, mock_client: AsyncMock, setup_integration: MockConfigEntry
) -> None:
    """Setting the target temperature writes the brew setpoint."""
    await hass.services.async_call(
        WATER_HEATER_DOMAIN,
        "set_temperature",
        {ATTR_ENTITY_ID: "water_heater.cube1", ATTR_TEMPERATURE: 126},
        blocking=True,
    )
    mock_client.set_value.assert_awaited_with(PARAM_BOILER_SETPOINT, 126)


async def test_water_heater_eco_mode_selects_then_enters(
    hass: HomeAssistant, mock_client: AsyncMock, setup_integration: MockConfigEntry
) -> None:
    """ECO and off are one machine state under two configurations."""
    await hass.services.async_call(
        WATER_HEATER_DOMAIN,
        SERVICE_SET_OPERATION_MODE,
        {ATTR_ENTITY_ID: "water_heater.cube1", ATTR_OPERATION_MODE: STATE_ECO},
        blocking=True,
    )

    calls = [call.args for call in mock_client.set_value.await_args_list]
    assert (PARAM_SELECT_ECO, None) in calls
    assert (PARAM_POWER_STANDBY, 1) in calls
    assert calls.index((PARAM_SELECT_ECO, None)) < calls.index((PARAM_POWER_STANDBY, 1))


async def test_water_heater_performance_mode_powers_on(
    hass: HomeAssistant, mock_client: AsyncMock, setup_integration: MockConfigEntry
) -> None:
    """Selecting performance just powers the machine on."""
    await hass.services.async_call(
        WATER_HEATER_DOMAIN,
        SERVICE_SET_OPERATION_MODE,
        {ATTR_ENTITY_ID: "water_heater.cube1", ATTR_OPERATION_MODE: STATE_PERFORMANCE},
        blocking=True,
    )
    mock_client.set_value.assert_awaited_with(PARAM_POWER_ON, 1)


# ── Calendar ───────────────────────────────────────────────────────────────────


async def test_calendar_projects_the_schedule(
    hass: HomeAssistant, setup_integration: MockConfigEntry, freezer
) -> None:
    """The scheduler is projected into calendar events."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-17 05:00:00+00:00")  # Monday, before the first slot
    state = hass.states.get("calendar.cube1_schedule_calendar")
    assert state is not None

    start = dt_util.now()
    response = await hass.services.async_call(
        CALENDAR_DOMAIN,
        "get_events",
        {
            ATTR_ENTITY_ID: "calendar.cube1_schedule_calendar",
            "start_date_time": start.isoformat(),
            "end_date_time": (start + timedelta(days=7)).isoformat(),
        },
        blocking=True,
        return_response=True,
    )

    events = response["calendar.cube1_schedule_calendar"]["events"]
    # Two windows Monday to Thursday, one Friday, one each weekend day: 11.
    assert len(events) == 11
    assert all(event["summary"] == "cube1" for event in events)
    assert events[0]["start"] == "2026-08-17T07:00:00+00:00"
    assert events[0]["end"] == "2026-08-17T09:30:00+00:00"
    # Friday opens an hour earlier than the rest of the week.
    friday = [e for e in events if e["start"].startswith("2026-08-21")]
    assert len(friday) == 1
    assert friday[0]["start"].endswith("06:00:00+00:00")


# ── Service action ─────────────────────────────────────────────────────────────


async def test_set_schedule_day_action(
    hass: HomeAssistant, mock_client: AsyncMock, setup_integration: MockConfigEntry
) -> None:
    """The schedule action writes the right row with the right day value."""
    registry = dr.async_get(hass)
    device = registry.async_get_device(identifiers={(DOMAIN, "aa:bb:cc:dd:ee:ff")})

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_SCHEDULE_DAY,
        {
            "device_id": device.id,
            "day": "wednesday",
            "slots": [{"on": "06:30", "off": "08:00"}],
            "copy_to": ["thursday"],
        },
        blocking=True,
    )

    row, slots, copy_to = mock_client.save_scheduler_day.await_args.args
    assert row == 2  # Wednesday's row index
    assert slots[0]["dow"] == 3  # Wednesday's day-of-week value
    assert slots[0]["on_hour"] == 6
    assert slots[0]["on_minute"] == 30
    assert slots[1]["dow"] == 7  # unspecified slots are cleared
    assert copy_to[3] is True  # Thursday


async def test_set_schedule_day_rejects_an_unknown_device(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """A bad device ID is reported rather than ignored."""
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_SCHEDULE_DAY,
            {
                "device_id": "does-not-exist",
                "day": "monday",
                "slots": [{"on": "07:00", "off": "09:00"}],
            },
            blocking=True,
        )
