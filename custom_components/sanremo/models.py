"""Model-independent state for Sanremo coffee machines."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time

from .const import EnergySavingMode, WaterSource, WifiStatus


@dataclass(frozen=True, slots=True)
class ScheduleSlot:
    """One programmable on/off window."""

    day: int
    index: int
    enabled: bool
    on_time: time
    off_time: time
    off_next_day: bool = False


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Identity of the WiNET module and the machine behind it."""

    name: str
    mac: str
    ip_address: str | None = None
    wifi_firmware: str | None = None
    board_firmware: str | None = None
    #: ``cube``, ``you`` or ``generic``.
    profile: str = "generic"
    model: str | None = None


@dataclass
class MachineState:
    """Normalised snapshot of a machine."""

    # ── Identity and connectivity ─────────────────────────────────────────────
    name: str | None = None
    wifi_status: WifiStatus | None = None
    ssid: str | None = None
    rssi: int | None = None
    ip_address: str | None = None
    wifi_firmware: str | None = None
    board_firmware: str | None = None
    firmware_update_available: bool | None = None
    cloud_connected: bool | None = None

    # ── Power ─────────────────────────────────────────────────────────────────
    #: False in standby or ECO.
    is_on: bool | None = None
    energy_saving_active: bool | None = None
    ready: bool | None = None

    # ── Temperatures and pressures, always metric on the wire ─────────────────
    boiler_temperature: float | None = None
    boiler_setpoint: float | None = None
    boiler_setpoint_min: float | None = None
    boiler_setpoint_max: float | None = None
    eco_setpoint: float | None = None
    #: Derived from the setpoint, not measured.
    estimated_brew_temperature: float | None = None
    group_temperature: float | None = None
    group_setpoint: float | None = None
    filter_holder_setpoint: float | None = None
    steam_temperature: float | None = None
    steam_pressure: float | None = None
    steam_pressure_setpoint: float | None = None
    pump_pressure: float | None = None

    # ── Brewing ───────────────────────────────────────────────────────────────
    last_shot_time: float | None = None
    realtime_flow: float | None = None
    shot_volume: int | None = None

    # ── Water ─────────────────────────────────────────────────────────────────
    tank_level_ok: bool | None = None
    boiler_level_ok: bool | None = None
    tank_level_low_warning: bool | None = None
    water_source: WaterSource | None = None

    # ── Counters ──────────────────────────────────────────────────────────────
    coffees_today: int | None = None
    coffees_week: int | None = None
    coffees_month: int | None = None
    coffees_year: int | None = None
    coffees_total: int | None = None
    water_dispensed_ml: int | None = None
    water_to_boiler_ml: int | None = None
    water_total_ml: int | None = None

    # ── Water filter ──────────────────────────────────────────────────────────
    filter_days_remaining: int | None = None
    filter_interval_days: int | None = None
    filter_interval_months: int | None = None
    filter_change_required: bool | None = None

    # ── Setup flags ───────────────────────────────────────────────────────────
    energy_saving_mode: EnergySavingMode | None = None
    energy_saving_delay: int | None = None
    energy_saving_countdown: int | None = None
    standby_after_last_coffee: bool | None = None
    steam_booster_enabled: bool | None = None
    steam_booster_heating: bool | None = None
    steam_booster_ready: bool | None = None
    pre_infusion_enabled: bool | None = None
    scheduler_enabled: bool | None = None
    display_temperature_fahrenheit: bool | None = None

    # ── Clock ─────────────────────────────────────────────────────────────────
    machine_time: datetime | None = None
    #: Reads 0 after a clock write, so not a validity flag.
    machine_weekday_known: bool | None = None
    #: The machine's own display, not Home Assistant's.
    clock_12_hour: bool | None = None

    # ── Alarms ────────────────────────────────────────────────────────────────
    #: Slug -> active; slugs are translation keys.
    alarms: dict[str, bool] = field(default_factory=dict)

    # ── Scheduler ─────────────────────────────────────────────────────────────
    schedule: list[ScheduleSlot] = field(default_factory=list)
    #: Per-day master enable, 0 = Monday .. 6 = Sunday.
    schedule_days_enabled: list[bool] = field(default_factory=list)

    # ── Diagnostics ───────────────────────────────────────────────────────────
    #: Keyed by RPC key, for unmapped values and bug reports.
    raw_registers: dict[str, list[int]] = field(default_factory=dict)

    @property
    def has_active_alarm(self) -> bool:
        """Return True if any alarm is active."""
        return any(self.alarms.values())

    @property
    def active_alarms(self) -> list[str]:
        """Return the slugs of every active alarm."""
        return sorted(slug for slug, active in self.alarms.items() if active)
