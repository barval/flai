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


# ── Delivery ─────────────────────────────────────────────────────────────────

INTERRUPTING_KINDS = ("reminder",)


def session_title(kind: str, language: str) -> str:
    """The title of a system chat, in the person's language.

    Literal ``gettext`` calls on purpose: the title is stored in the row, so it is not
    rendered from a template and the extractor would otherwise never see the string.
    """
    from flask_babel import force_locale, gettext

    with force_locale(language):
        if kind == "action":
            return gettext("⚙️ Scheduler: actions")
        return gettext("🔔 Scheduler: reminders")


def _publisher(app):
    """The app's SSE publisher, or None where events are not wired (tests, CLI)."""
    publisher = getattr(app, "events_publisher", None)
    if publisher is not None:
        return publisher
    try:
        from app.events import get_events_publisher

        return get_events_publisher(app)
    except Exception:  # noqa: BLE001 - a missing publisher must not lose the delivery
        return None


def _user_language(user_id: str | None) -> str:
    """The language the person reads, which is the language their reminders are written in."""
    if not user_id:
        return "ru"
    try:
        from app import userdb

        row = userdb.get_user_by_login(user_id) or {}
    except Exception:  # noqa: BLE001 - a missing account must not stop a delivery
        return "ru"
    language = row.get("language")
    return language if language in ("ru", "en") else "ru"


def scheduler_session(app, user_id: str, kind: str) -> str:
    """The user's chat for this kind, created on first delivery and after a deletion."""
    from app import db
    from app.database import get_db

    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id FROM chat_sessions WHERE user_id = %s AND scheduler_kind = %s"
            " ORDER BY created_at DESC LIMIT 1",
            (user_id, kind),
        )
        row = c.fetchone()
    if row:
        return row["id"]

    language = _user_language(user_id)
    title = session_title(kind, language)
    session_id = db.create_session(user_id, title=title, lang=language)
    with get_db() as conn:
        conn.cursor().execute(
            "UPDATE chat_sessions SET scheduler_kind = %s WHERE id = %s", (kind, session_id)
        )
    return session_id


def render_when(moment: datetime | None, lang: str = "ru") -> str:
    """The firing time as the person reads it: "18 November, 09:00"."""
    if moment is None:
        return ""
    from babel.dates import format_datetime

    return format_datetime(moment, "d MMMM, HH:mm", locale=lang if lang in ("ru", "en") else "ru")


def deliver_message(app, item: dict, text: str) -> int:
    """Write one delivered message into its chat and announce it.

    The message carries the item it belongs to, so a re-render can style it and an
    export keeps the provenance. Only a reminder interrupts: an action's result is a
    message, which the person finds when they open the chat.
    """
    from app import db

    user_id = item.get("user_id")
    kind = str(item.get("kind") or "reminder")
    session_id = scheduler_session(app, user_id, kind)
    message_id = db.save_message(
        session_id,
        "assistant",
        text,
        user_id=user_id,
        scheduler_item_id=item.get("id"),
        scheduler_kind=kind,
    )
    if kind in INTERRUPTING_KINDS:
        publisher = _publisher(app)
        if publisher is not None:
            fired_at = item.get("next_run_at")
            publisher.publish(
                user_id,
                "scheduler_popup",
                {
                    "item_id": str(item.get("id") or ""),
                    "kind": kind,
                    "title": str(item.get("title") or ""),
                    "text": text,
                    "session_id": session_id,
                    "message_id": message_id,
                    "fired_at": fired_at.isoformat() if hasattr(fired_at, "isoformat") else None,
                },
            )
    return message_id


def deliver(app, item: dict, fired_at: datetime) -> int:
    """Deliver one firing.

    A reminder is a prepared sentence: ``{when}`` is the only part the clock supplies,
    and it is rendered in the item's zone and the person's language. An action is a
    task, so its delivery belongs to the Tasks engine.
    """
    kind = str(item.get("kind") or "reminder")
    if kind == "action":
        raise NotImplementedError("an action is delivered by the Tasks engine")

    user_id = item.get("user_id")
    language = _user_language(user_id)
    scheduled = item.get("next_run_at") or fired_at
    moment = scheduled.astimezone(_zone(item.get("timezone")))
    body = str(item.get("body") or "").strip() or str(item.get("title") or "").strip()
    text = body.replace("{when}", render_when(moment, language))
    return deliver_message(app, item, text)


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
