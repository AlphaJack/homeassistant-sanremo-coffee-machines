"""A read-only calendar view of the machine's power schedule."""

from __future__ import annotations

from dataclasses import dataclass
import datetime as dt

from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEntityDescription,
    CalendarEvent,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SanremoConfigEntry
from .const import Capability
from .coordinator import SanremoCoordinator
from .entity import SanremoEntity, SanremoEntityDescription
from .models import ScheduleSlot

PARALLEL_UPDATES = 0

#: How far ahead to project the weekly pattern when asked for events.
_MAX_LOOKAHEAD = dt.timedelta(days=90)


@dataclass(frozen=True, kw_only=True)
class SanremoCalendarDescription(SanremoEntityDescription, CalendarEntityDescription):
    """Describes a Sanremo calendar."""


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SanremoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the schedule calendar."""
    coordinator = entry.runtime_data
    if coordinator.supports(Capability.SCHEDULER) and coordinator.data.schedule:
        async_add_entities([SanremoScheduleCalendar(coordinator)])


class SanremoScheduleCalendar(SanremoEntity, CalendarEntity):
    """Projects the weekly schedule into calendar events."""

    def __init__(self, coordinator: SanremoCoordinator) -> None:
        super().__init__(
            coordinator,
            SanremoCalendarDescription(
                key="schedule",
                translation_key="schedule",
                capability=Capability.SCHEDULER,
            ),
        )

    @property
    def event(self) -> CalendarEvent | None:
        """Return the running or next upcoming window."""
        now = dt_util.now()
        events = self._events(now - dt.timedelta(days=1), now + dt.timedelta(days=8))
        for event in events:
            if event.end > now:
                return event
        return None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: dt.datetime,
        end_date: dt.datetime,
    ) -> list[CalendarEvent]:
        """Return every window in the requested range."""
        capped = min(end_date, start_date + _MAX_LOOKAHEAD)
        return self._events(start_date, capped)

    def _events(self, start: dt.datetime, end: dt.datetime) -> list[CalendarEvent]:
        """Expand the weekly pattern across the requested range."""
        state = self.machine
        if not state.schedule:
            return []

        days_enabled = state.schedule_days_enabled
        events: list[CalendarEvent] = []

        day = dt_util.as_local(start).date()
        last = dt_util.as_local(end).date()

        while day <= last:
            weekday = day.weekday()
            # Absent on machines that index slots rather than weekdays: no extra gating.
            if not days_enabled or (
                len(days_enabled) > weekday and days_enabled[weekday]
            ):
                for slot in state.schedule:
                    if slot.day != weekday or not slot.enabled:
                        continue
                    event = self._to_event(day, slot)
                    if event.end > start and event.start < end:
                        events.append(event)
            day += dt.timedelta(days=1)

        events.sort(key=lambda event: event.start)
        return events

    def _to_event(self, day: dt.date, slot: ScheduleSlot) -> CalendarEvent:
        """Turn one slot on one date into a calendar event."""
        start = dt_util.as_local(dt.datetime.combine(day, slot.on_time))
        end = dt_util.as_local(dt.datetime.combine(day, slot.off_time))
        if slot.off_next_day:
            end += dt.timedelta(days=1)

        return CalendarEvent(
            start=start,
            end=end,
            summary=self.coordinator.device.name,
            description=f"Scheduled on time slot {slot.index + 1}",
            uid=f"{self.unique_id}-{day.isoformat()}-{slot.index}",
        )
