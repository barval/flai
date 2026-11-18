"""Scheduled runs: one row per firing of an action.

A run is not a task. It is a short service loop that answers a prepared goal, so it lives
in its own table and never appears in the Tasks tab — by construction, not by a filter that
a later query could forget. A run can launch a real task through its tools; that task is the
person's and is stored where every task is.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.database import get_db

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED)
OPEN_STATUSES = (STATUS_QUEUED, STATUS_RUNNING)


def _row(row) -> dict | None:
    return dict(row) if row else None


def create_run(item_id: str, user_id: str, due_at: datetime) -> str:
    """Queue one run; ``due_at`` is the moment its answer is due."""
    run_id = str(uuid.uuid4())
    with get_db() as conn:
        conn.cursor().execute(
            "INSERT INTO scheduler_runs (id, item_id, user_id, status, due_at)"
            " VALUES (%s, %s, %s, %s, %s)",
            (run_id, item_id, user_id, STATUS_QUEUED, due_at),
        )
    return run_id


def get_run(run_id: str) -> dict | None:
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM scheduler_runs WHERE id = %s", (run_id,))
        return _row(c.fetchone())


def has_open_run(item_id: str) -> bool:
    """Whether an earlier firing of this item is still owed an answer."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT 1 FROM scheduler_runs WHERE item_id = %s AND status = ANY(%s) LIMIT 1",
            (item_id, list(OPEN_STATUSES)),
        )
        return c.fetchone() is not None


def mark_running(run_id: str) -> dict | None:
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE scheduler_runs SET status = %s, started_at = now()"
            " WHERE id = %s AND status = %s RETURNING *",
            (STATUS_RUNNING, run_id, STATUS_QUEUED),
        )
        return _row(c.fetchone())


def finish_run(
    run_id: str,
    status: str,
    result: str = "",
    error: str = "",
    rounds: int = 0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    from_statuses: tuple[str, ...] | None = None,
) -> bool:
    """Write a terminal state; return whether this writer was the one that moved it.

    ``from_statuses`` is the guard that keeps a late writer from undoing a verdict
    already taken: the tick may cancel an overdue run in the same instant the worker
    finishes it, and the row must end in one state, decided by whoever got there
    first. ``error`` is coalesced, so a cancellation path that carries no reason
    cannot erase the reason the tick wrote.
    """
    where = "WHERE id = %s"
    params: list[object] = [
        status, result or None, error or None, int(rounds), int(prompt_tokens),
        int(completion_tokens), run_id,
    ]
    if from_statuses:
        where += " AND status = ANY(%s)"
        params.append(list(from_statuses))
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE scheduler_runs SET status = %s, result = %s, error = COALESCE(%s, error),"
            f" rounds = %s, prompt_tokens = %s, completion_tokens = %s, finished_at = now() {where}",
            tuple(params),
        )
        return (c.rowcount or 0) > 0


def due_runs(moment: datetime) -> list[dict]:
    """Terminal runs whose answer is due and has not gone out yet."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT * FROM scheduler_runs WHERE status = ANY(%s)"
            " AND delivered_at IS NULL AND due_at <= %s ORDER BY due_at ASC LIMIT 50",
            (list(TERMINAL_STATUSES), moment),
        )
        return [dict(row) for row in c.fetchall()]


def mark_delivered(run_id: str, moment: datetime) -> None:
    with get_db() as conn:
        conn.cursor().execute(
            "UPDATE scheduler_runs SET delivered_at = %s WHERE id = %s", (moment, run_id)
        )


def overdue_runs(moment: datetime, max_lateness_minutes: int) -> list[dict]:
    """Runs that never started and are too old to answer honestly."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT * FROM scheduler_runs WHERE status = %s AND delivered_at IS NULL"
            " AND due_at < %s - make_interval(mins => %s)",
            (STATUS_QUEUED, moment, int(max_lateness_minutes)),
        )
        return [dict(row) for row in c.fetchall()]


def cancel_for_item(item_id: str, moment: datetime) -> int:
    """Silence everything this item owes: nothing is delivered after it is deleted."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE scheduler_runs SET status = %s, finished_at = now(), delivered_at = %s"
            " WHERE item_id = %s AND status = ANY(%s)",
            (STATUS_CANCELLED, moment, item_id, list(OPEN_STATUSES)),
        )
        return c.rowcount or 0


def sweep_stale_running(moment: datetime, older_than_seconds: int) -> int:
    """Cancel a run whose worker vanished between `running` and its terminal write.

    A crash is covered at startup; this covers the smaller failure — a worker that
    survived but lost its write to a transient database error. Without it the row
    stays `running`, the overlap rule reads it forever and the item goes silent
    until the next restart. No live run outlives its own deadline, so the threshold
    is that deadline plus a wide margin.
    """
    if older_than_seconds <= 0:
        return 0
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE scheduler_runs SET status = %s, finished_at = %s, error = 'run_timed_out'"
            " WHERE status = %s AND started_at < %s - make_interval(secs => %s)",
            (STATUS_CANCELLED, moment, STATUS_RUNNING, moment, int(older_than_seconds)),
        )
        return c.rowcount or 0


def recover_after_restart(moment: datetime) -> int:
    """A run left `running` by a crash would silence its item forever: cancel it."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE scheduler_runs SET status = %s, finished_at = now(), delivered_at = %s,"
            " error = COALESCE(error, 'restarted') WHERE status = %s",
            (STATUS_CANCELLED, moment, STATUS_RUNNING),
        )
        return c.rowcount or 0
