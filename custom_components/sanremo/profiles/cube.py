"""Profile for the Sanremo Cube family (the WiNET register protocol)."""

from __future__ import annotations

from datetime import datetime, time
import logging
from typing import Any, Final

from homeassistant.util import dt as dt_util

from ..const import (
    CUBE_BOILER_MAX_C,
    CUBE_BOILER_MIN_C,
    CUBE_CONFIG_ADJUSTABLE_BOILER,
    CUBE_ECO_MAX_C,
    CUBE_ECO_MIN_BOARD_VERSION,
    CUBE_ECO_MIN_C,
    WIFI_STATUS_MAP,
    Capability,
    EnergySavingMode,
    WaterSource,
    WifiStatus,
)
from ..models import DeviceInfo, MachineState, ScheduleSlot
from .base import MachineProfile

_LOGGER = logging.getLogger(__name__)

# ── key=200 parameter IDs ──────────────────────────────────────────────────────
PARAM_BOILER_SETPOINT: Final = 1
PARAM_SELECT_ECO: Final = 8
PARAM_SELECT_STANDBY: Final = 9
PARAM_STANDBY_AFTER_LAST_COFFEE: Final = 10
PARAM_POWER_ON: Final = 11
PARAM_POWER_STANDBY: Final = 12
PARAM_CLOCK_24_HOUR: Final = 20
PARAM_DISPLAY_TEMP_UNIT: Final = 21
PARAM_FILTER_MONTHS: Final = 22
PARAM_FILTER_RESET: Final = 23
PARAM_STEAM_BOOSTER: Final = 24
PARAM_ENERGY_SAVING_DELAY: Final = 25
PARAM_ECO_SETPOINT: Final = 26

# ── key=151 read-only registers ────────────────────────────────────────────────
RO_BOILER_TEMP: Final = 0
RO_RTC_MINUTE: Final = 2
RO_RTC_HOUR: Final = 3
RO_RTC_DAY: Final = 4
RO_RTC_MONTH: Final = 5
RO_RTC_YEAR: Final = 6
RO_LAST_SHOT_TIME: Final = 9
RO_FILTER_DAYS_REMAINING: Final = 10
RO_STATUS: Final = 12
RO_ALARMS: Final = 14
RO_COFFEES_TODAY: Final = 21
RO_COFFEES_WEEK: Final = 22
RO_COFFEES_MONTH_LO: Final = 23
RO_COFFEES_MONTH_HI: Final = 24
RO_COFFEES_YEAR_LO: Final = 25
RO_COFFEES_YEAR_HI: Final = 26
RO_COFFEES_TOTAL_LO: Final = 29
RO_COFFEES_TOTAL_HI: Final = 30
RO_WATER_DISPENSED_LO: Final = 31
RO_WATER_DISPENSED_HI: Final = 32
RO_WATER_BOILER_LO: Final = 33
RO_WATER_BOILER_HI: Final = 34
RO_WATER_TOTAL_LO: Final = 35
RO_WATER_TOTAL_HI: Final = 36
RO_ENERGY_SAVING_COUNTDOWN: Final = 38
RO_MIN_LENGTH: Final = 37

# ── key=152 read/write registers ───────────────────────────────────────────────
RW_BOILER_SETPOINT: Final = 0
RW_FILTER_MONTHS: Final = 8
RW_SETUP_FLAGS: Final = 17
RW_SCHEDULE_START: Final = 18
RW_SCHEDULE_DAY_MASK: Final = 60
RW_ENERGY_SAVING_DELAY: Final = 67
RW_ECO_SETPOINT: Final = 68
RW_MIN_LENGTH: Final = 61
RW_V2_LENGTH: Final = 69

# ── 151[12] machine status bits ────────────────────────────────────────────────
STATUS_TANK_LEVEL_OK: Final = 1 << 0
STATUS_BOILER_LEVEL_OK: Final = 1 << 1
STATUS_TANK_PRE_ALARM: Final = 1 << 2
STATUS_WATER_SOURCE: Final = 1 << 3
STATUS_ENERGY_SAVING: Final = 1 << 4
STATUS_READY: Final = 1 << 5
STATUS_STEAM_BOOSTER_HEATING: Final = 1 << 8
STATUS_STEAM_BOOSTER_READY: Final = 1 << 9

# ── 151[14] alarm bits, slug -> bit ────────────────────────────────────────────
ALARM_BITS: Final[dict[str, int]] = {
    "e03_boiler_probe_open": 1 << 0,
    "e02_boiler_heating_timeout": 1 << 1,
    "e01_boiler_filling_timeout": 1 << 2,
    "e04_boiler_probe_short_circuit": 1 << 3,
    "e06_eeprom_corrupted": 1 << 4,
    "e05_potentiometer_disconnected": 1 << 5,
    "e07_coffee_dispensing_timeout": 1 << 6,
    "e08_filter_replacement_required": 1 << 7,
    "water_level_low": 1 << 8,
}

# ── 152[17] setup bits ─────────────────────────────────────────────────────────
SETUP_STANDBY_AFTER_LAST_COFFEE: Final = 1 << 0
SETUP_FILTER_WARNING_LITRES: Final = 1 << 1
SETUP_DISPLAY_FAHRENHEIT: Final = 1 << 2
SETUP_SCHEDULER_ENABLED: Final = 1 << 3
SETUP_WIFI_CIRCUIT: Final = 1 << 4
SETUP_PRE_INFUSION: Final = 1 << 5
SETUP_ENERGY_SAVING_IS_ECO: Final = 1 << 6
SETUP_STEAM_BOOSTER: Final = 1 << 7

SCHEDULE_SLOTS_PER_DAY: Final = 3
SCHEDULE_DAYS: Final = 7
#: Day-of-week value written into a slot to mark it unused.
SLOT_DISABLED: Final = 7

#: Refresh the slow-moving system parameters (``key=150``) every N polls.
SYSTEM_PARAMS_EVERY: Final = 6

#: Consecutive key=150 failures before saying so at warning level.
SYSTEM_PARAMS_FAIL_WARN_AT: Final = 3


def _registers(payload: dict[str, Any]) -> list[int]:
    """Flatten the ``[[index, value], ...]`` register array into a list."""
    pairs = payload.get("registers") or []
    if not pairs:
        return []
    try:
        highest = max(int(index) for index, _ in pairs)
    except (TypeError, ValueError):
        return []

    out = [0] * (highest + 1)
    for index, value in pairs:
        out[int(index)] = int(value)
    return out


def _u32(registers: list[int], lo: int, hi: int) -> int | None:
    """Combine two 16-bit registers into one 32-bit counter."""
    if len(registers) <= max(lo, hi):
        return None
    return (registers[hi] << 16) | registers[lo]


def _quarters_to_time(quarters: int) -> tuple[time, bool]:
    """Decode a quarter-hour count into a time and a "next day" flag."""
    if quarters >= 96:
        return time(0, 0), True
    return time(hour=quarters // 4, minute=(quarters % 4) * 15), False


def _dow_for_row(row: int) -> int:
    """Map a Monday-first row index to the machine's Sunday-first day value."""
    return (row + 1) % 7


class CubeProfile(MachineProfile):
    """Cube / Cube R, and any machine exposing the same register layout."""

    profile_id = "cube"
    model_name = "Cube"

    def __init__(self, client) -> None:
        super().__init__(client)
        self._board_version: float | None = None
        self._config: int | None = None
        self._poll_count = 0
        self._system_params: dict[str, Any] = {}
        self._system_params_failures = 0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def async_setup(self) -> DeviceInfo:
        """Probe the machine and work out what it can do."""
        info = await self.client.get_device_info()
        system = await self.client.get_system_params()
        read_only = await self.client.get_read_only()
        read_write = await self.client.get_read_write()

        self._system_params = system
        self._board_version = _as_float(system.get("ver"))
        self._config = _as_int(read_only.get("config"))

        rw_registers = _registers(read_write)
        ro_registers = _registers(read_only)

        caps = {
            Capability.POWER,
            Capability.CLOCK,
            Capability.RENAME,
            Capability.REBOOT,
            Capability.FIRMWARE_CHECK,
            Capability.SCHEDULER,
            Capability.DISPLAY_TEMPERATURE_UNIT,
            Capability.CLOCK_FORMAT,
            # Works on board 1.26 even though the vendor app hides it there.
            Capability.STANDBY_AFTER_LAST_COFFEE,
        }

        # Only the CUBE R exposes an adjustable boiler setpoint.
        if self._config == CUBE_CONFIG_ADJUSTABLE_BOILER:
            caps.add(Capability.BOILER_SETPOINT)

        # Board > 1.14 unlocks the v2 energy-saving set; guard on length too.
        if (
            self._board_version is not None
            and self._board_version > CUBE_ECO_MIN_BOARD_VERSION
            and len(rw_registers) >= RW_V2_LENGTH
        ):
            caps |= {
                Capability.ECO_SETPOINT,
                Capability.ENERGY_SAVING_DELAY,
                Capability.ENERGY_SAVING_MODE,
                Capability.STEAM_BOOSTER,
            }

        if len(rw_registers) > RW_FILTER_MONTHS:
            caps.add(Capability.FILTER_MONITORING)

        self._capabilities = caps

        if len(ro_registers) <= RO_MIN_LENGTH:
            _LOGGER.warning(
                "Cube at %s returned only %s read-only registers; some entities "
                "will be unavailable",
                self.client.host,
                len(ro_registers),
            )

        model = self.model_name
        if self._config == CUBE_CONFIG_ADJUSTABLE_BOILER:
            model = "Cube R"

        return DeviceInfo(
            name=str(info.get("name") or "Sanremo Cube"),
            mac=str(info.get("mac") or ""),
            ip_address=info.get("currentIp"),
            wifi_firmware=info.get("fwVer"),
            board_firmware=_format_board_version(self._board_version),
            profile=self.profile_id,
            model=model,
        )

    async def async_poll(self) -> MachineState:
        """Read the machine and normalise it."""
        read_only = await self.client.get_read_only()
        read_write = await self.client.get_read_write()

        # System parameters barely move, so spend a request on them only occasionally.
        if self._poll_count % SYSTEM_PARAMS_EVERY == 0:
            try:
                self._system_params = await self.client.get_system_params()
            except Exception as err:  # noqa: BLE001 - best effort, keep polling
                self._system_params_failures += 1
                # A run of misses silently freezes identity, Wi-Fi and OTA; say so once.
                if self._system_params_failures == SYSTEM_PARAMS_FAIL_WARN_AT:
                    _LOGGER.warning(
                        "key=150 has failed %s times running for the machine at "
                        "%s; network and firmware details may be stale: %s",
                        self._system_params_failures,
                        self.client.host,
                        err,
                    )
                else:
                    _LOGGER.debug("Could not refresh system params: %s", err)
            else:
                if self._system_params_failures >= SYSTEM_PARAMS_FAIL_WARN_AT:
                    _LOGGER.info(
                        "key=150 is answering again for the machine at %s",
                        self.client.host,
                    )
                self._system_params_failures = 0
        self._poll_count += 1

        state = MachineState()
        self._apply_system_params(state, self._system_params)
        self._apply_read_only(state, read_only)
        self._apply_read_write(state, read_write)
        return state

    # ── Decoding ──────────────────────────────────────────────────────────────

    def _apply_system_params(
        self, state: MachineState, payload: dict[str, Any]
    ) -> None:
        """Apply ``key=150``: identity, units and the OTA flag."""
        if not payload:
            return
        state.name = payload.get("name")
        state.ssid = payload.get("ssid")
        state.rssi = _as_int(payload.get("rssi"))
        state.firmware_update_available = _as_bool(payload.get("ota"))
        state.cloud_connected = _as_bool(payload.get("cloudConnection"))
        state.wifi_status = WIFI_STATUS_MAP.get(
            _as_int(payload.get("status")) or -1, WifiStatus.UNKNOWN
        )
        board = _as_float(payload.get("ver"))
        if board is not None:
            self._board_version = board
            state.board_firmware = _format_board_version(board)

    def _apply_read_only(self, state: MachineState, payload: dict[str, Any]) -> None:
        """Apply ``key=151``: live machine state, counters and alarms."""
        registers = _registers(payload)
        state.raw_registers["151"] = registers

        if payload.get("ssid"):
            state.ssid = payload["ssid"]
        if (rssi := _as_int(payload.get("rssi"))) is not None:
            state.rssi = rssi
        if (ota := payload.get("ota")) is not None:
            state.firmware_update_available = _as_bool(ota)

        # Filter interval, in days, under the vendor's own spelling.
        interval_days = _as_int(payload.get("ThesholdWarningChangeFilter"))
        if interval_days is not None:
            state.filter_interval_days = interval_days

        state.machine_time, state.machine_weekday_known = _parse_machine_time(
            payload.get("time")
        )
        if (use_24h := payload.get("use24H")) is not None:
            state.clock_12_hour = not bool(int(use_24h))

        if not registers:
            return

        def reg(index: int) -> int | None:
            return registers[index] if len(registers) > index else None

        if (value := reg(RO_BOILER_TEMP)) is not None:
            state.boiler_temperature = float(value)
        if (value := reg(RO_LAST_SHOT_TIME)) is not None:
            state.last_shot_time = value / 10
        if (value := reg(RO_FILTER_DAYS_REMAINING)) is not None:
            state.filter_days_remaining = value
        if (value := reg(RO_ENERGY_SAVING_COUNTDOWN)) is not None:
            state.energy_saving_countdown = value

        # ── Status bitfield ──
        if (status := reg(RO_STATUS)) is not None:
            energy_saving = bool(status & STATUS_ENERGY_SAVING)
            state.energy_saving_active = energy_saving
            state.is_on = not energy_saving
            state.ready = bool(status & STATUS_READY)
            state.tank_level_ok = bool(status & STATUS_TANK_LEVEL_OK)
            state.boiler_level_ok = bool(status & STATUS_BOILER_LEVEL_OK)
            state.water_source = (
                WaterSource.MAINS if status & STATUS_WATER_SOURCE else WaterSource.TANK
            )
            state.steam_booster_heating = bool(status & STATUS_STEAM_BOOSTER_HEATING)
            state.steam_booster_ready = bool(status & STATUS_STEAM_BOOSTER_READY)

        # ── Alarm bitfield ──
        if (alarms := reg(RO_ALARMS)) is not None:
            state.alarms = {
                slug: bool(alarms & mask) for slug, mask in ALARM_BITS.items()
            }
            state.filter_change_required = state.alarms[
                "e08_filter_replacement_required"
            ]

            # The pre-alarm only means anything while the hard low-water alarm is clear.
            if (status := reg(RO_STATUS)) is not None:
                state.tank_level_low_warning = (
                    False
                    if state.alarms["water_level_low"]
                    else bool(status & STATUS_TANK_PRE_ALARM)
                )

        # ── Counters ──
        if (value := reg(RO_COFFEES_TODAY)) is not None:
            state.coffees_today = value
        if (value := reg(RO_COFFEES_WEEK)) is not None:
            state.coffees_week = value
        state.coffees_month = _u32(registers, RO_COFFEES_MONTH_LO, RO_COFFEES_MONTH_HI)
        state.coffees_year = _u32(registers, RO_COFFEES_YEAR_LO, RO_COFFEES_YEAR_HI)
        state.coffees_total = _u32(registers, RO_COFFEES_TOTAL_LO, RO_COFFEES_TOTAL_HI)
        state.water_dispensed_ml = _u32(
            registers, RO_WATER_DISPENSED_LO, RO_WATER_DISPENSED_HI
        )
        state.water_to_boiler_ml = _u32(
            registers, RO_WATER_BOILER_LO, RO_WATER_BOILER_HI
        )
        state.water_total_ml = _u32(registers, RO_WATER_TOTAL_LO, RO_WATER_TOTAL_HI)

    def _apply_read_write(self, state: MachineState, payload: dict[str, Any]) -> None:
        """Apply ``key=152``: setpoints, setup flags and the scheduler."""
        registers = _registers(payload)
        state.raw_registers["152"] = registers
        if not registers:
            return

        if len(registers) > RW_BOILER_SETPOINT:
            state.boiler_setpoint = registers[RW_BOILER_SETPOINT] / 10
            state.estimated_brew_temperature = _estimated_brew_temperature(
                state.boiler_setpoint
            )
            state.boiler_setpoint_min = CUBE_BOILER_MIN_C
            state.boiler_setpoint_max = CUBE_BOILER_MAX_C

        if len(registers) > RW_FILTER_MONTHS:
            state.filter_interval_months = registers[RW_FILTER_MONTHS]

        if len(registers) > RW_SETUP_FLAGS:
            flags = registers[RW_SETUP_FLAGS]
            state.standby_after_last_coffee = bool(
                flags & SETUP_STANDBY_AFTER_LAST_COFFEE
            )
            state.display_temperature_fahrenheit = bool(
                flags & SETUP_DISPLAY_FAHRENHEIT
            )
            state.scheduler_enabled = bool(flags & SETUP_SCHEDULER_ENABLED)
            state.pre_infusion_enabled = bool(flags & SETUP_PRE_INFUSION)
            state.steam_booster_enabled = bool(flags & SETUP_STEAM_BOOSTER)
            state.energy_saving_mode = (
                EnergySavingMode.ECO
                if flags & SETUP_ENERGY_SAVING_IS_ECO
                else EnergySavingMode.STANDBY
            )

        # v2-only registers; absent on board firmware <= 1.14.
        if len(registers) >= RW_V2_LENGTH:
            state.energy_saving_delay = registers[RW_ENERGY_SAVING_DELAY]
            state.eco_setpoint = registers[RW_ECO_SETPOINT] / 10

        if len(registers) > RW_SCHEDULE_DAY_MASK:
            mask = registers[RW_SCHEDULE_DAY_MASK]
            # Wire order is Sunday-first; present it Monday-first.
            state.schedule_days_enabled = [
                bool(mask & (1 << _dow_for_row(row))) for row in range(SCHEDULE_DAYS)
            ]

        state.schedule = _parse_schedule(registers)

    # ── Commands ──────────────────────────────────────────────────────────────

    async def async_set_power(self, on: bool) -> None:
        """Switch the machine on, or drop it into standby."""
        await self.client.set_value(PARAM_POWER_ON if on else PARAM_POWER_STANDBY, 1)

    async def async_set_boiler_setpoint(self, celsius: float) -> None:
        """Set the boiler setpoint."""
        clamped = min(max(celsius, CUBE_BOILER_MIN_C), CUBE_BOILER_MAX_C)
        await self.client.set_value(PARAM_BOILER_SETPOINT, round(clamped))

    async def async_set_eco_setpoint(self, celsius: float) -> None:
        """Set the ECO setpoint, which is written in tenths of a degree."""
        clamped = min(max(celsius, CUBE_ECO_MIN_C), CUBE_ECO_MAX_C)
        # The vendor only sends whole degrees; do not gamble on halves.
        await self.client.set_value(PARAM_ECO_SETPOINT, round(clamped) * 10)

    async def async_set_energy_saving_delay(self, seconds: int) -> None:
        """Set the idle delay before the low-power state engages."""
        await self.client.set_value(PARAM_ENERGY_SAVING_DELAY, max(0, int(seconds)))

    async def async_set_energy_saving_mode(self, eco: bool) -> None:
        """Pick ECO or STANDBY. Both IDs take no argument."""
        await self.client.set_value(
            PARAM_SELECT_ECO if eco else PARAM_SELECT_STANDBY, None
        )

    async def async_set_standby_after_last_coffee(self, enabled: bool) -> None:
        """Toggle the fixed 30-minute standby timer."""
        await self.client.set_value(
            PARAM_STANDBY_AFTER_LAST_COFFEE, 1 if enabled else 0
        )

    async def async_set_steam_booster(self, enabled: bool) -> None:
        """Toggle the SteamBooster."""
        await self.client.set_value(PARAM_STEAM_BOOSTER, 1 if enabled else 0)

    async def async_set_filter_interval_months(self, months: int) -> None:
        """Set the filter interval. Zero turns filter monitoring off."""
        await self.client.set_value(PARAM_FILTER_MONTHS, max(0, int(months)))

    async def async_reset_filter(self) -> None:
        """Reset the filter expiry date. There is no inverse command."""
        await self.client.set_value(PARAM_FILTER_RESET, 0)

    async def async_set_clock_12_hour(self, enabled: bool) -> None:
        """Switch the machine's own clock display between 12- and 24-hour."""
        await self.client.set_value(PARAM_CLOCK_24_HOUR, 0 if enabled else 1)

    async def async_set_display_temperature_fahrenheit(self, enabled: bool) -> None:
        """Switch the machine's own display unit."""
        await self.client.set_value(PARAM_DISPLAY_TEMP_UNIT, 1 if enabled else 0)

    async def async_set_scheduler_enabled(self, enabled: bool) -> None:
        """Flip the scheduler master switch."""
        await self.client.set_scheduler_enabled(enabled)

    async def async_set_scheduler_day_enabled(self, day: int, enabled: bool) -> None:
        """Enable one scheduler day, given Monday-first."""
        await self.client.set_scheduler_day_enabled(_dow_for_row(day), enabled)

    async def async_set_schedule_day(
        self,
        day: int,
        slots: list[tuple[bool, time, time]],
        copy_to: list[int] | None = None,
    ) -> None:
        """Replace one day's three slots."""
        if not 0 <= day < SCHEDULE_DAYS:
            raise ValueError(f"day must be 0..6, got {day}")

        dow = _dow_for_row(day)
        payload: list[dict[str, int]] = []
        for index in range(SCHEDULE_SLOTS_PER_DAY):
            if index < len(slots):
                enabled, on_time, off_time = slots[index]
            else:
                enabled, on_time, off_time = False, time(0, 0), time(0, 0)
            payload.append(
                {
                    "dow": dow if enabled else SLOT_DISABLED,
                    "on_hour": on_time.hour,
                    "on_minute": _round_quarter(on_time.minute),
                    "off_hour": off_time.hour,
                    "off_minute": _round_quarter(off_time.minute),
                }
            )

        flags = [False] * SCHEDULE_DAYS
        for target in copy_to or []:
            if 0 <= target < SCHEDULE_DAYS:
                flags[target] = True

        await self.client.save_scheduler_day(day, payload, flags)

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


# ── Helpers ────────────────────────────────────────────────────────────────────


#: Boiler setpoint (whole °C) -> temperature at the group; see PROTOCOL.md 4.7.
_BREW_TEMPERATURE_TABLE: Final[dict[int, float]] = {
    115: 89.5,
    116: 89.8,
    117: 90.2,
    118: 90.5,
    119: 90.7,
    120: 91.5,
    121: 92.4,
    122: 92.7,
    123: 94.0,
    124: 95.0,
    125: 95.7,
    126: 96.3,
}


def _estimated_brew_temperature(setpoint: float | None) -> float | None:
    """Estimate the temperature at the group for a given boiler setpoint."""
    if not setpoint:
        return None
    key = round(setpoint)
    if (value := _BREW_TEMPERATURE_TABLE.get(key)) is not None:
        return value
    floor = min(_BREW_TEMPERATURE_TABLE)
    ceiling = max(_BREW_TEMPERATURE_TABLE)
    if key < floor:
        return _BREW_TEMPERATURE_TABLE[floor]
    return _BREW_TEMPERATURE_TABLE[ceiling]


def _parse_schedule(registers: list[int]) -> list[ScheduleSlot]:
    """Decode the 21 scheduler rows from ``152[18:60]``."""
    slots: list[ScheduleSlot] = []
    cursor = RW_SCHEDULE_START

    for row in range(SCHEDULE_DAYS * SCHEDULE_SLOTS_PER_DAY):
        if len(registers) <= cursor + 1:
            break
        day_value = registers[cursor] & 7
        times = registers[cursor + 1]
        cursor += 2

        on_time, _ = _quarters_to_time((times >> 8) & 0xFF)
        off_time, off_at_midnight = _quarters_to_time(times & 0xFF)

        slots.append(
            ScheduleSlot(
                day=row // SCHEDULE_SLOTS_PER_DAY,
                index=row % SCHEDULE_SLOTS_PER_DAY,
                enabled=day_value != SLOT_DISABLED,
                on_time=on_time,
                off_time=off_time,
                # 24:00, or off before on; the latter's behaviour is unverified.
                off_next_day=off_at_midnight or off_time <= on_time,
            )
        )

    return slots


def _parse_machine_time(raw: Any) -> tuple[datetime | None, bool | None]:
    """Decode the ``[dow, year, month, day, hour, minute]`` clock array."""
    if not isinstance(raw, (list, tuple)) or len(raw) < 6:
        return None, None
    try:
        dow, year, month, day, hour, minute = (int(value) for value in raw[:6])
        parsed = datetime(
            year, month, day, hour, minute, tzinfo=dt_util.DEFAULT_TIME_ZONE
        )
    except (TypeError, ValueError):
        return None, None
    return parsed, dow != 0


def _round_quarter(minute: int) -> int:
    """Snap a minute value onto the machine's 15-minute grid."""
    return min(45, round(minute / 15) * 15)


def _format_board_version(version: float | None) -> str | None:
    """Render the board firmware the way the vendor app does."""
    return None if version is None else f"{version:.2f}"


def _as_int(value: Any) -> int | None:
    """Coerce to int, or None when that is not possible."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    """Coerce to float, or None when that is not possible."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    """Coerce to bool, preserving None."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)
