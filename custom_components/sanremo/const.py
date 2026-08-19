"""Constants for the Sanremo coffee machine integration."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

DOMAIN: Final = "sanremo"
MANUFACTURER: Final = "Sanremo"
GENERIC_PROFILE_ID: Final = "generic"
DEFAULT_SCAN_INTERVAL: Final = 10
CONF_SCAN_INTERVAL: Final = "scan_interval"
SERVICE_SET_SCHEDULE_DAY: Final = "set_schedule_day"

AJAX_PATH: Final = "/ajax/post"
#: Redirects to a model page such as ``cube.html``.
INDEX_PATH: Final = "/index.html"


class Key(StrEnum):
    """WiNET RPC command keys."""

    DEVICE_INFO = "105"
    SYSTEM_PARAMS = "150"
    READ_ONLY = "151"
    READ_WRITE = "152"
    SET_VALUE = "200"
    REBOOT = "202"
    CHECK_FIRMWARE = "203"
    SET_CLOCK = "249"
    SET_SCHEDULER_DAY_ENABLED = "250"
    SET_NAME = "251"
    SET_SCHEDULER_ENABLED = "252"
    SAVE_SCHEDULER_DAY = "253"


class WifiStatus(StrEnum):
    """Wi-Fi station state."""

    OPERATING = "operating"
    CONNECTING = "connecting"
    WRONG_PASSWORD = "wrong_password"
    AP_NOT_FOUND = "ap_not_found"
    CONNECTION_ERROR = "connection_error"
    CONNECTED = "connected"
    NOT_STATION_MODE = "not_station_mode"
    UNKNOWN = "unknown"


WIFI_STATUS_MAP: Final[dict[int, WifiStatus]] = {
    0: WifiStatus.OPERATING,
    1: WifiStatus.CONNECTING,
    2: WifiStatus.WRONG_PASSWORD,
    3: WifiStatus.AP_NOT_FOUND,
    4: WifiStatus.CONNECTION_ERROR,
    5: WifiStatus.CONNECTED,
    255: WifiStatus.NOT_STATION_MODE,
}


class EnergySavingMode(StrEnum):
    """Low-power state the idle timer engages."""

    ECO = "eco"
    STANDBY = "standby"


class WaterSource(StrEnum):
    """Where the machine takes its water from."""

    TANK = "tank"
    MAINS = "mains"


class Capability(StrEnum):
    """Feature a profile may advertise; platforms skip entities without it."""

    POWER = "power"
    BOILER_SETPOINT = "boiler_setpoint"
    ECO_SETPOINT = "eco_setpoint"
    ENERGY_SAVING_MODE = "energy_saving_mode"
    ENERGY_SAVING_DELAY = "energy_saving_delay"
    STANDBY_AFTER_LAST_COFFEE = "standby_after_last_coffee"
    STEAM_BOOSTER = "steam_booster"
    FILTER_MONITORING = "filter_monitoring"
    SCHEDULER = "scheduler"
    CLOCK = "clock"
    RENAME = "rename"
    REBOOT = "reboot"
    FIRMWARE_CHECK = "firmware_check"
    DISPLAY_TEMPERATURE_UNIT = "display_temperature_unit"
    CLOCK_FORMAT = "clock_format"


# ── Cube write bounds, from the min/max attributes in cube.html (whole steps) ──
CUBE_BOILER_MIN_C: Final = 115.0
CUBE_BOILER_MAX_C: Final = 126.0
CUBE_ECO_MIN_C: Final = 80.0
CUBE_ECO_MAX_C: Final = 100.0
CUBE_ENERGY_SAVING_DELAY_MIN_MIN: Final = 1
CUBE_ENERGY_SAVING_DELAY_MAX_MIN: Final = 90
CUBE_FILTER_MONTHS_MIN: Final = 0
CUBE_FILTER_MONTHS_MAX: Final = 12

#: ``key=151`` config value marking the CUBE R, whose setpoint is adjustable.
CUBE_CONFIG_ADJUSTABLE_BOILER: Final = 2
CUBE_ECO_MIN_BOARD_VERSION: Final = 1.14
