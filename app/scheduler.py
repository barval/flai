"""The scheduler: when an item fires next, and what happens when it does.

A reminder is a sentence prepared when it was created — delivering it costs no model
time. An action is work: it runs on the Tasks engine, uses the same tools and skills a
chat turn does, and may therefore run late. Keeping the two apart is why a reminder is
reliable and an action is honest about being late.

This module holds the recurrence function and, later, the tick. The database lives in
``app.scheduler_store``.
"""

from __future__ import annotations

import calendar
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Anything faster is a busy loop wearing a schedule's clothes.
MIN_MINUTES = 15

REPEATS = ("once", "minutes", "daily", "weekly", "monthly")


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:  # noqa: BLE001 - an unknown zone must not stop the scheduler
        return ZoneInfo("UTC")


def _at(item: dict, day: datetime) -> datetime:
    """The item's local time on ``day``'s date, in the item's zone."""
    zone = _zone(item.get("timezone"))
    local = day.astimezone(zone)
    clock: time = item.get("time_of_day") or time(9, 0)
    return local.replace(
        hour=clock.hour, minute=clock.minute, second=0, microsecond=0, tzinfo=None
    ).replace(tzinfo=zone)


def next_after(item: dict, moment: datetime) -> datetime | None:
    """The first firing strictly after ``moment``, or None for a one-off.

    ``daily``/``weekly``/``monthly`` are computed on the wall clock of the item's zone:
    "nine in the morning" must stay nine across a DST change, which adding 24 hours does
    not do. A month without the chosen day fires on its last day rather than skipping.
    """
    repeat = str(item.get("repeat") or "once")
    if repeat == "once":
        return None

    zone = _zone(item.get("timezone"))
    local = moment.astimezone(zone)

    if repeat == "minutes":
        step = max(MIN_MINUTES, int(item.get("every_minutes") or MIN_MINUTES))
        return moment + timedelta(minutes=step)

    if repeat == "daily":
        candidate = _at(item, local)
        if candidate <= local:
            candidate = _at(item, local + timedelta(days=1))
        return candidate

    if repeat == "weekly":
        weekday = int(item.get("weekday") if item.get("weekday") is not None else 0)
        candidate = _at(item, local)
        ahead = (weekday - local.weekday()) % 7
        candidate = _at(item, local + timedelta(days=ahead))
        if candidate <= local:
            candidate = _at(item, local + timedelta(days=ahead + 7))
        return candidate

    if repeat == "monthly":
        day = int(item.get("day_of_month") or 1)
        year, month = local.year, local.month
        for _ in range(13):
            last = calendar.monthrange(year, month)[1]
            candidate = _at(item, local.replace(year=year, month=month, day=min(day, last)))
            if candidate > local:
                return candidate
            month += 1
            if month > 12:
                month, year = 1, year + 1
        return None

    logger.warning(f"scheduler: unknown repeat {repeat!r}, treating it as a one-off")
    return None
