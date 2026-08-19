"""The Sanremo coffee machines integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import SanremoClient, SanremoError
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN
from .coordinator import SanremoCoordinator
from .profiles import async_detect_profile
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CALENDAR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
    Platform.WATER_HEATER,
]

type SanremoConfigEntry = ConfigEntry[SanremoCoordinator]

# Machines are only ever added through the UI, never from YAML.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's service actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SanremoConfigEntry) -> bool:
    """Set up a machine from a config entry."""
    session = async_get_clientsession(hass)
    client = SanremoClient(entry.data[CONF_HOST], session)

    try:
        profile = await async_detect_profile(client)
        device = await profile.async_setup()
    except SanremoError as err:
        raise ConfigEntryNotReady(
            f"Cannot reach the Sanremo machine at {entry.data[CONF_HOST]}: {err}"
        ) from err

    _LOGGER.debug(
        "Set up %s (%s) at %s using the %s profile with capabilities %s",
        device.name,
        device.model,
        entry.data[CONF_HOST],
        device.profile,
        sorted(profile.capabilities),
    )

    coordinator = SanremoCoordinator(
        hass,
        entry,
        client,
        profile,
        device,
        scan_interval=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start push only after platforms subscribe, so the first frame is not dropped.
    await coordinator.async_start_push()
    entry.async_on_unload(coordinator.async_stop_push)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: SanremoConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
