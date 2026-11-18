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

# Anything faster is a busy loop wearing a schedule's clothes. Work is held to a wider
# floor than a sentence: an action costs a planning loop and the GPU, a reminder costs a
# database row.
MIN_MINUTES = 15


def min_interval_minutes(kind: str | None = None) -> int:
    """The smallest useful step for a repeating item of this kind."""
    if str(kind or "reminder") != "action":
        return MIN_MINUTES
    from app.config import setting

    try:
        value = int(setting("SCHEDULER_ACTION_MIN_MINUTES", 0) or 0)
    except (TypeError, ValueError):
        value = 0
    # 0 (or nonsense) means "the conservative floor": startup resolves the hardware and
    # writes 5 on a GPU host or 15 on a CPU-only one, so this is only the fallback for a
    # value saved by hand.
    return value if value > 0 else MIN_MINUTES

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


def action_lead_minutes(item: dict, configured: int | None = None) -> int:
    """How early an action is enqueued, capped by its own interval.

    A ten-minute lead under a five-minute cadence put the next task in the queue before the
    previous one had started, so every second firing was skipped as an overlap. Half the
    interval keeps the queue ahead of the target without racing the run it replaced; a daily
    or weekly item keeps the configured lead, because half of a day is not a lead.
    """
    if configured is None:
        from app.config import setting

        try:
            configured = int(setting("SCHEDULER_ACTION_LEAD_MIN", 10) or 0)
        except (TypeError, ValueError):
            configured = 10
    lead = max(0, int(configured))
    if str(item.get("repeat") or "once") != "minutes":
        return lead
    try:
        every = int(item.get("every_minutes") or 0)
    except (TypeError, ValueError):
        return lead
    if every <= 0:
        return lead
    return max(0, min(lead, every // 2))


def is_due_now(item: dict, moment: datetime, max_lead: int) -> bool:
    """Whether this item has reached its firing window, given its own lead."""
    scheduled = item.get("next_run_at")
    if scheduled is None:
        return True
    if str(item.get("kind") or "reminder") == "action":
        return moment >= scheduled - timedelta(minutes=action_lead_minutes(item, max_lead))
    return moment >= scheduled


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
        floor = min_interval_minutes(item.get("kind"))
        step = max(floor, int(item.get("every_minutes") or floor))
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
            return gettext("🛠️ Scheduler: actions")
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


def rename_action_chats(app) -> int:
    """Point existing action chats at the current mark.

    The title is written once, when the chat is created, so changing the mark would leave
    every installation with the old one forever. One UPDATE, keyed by the kind, at startup.
    """
    from app.database import get_db

    with get_db() as conn:
        c = conn.cursor()
        # Only the mark is replaced, so a chat the person renamed in their own language keeps
        # its words; one they renamed away from the mark at all is left alone.
        c.execute(
            "UPDATE chat_sessions SET title = '🛠️' || substr(title, 3)"
            " WHERE scheduler_kind = 'action' AND title LIKE '⚙️ %'"
        )
        changed = c.rowcount
    return changed or 0


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


# The user's service class (0/1/2) is the chat priority; a background action is always
# one step below it, which is what keeps a task from competing with a conversation.
_SERVICE_TO_TASK_CLASS = {0: 3, 1: 4, 2: 5}


def enqueue_action(app, item: dict, target: datetime) -> str | None:
    """Put one action on the Tasks engine, or return None when the previous run is not done.

    Overlap is refused rather than queued: an every-fifteen-minutes action whose previous
    run is still going must not build a pile behind a single GPU.
    """
    from app import userdb
    from app.tasks import store as task_store

    item_id = str(item.get("id") or "")
    if item_id and task_store.active_scheduler_task(item_id):
        logger.info(f"scheduler: action {item_id} skipped, its previous task is still open")
        return None

    user_id = item.get("user_id")
    service_class = 2
    try:
        row = userdb.get_user_by_login(user_id) or {}
        service_class = int(row.get("service_class", 2))
    except Exception:  # noqa: BLE001 - an unknown account still gets the lowest class
        service_class = 2
    title = str(item.get("title") or "").strip() or str(item.get("body") or "").strip()[:60]
    return task_store.create_task(
        user_id,
        _SERVICE_TO_TASK_CLASS.get(service_class, 5),
        title,
        _user_language(user_id),
        "neutral",
        scheduler_item_id=item_id or None,
        scheduler_deliver_at=target,
    )


def deliver(app, item: dict, fired_at: datetime) -> int | str | None:
    """Deliver one firing.

    A reminder is a prepared sentence: ``{when}`` is the only part the clock supplies,
    and it is rendered in the item's zone and the person's language. An action is a
    task: this call enqueues it, and its result is delivered later by the tick.
    """
    kind = str(item.get("kind") or "reminder")
    if kind == "action":
        return enqueue_action(app, item, item.get("next_run_at") or fired_at)

    user_id = item.get("user_id")
    language = _user_language(user_id)
    scheduled = item.get("next_run_at") or fired_at
    moment = scheduled.astimezone(_zone(item.get("timezone")))
    body = str(item.get("body") or "").strip() or str(item.get("title") or "").strip()
    text = body.replace("{when}", render_when(moment, language))
    skipped = int(item.get("_skipped") or 0)
    if skipped > 0:
        # "last" (and "all" past its cap) delivers one firing but says how many it stands for.
        from flask_babel import force_locale, gettext

        with force_locale(language):
            text = str(gettext("Skipped firings: {count}")).format(count=skipped) + "\n\n" + text
    return deliver_message(app, item, text)


def _advance(item: dict, scheduled: datetime, now: datetime) -> datetime | None:
    """The next firing after this one, anchored to the schedule rather than to the clock.

    Advancing from ``now`` made a schedule drift: an action enqueued ten minutes early
    would have computed its next minute from the early moment, and a reminder that fired
    late would have moved its own time. The loop then skips any occurrence that is
    already in the past, so a long outage does not leave the item permanently due.
    """
    following = next_after(item, scheduled)
    guard = 0
    while following is not None and following <= now and guard < 1000:
        following = next_after(item, following)
        guard += 1
    return following


def missed_policy(app, kind: str) -> str:
    """The policy for this kind, and ``last`` for anything that makes no sense."""
    key = "SCHEDULER_MISSED_ACTION" if kind == "action" else "SCHEDULER_MISSED_REMINDER"
    value = str(app.config.get(key) or "last").strip().lower()
    return value if value in ("all", "last", "none") else "last"


def firing_series(item: dict, start: datetime, now: datetime, boundary: int = 10000) -> list[datetime]:
    """Every firing from ``start`` up to ``now``, oldest first.

    The walk is bounded: an every-fifteen-minutes item after a hundred days of downtime
    is 9 600 moments, which is cheap to count and must never become an infinite loop.
    """
    moments: list[datetime] = []
    moment: datetime | None = start
    while moment is not None and moment <= now and len(moments) < boundary:
        moments.append(moment)
        moment = next_after(item, moment)
    return moments


def selected_firings(policy: str, kind: str, moments: list[datetime], cap: int) -> tuple[list[datetime], int]:
    """Which missed firings are delivered, and how many are skipped.

    ``all`` is the only policy that catches up, and it is bounded: past ``cap`` the item
    behaves as ``last`` and says how many were skipped, because 672 messages in one tick
    is a flood, not a policy. An action always catches up once — four copies of the same
    work is a pile behind one GPU, not four results.
    """
    if not moments:
        return [], 0
    if policy == "none":
        return [], len(moments)
    if kind == "action":
        return [moments[-1]], len(moments) - 1
    if policy == "all" and cap > 0 and len(moments) <= cap:
        return list(moments), 0
    return [moments[-1]], len(moments) - 1


def _fire(app, item: dict, now: datetime) -> dict:
    """One due item: deliver (or enqueue) it, then schedule the next firing.

    A firing that is more than ``SCHEDULER_GRACE_MIN`` late is missed, and the missed
    policy decides what the person hears: every firing, only the newest one named with
    how many were skipped, or nothing at all (the item still advances).
    """
    from app import scheduler_store

    counts = {"delivered": 0, "skipped": 0, "failed": 0, "missed": 0}
    kind = str(item.get("kind") or "reminder")
    scheduled = item.get("next_run_at") or now
    grace = timedelta(minutes=max(0.0, float(app.config.get("SCHEDULER_GRACE_MIN", 2) or 0)))
    cap = int(app.config.get("SCHEDULER_CATCHUP_MAX", 20) or 0)

    if scheduled <= now - grace:
        moments = firing_series(item, scheduled, now)
        selected, missed = selected_firings(missed_policy(app, kind), kind, moments, cap)
        counts["missed"] = missed
    else:
        selected, missed = [scheduled], 0

    for moment in selected:
        # Each firing carries its own moment, so {when} names the time it was due for
        # rather than the time it was delivered.
        payload = dict(item, next_run_at=moment, _skipped=missed)
        try:
            outcome = deliver(app, payload, moment)
            if outcome is None:
                counts["skipped"] += 1
            else:
                counts["delivered"] += 1
        except Exception:  # noqa: BLE001 - one broken firing must not stop the item
            logger.exception(f"scheduler: item {item.get('id')} could not be delivered")
            counts["failed"] += 1

    following = _advance(item, scheduled, now)
    if following is None:
        scheduler_store.update_item(item["id"], next_run_at=None, last_run_at=now, enabled=False)
    else:
        scheduler_store.update_item(item["id"], next_run_at=following, last_run_at=now)
    return counts


def _cancel_overdue(app, now: datetime) -> int:
    """Cancel an action that never started in time, so it cannot hold the slot all day."""
    from app.tasks import store as task_store

    lateness = int(app.config.get("SCHEDULER_MAX_LATENESS_MIN", 60) or 0)
    if lateness <= 0:
        return 0

    cancelled = 0
    for task in task_store.overdue_scheduler_tasks(now, lateness):
        client = _redis(app)
        if client is not None:
            try:
                client.set(f"task:cancelled:{task['id']}", "1", ex=3600)
            except Exception:  # noqa: BLE001 - the flag is best effort, as in the API
                logger.warning(f"scheduler: could not raise the cancel flag for {task['id']}")
        if task.get("status") in ("queued", "frozen", "awaiting_user"):
            task_store.update_task(task["id"], status="cancelled", abort_reason="scheduler_late")
        else:
            # A running task is cancelled by its worker, which watches the flag; the
            # reason has to be written now, because that worker only writes the status.
            task_store.update_task(task["id"], abort_reason="scheduler_late")
        cancelled += 1
        logger.warning(f"scheduler: task {task['id']} was too late to run and is cancelled")
    return cancelled


def action_message(app, item: dict, task: dict, now: datetime) -> str:
    """The sentence an action delivers: its result, or the reason there is none."""
    from flask_babel import force_locale, gettext

    from app.tasks import store as task_store

    language = _user_language(item.get("user_id"))
    zone = _zone(item.get("timezone"))
    status = str(task.get("status") or "")
    reason = str(task.get("abort_reason") or "")
    result = task_store.task_result_text(str(task.get("id"))) if status != "cancelled" else ""

    with force_locale(language):
        if reason == "scheduler_late":
            body = "⚠️ " + gettext("The action could not run in time")
        elif result:
            body = result
        elif status == "completed":
            body = gettext("The action finished without an answer")
        elif status == "cancelled":
            body = "⚠️ " + gettext("The action was cancelled")
        elif status == "expired":
            body = "⚠️ " + gettext("The action ran out of time")
        else:
            body = "⚠️ " + gettext("The action failed")

        title = str(item.get("title") or "").strip()
        text = f"⚙️ {title}\n\n{body}" if title else body

        target = task.get("scheduler_deliver_at")
        if target is not None and now - target > timedelta(minutes=1):
            text += "\n\n" + gettext("Finished at {at} instead of {target}").format(
                at=render_when(now.astimezone(zone), language),
                target=render_when(target.astimezone(zone), language),
            )
    return text


def _deliver_ready(app, now: datetime) -> int:
    """Deliver every finished action whose time has come."""
    from app import scheduler_store
    from app.tasks import store as task_store

    delivered = 0
    for task in task_store.due_scheduler_tasks(now):
        item = scheduler_store.get_item(str(task.get("scheduler_item_id"))) if task.get("scheduler_item_id") else None
        if item is None:
            # The schedule was deleted while the task ran: a message from an item the
            # person removed is exactly what they would report as a bug.
            task_store.mark_scheduler_delivered(task["id"], now)
            continue
        deliver_message(app, item, action_message(app, item, task, now))
        task_store.mark_scheduler_delivered(task["id"], now)
        delivered += 1
    return delivered


def tick(app, now: datetime | None = None) -> dict:
    """Cancel what cannot run, deliver what is ready, then fire what is due."""
    from app import scheduler_store

    moment = now or datetime.now(UTC)
    if not acquire_lease(app):
        return {"delivered": 0, "skipped": 0, "failed": 0, "skipped_lease": 1}

    _cancel_overdue(app, moment)
    counts = {"delivered": _deliver_ready(app, moment), "skipped": 0, "failed": 0, "missed": 0}
    lead = int(app.config.get("SCHEDULER_ACTION_LEAD_MIN", 10) or 0)
    for item in scheduler_store.due_items(moment, action_lead_minutes=lead):
        # The query uses the widest lead any item could have; each item is then judged by
        # its own, which is half its interval for a frequent one.
        if not is_due_now(item, moment, lead):
            continue
        result = _fire(app, item, moment)
        counts["delivered"] += result["delivered"]
        counts["skipped"] += result["skipped"]
        counts["failed"] += result["failed"]
        counts["missed"] += result["missed"]
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
