"""Service actions for Sanremo coffee machines."""

from __future__ import annotations

from datetime import time
import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
import voluptuous as vol

from .const import DOMAIN, SERVICE_SET_SCHEDULE_DAY, Capability

if TYPE_CHECKING:
    from .coordinator import SanremoCoordinator

_LOGGER = logging.getLogger(__name__)

ATTR_DEVICE_ID = "device_id"
ATTR_DAY = "day"
ATTR_SLOTS = "slots"
ATTR_COPY_TO = "copy_to"

WEEKDAYS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]

SLOT_SCHEMA = vol.Schema(
    {
        vol.Required("on"): cv.time,
        vol.Required("off"): cv.time,
        vol.Optional("enabled", default=True): cv.boolean,
    }
)

SET_SCHEDULE_DAY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_DAY): vol.In(WEEKDAYS),
        # Exactly three slots per day; fewer is fine, more is a user error.
        vol.Required(ATTR_SLOTS): vol.All(
            cv.ensure_list, vol.Length(min=1, max=3), [SLOT_SCHEMA]
        ),
        vol.Optional(ATTR_COPY_TO, default=[]): vol.All(
            cv.ensure_list, [vol.In(WEEKDAYS)]
        ),
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's service actions."""

    async def _async_set_schedule_day(call: ServiceCall) -> None:
        """Replace one weekday's schedule slots."""
        coordinator = _coordinator_for_device(hass, call.data[ATTR_DEVICE_ID])

        if not coordinator.supports(Capability.SCHEDULER):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="scheduler_unsupported",
            )

        day = WEEKDAYS.index(call.data[ATTR_DAY])
        slots: list[tuple[bool, time, time]] = [
            (slot["enabled"], slot["on"], slot["off"]) for slot in call.data[ATTR_SLOTS]
        ]
        copy_to = [WEEKDAYS.index(name) for name in call.data[ATTR_COPY_TO]]

        await coordinator.async_execute(
            coordinator.profile.async_set_schedule_day(day, slots, copy_to)
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SCHEDULE_DAY,
        _async_set_schedule_day,
        schema=SET_SCHEDULE_DAY_SCHEMA,
    )


def _coordinator_for_device(hass: HomeAssistant, device_id: str) -> SanremoCoordinator:
    """Resolve a device ID to its coordinator."""
    registry = dr.async_get(hass)
    device = registry.async_get(device_id)
    if device is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="device_not_found",
            translation_placeholders={"device_id": device_id},
        )

    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if (
            entry is not None
            and entry.domain == DOMAIN
            and entry.state is ConfigEntryState.LOADED
        ):
            return entry.runtime_data

    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="device_not_loaded",
        translation_placeholders={"device_id": device_id},
    )
