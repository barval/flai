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
from datetime import UTC, datetime, time, timedelta
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


# ── The tick ─────────────────────────────────────────────────────────────────

TICK_SECONDS = 30
LEASE_KEY = "scheduler:tick"
LEASE_SECONDS = 25


def _redis(app):
    """The application's Redis client, or None outside a request (tests, CLI)."""
    queue = getattr(app, "request_queue", None)
    return getattr(queue, "redis", None)


def acquire_lease(app) -> bool:
    """One tick at a time across workers; without Redis a single process is assumed."""
    client = _redis(app)
    if client is None:
        return True
    try:
        return bool(client.set(LEASE_KEY, "1", nx=True, ex=LEASE_SECONDS))
    except Exception:  # noqa: BLE001 - a broken lease must not stop the scheduler
        logger.warning("scheduler: lease unavailable, running the tick anyway")
        return True


def deliver(app, item: dict, fired_at: datetime) -> str:
    """Deliver one firing. Replaced by the delivery task; a stub keeps the tick testable."""
    raise NotImplementedError("delivery is not wired yet")


def _fire(app, item: dict, now: datetime) -> dict:
    """One due item: deliver it, then schedule the next firing."""
    from app import scheduler_store

    counts = {"delivered": 0, "failed": 0}
    try:
        deliver(app, item, now)
        counts["delivered"] = 1
    except NotImplementedError:
        raise
    except Exception:  # noqa: BLE001 - one broken item must not stop the tick
        logger.exception(f"scheduler: item {item.get('id')} could not be delivered")
        counts["failed"] = 1

    following = next_after(item, now)
    if following is None:
        scheduler_store.update_item(item["id"], next_run_at=None, last_run_at=now, enabled=False)
    else:
        scheduler_store.update_item(item["id"], next_run_at=following, last_run_at=now)
    return counts


def tick(app, now: datetime | None = None) -> dict:
    """Deliver everything due. Returns counts so a test can read what happened."""
    from app import scheduler_store

    moment = now or datetime.now(UTC)
    if not acquire_lease(app):
        return {"delivered": 0, "failed": 0, "skipped_lease": 1}

    counts = {"delivered": 0, "failed": 0}
    for item in scheduler_store.due_items(moment):
        result = _fire(app, item, moment)
        counts["delivered"] += result["delivered"]
        counts["failed"] += result["failed"]
    return counts


def start(app) -> None:
    """Background tick, beside the warm-up and the memory watcher."""
    import threading
    import time as time_module

    if not app.config.get("SCHEDULER_ENABLED", True):
        return

    def loop():
        while True:
            try:
                with app.app_context():
                    tick(app)
            except Exception:  # noqa: BLE001 - the loop must survive anything
                logger.exception("scheduler: tick failed")
            time_module.sleep(TICK_SECONDS)

    thread = threading.Thread(target=loop, daemon=True, name="scheduler-tick")
    thread.start()
    logger.info(f"scheduler: tick thread started (every {TICK_SECONDS}s)")
