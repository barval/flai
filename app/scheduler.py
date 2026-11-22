"""The scheduler: when an item fires next, and what happens when it does.

A reminder is a sentence prepared when it was created — delivering it costs no model
time. An action is a run: a short tool loop over a prepared goal, executed by
``app.scheduler_runner`` and stored in ``scheduler_runs``, never a task in the person's
own list. It may be launched a minute early and finish late, so it is honest about being
late. Keeping the two apart is why a reminder is reliable and an action is a run.

This module holds the recurrence function and, later, the tick. The database lives in
``app.scheduler_store``.
"""

from __future__ import annotations

import calendar
import logging
import math
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Anything faster is a busy loop wearing a schedule's clothes. Work is held to a wider
# floor than a sentence: an action costs a few model rounds and the GPU, a reminder costs
# a database row. The action floor is resolved per host by ``resolve_action_floor``.
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


# How many of an item's own completed runs the median is taken over. Few enough to react to
# a change in the work, many enough that one slow day does not redefine the schedule.
ADAPTIVE_LEAD_RUNS = 5


def median_run_seconds(item_id: str, limit: int = ADAPTIVE_LEAD_RUNS) -> float | None:
    """Median wall-clock duration of the item's last completed runs, or None without history."""
    from app.database import get_db

    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT percentile_cont(0.5) WITHIN GROUP (
                       ORDER BY EXTRACT(EPOCH FROM (finished_at - started_at))
                   ) AS median_seconds
            FROM (
                SELECT started_at, finished_at
                FROM scheduler_runs
                WHERE item_id = %s AND status = 'completed'
                  AND started_at IS NOT NULL AND finished_at IS NOT NULL
                ORDER BY finished_at DESC
                LIMIT %s
            ) recent
            """,
            (str(item_id), int(limit)),
        )
        row = c.fetchone()
    value = row.get("median_seconds") if row else None
    return float(value) if value is not None else None


def lead_ceiling_minutes(default: int = 10) -> int:
    """The largest lead the adaptive history may ask for, from the panel settings."""
    from app.config import setting

    try:
        return max(1, int(setting("SCHEDULER_LEAD_MAX_MIN", default) or default))
    except (TypeError, ValueError):
        return default


def adaptive_lead_minutes(item: dict, configured: int) -> int | None:
    """Lead by what the item's own runs cost, or None when there is nothing to go on.

    A constant assumes every check costs the same; measured, they do not. The median of the
    last few completed runs plus a margin is the smallest lead that has actually been enough,
    and the ceiling keeps one unusually slow day from queueing the next run ten minutes early.
    """
    from app.config import setting

    enabled = str(setting("SCHEDULER_ADAPTIVE_LEAD", True)).strip().lower()
    if enabled in ("0", "false", "off", "no"):
        return None
    item_id = item.get("id")
    if not item_id:
        return None
    median = median_run_seconds(str(item_id))
    if median is None:
        return None
    try:
        margin = max(0, int(setting("SCHEDULER_LEAD_MARGIN_S", 30) or 0))
    except (TypeError, ValueError):
        margin = 30
    wanted = math.ceil((median + margin) / 60)
    return max(int(configured), min(lead_ceiling_minutes(), wanted))


def action_lead_minutes(item: dict, configured: int | None = None) -> int:
    """How early an action is queued, capped by its own interval.

    Half the interval keeps the queue ahead of the target without racing the run it
    replaced; with the one-minute default and a five-minute floor the cap never binds, and
    it is kept only for an installation that sets a lead larger than its own cadence.

    With history the lead is the item's own median duration plus the margin (v13.6); the
    configured value stays the floor and the answer when there is nothing to measure.
    """
    if configured is None:
        from app.config import setting

        try:
            configured = int(setting("SCHEDULER_ACTION_LEAD_MIN", 1) or 0)
        except (TypeError, ValueError):
            configured = 1
    lead = max(0, int(configured))
    adaptive = adaptive_lead_minutes(item, lead)
    if adaptive is not None:
        lead = adaptive
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


def _weekday_set(item: dict) -> list[int]:
    """The weekdays a weekly item fires on, Monday = 0.

    A weekly item carries a set, so "every Tuesday and Thursday" is one schedule. A row
    written before the set existed carries a single ``weekday`` and is read as a set of one,
    which is also the fallback for a set that cannot be used.
    """
    raw = item.get("weekdays")
    if raw is None:
        raw = [item["weekday"]] if item.get("weekday") is not None else [0]
    if isinstance(raw, (int, str)):
        raw = [raw]
    days = set()
    for value in raw:
        try:
            day = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= day <= 6:
            days.add(day)
    return sorted(days) or [0]


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
        first = None
        for weekday in _weekday_set(item):
            ahead = (weekday - local.weekday()) % 7
            candidate = _at(item, local + timedelta(days=ahead))
            if candidate <= local:
                candidate = _at(item, local + timedelta(days=ahead + 7))
            if first is None or candidate < first:
                first = candidate
        return first

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


def enqueue_run(app, item: dict, target: datetime) -> str | None:
    """Queue one service run for this firing, or None when the previous one is still open.

    A run is not a task: it is a short tool loop, it lives in its own table, and the Tasks
    tab never sees it. Overlap is refused rather than queued — a five-minute action whose
    previous run is still going must not build a pile behind a single GPU.
    """
    from app import scheduler_runs, userdb

    item_id = str(item.get("id") or "")
    if item_id and scheduler_runs.has_open_run(item_id):
        logger.info(f"scheduler: run for {item_id} skipped, the previous one is still open")
        return None

    user_id = item.get("user_id")
    run_id = scheduler_runs.create_run(item_id, user_id, target)

    service_class = 2
    try:
        row = userdb.get_user_by_login(user_id) or {}
        service_class = int(row.get("service_class", 2))
    except Exception:  # noqa: BLE001 - an unknown account still gets the lowest class
        service_class = 2
    try:
        app.request_queue.add_service_run(run_id, user_id, _SERVICE_TO_TASK_CLASS.get(service_class, 5), _user_language(user_id))
    except Exception:  # noqa: BLE001 - the run stays queued and the lateness rule cancels it
        logger.exception(f"scheduler: run {run_id} could not be queued")
    return run_id


def deliver(app, item: dict, fired_at: datetime) -> int | str | None:
    """Deliver one firing.

    A reminder is a prepared sentence: ``{when}`` is the only part the clock supplies,
    and it is rendered in the item's zone and the person's language. An action is a
    task: this call enqueues it, and its result is delivered later by the tick.
    """
    kind = str(item.get("kind") or "reminder")
    if kind == "action":
        return enqueue_run(app, item, item.get("next_run_at") or fired_at)

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
    """Cancel a run that never started in time, so yesterday's answer is not delivered today.

    The cancel is conditional on the run still being `queued`: the worker may have
    claimed it in the same instant, and whoever moved the row first owns its verdict.
    """
    from app import scheduler_runs

    lateness = int(app.config.get("SCHEDULER_MAX_LATENESS_MIN", 60) or 0)
    if lateness <= 0:
        return 0

    cancelled = 0
    for run in scheduler_runs.overdue_runs(now, lateness):
        moved = scheduler_runs.finish_run(
            run["id"],
            scheduler_runs.STATUS_CANCELLED,
            error="scheduler_late",
            from_statuses=(scheduler_runs.STATUS_QUEUED,),
        )
        if not moved:
            continue
        cancelled += 1
        logger.warning(f"scheduler: run {run['id']} was too late to start and is cancelled")
    return cancelled


def _sweep_stale_runs(app, now: datetime) -> int:
    """Cancel a run that a live worker left in `running` after its deadline."""
    from app import scheduler_runs

    budget = int(app.config.get("SCHEDULER_RUN_TIMEOUT_S", 180) or 0)
    if budget <= 0:
        return 0
    swept = scheduler_runs.sweep_stale_running(now, budget + 600)
    if swept:
        logger.warning(f"scheduler: {swept} run(s) exceeded their deadline and are cancelled")
    return swept


def run_message(app, item: dict, run: dict, now: datetime) -> str:
    """The sentence a run delivers: its answer, or the reason there is none."""
    from flask_babel import force_locale, gettext

    language = _user_language(item.get("user_id"))
    zone = _zone(item.get("timezone"))
    status = str(run.get("status") or "")
    reason = str(run.get("error") or "")
    result = str(run.get("result") or "").strip() if status != "cancelled" else ""

    with force_locale(language):
        if reason == "scheduler_late":
            body = "⚠️ " + gettext("The action could not run in time")
        elif reason == "run_timed_out":
            body = "⚠️ " + gettext("The action did not finish in time")
        elif result:
            body = result
        elif status == "completed":
            body = gettext("The action finished without an answer")
        elif status == "cancelled":
            body = "⚠️ " + gettext("The action was cancelled")
        else:
            body = "⚠️ " + gettext("The action failed")

        title = str(item.get("title") or "").strip()
        text = f"🛠️ {title}\n\n{body}" if title else body

        target = run.get("due_at")
        finished = run.get("finished_at") or now
        if target is not None and finished - target > timedelta(minutes=1):
            text += "\n\n" + gettext("Finished at {at} instead of {target}").format(
                at=render_when(finished.astimezone(zone), language),
                target=render_when(target.astimezone(zone), language),
            )
    return text


def _deliver_ready(app, now: datetime) -> int:
    """Deliver every finished run whose time has come."""
    from app import scheduler_runs, scheduler_store

    delivered = 0
    for run in scheduler_runs.due_runs(now):
        item = scheduler_store.get_item(str(run.get("item_id"))) if run.get("item_id") else None
        if item is None:
            # The schedule was deleted while its run executed: a message from an item the
            # person removed is exactly what they would report as a bug.
            scheduler_runs.mark_delivered(run["id"], now)
            continue
        deliver_message(app, item, run_message(app, item, run, now))
        scheduler_runs.mark_delivered(run["id"], now)
        delivered += 1
    return delivered


def tick(app, now: datetime | None = None) -> dict:
    """Cancel what cannot run, deliver what is ready, then fire what is due."""
    from app import scheduler_store

    moment = now or datetime.now(UTC)
    if not acquire_lease(app):
        return {"delivered": 0, "skipped": 0, "failed": 0, "skipped_lease": 1}

    _cancel_overdue(app, moment)
    _sweep_stale_runs(app, moment)
    counts = {"delivered": _deliver_ready(app, moment), "skipped": 0, "failed": 0, "missed": 0}
    configured = int(app.config.get("SCHEDULER_ACTION_LEAD_MIN", 1) or 0)
    # The query uses the widest lead any item could have — the adaptive ceiling — while each
    # item is judged by its own, which may be its measured median or the configured value.
    widest = max(configured, lead_ceiling_minutes())
    for item in scheduler_store.due_items(moment, action_lead_minutes=widest):
        if not is_due_now(item, moment, configured):
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
