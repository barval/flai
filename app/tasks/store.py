# app/tasks/store.py
"""Persistence for the Tasks subsystem: tasks + task_steps tables.

All helpers go through ``app.database.get_db()`` with parameterized ``%s``
queries and commit per call (same conventions as ``app/db.py``). The module
never builds SQL from f-string interpolation of user values — only column
names from a fixed whitelist reach the SQL string.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.database import get_db
from app.monitoring.archive import archive_task

# Columns updated via update_task/update_step; values may be datetime objects
# or ISO strings for timestamptz columns, dicts for JSONB columns.
_TASK_FIELDS = {
    "status",
    "title",
    "step_plan",
    "next_step_index",
    "accumulated_exec_seconds",
    "doc_ids",
    "deep_analysis",
    "images",
    "audio",
    "visual_bible",
    "visual_bible_attempted",
    "visual_bible_stale",
    "started_at",
    "completed_at",
    "abort_reason",
    "lang",
    "scheduler_item_id",
    "scheduler_deliver_at",
    "scheduler_delivered_at",
}
_STEP_FIELDS = {
    "status",
    "result",
    "prompt",
    # A redo re-opens the failed row with the reviewer's refined wording
    # (the plan review may sharpen the goal/criterion of the same step).
    "goal",
    "checkpoint_criteria",
    "checkpoint_result",
    "checkpoint_verdict",
    "attempts",
    "last_error",
    "exec_seconds",
    "started_at",
    "finished_at",
    "interrupt_reason",
    "extra",
}
_JSONB_FIELDS = {"step_plan", "extra", "doc_ids", "images", "audio", "visual_bible"}
_TIMESTAMPTZ_FIELDS = {"started_at", "finished_at", "completed_at", "scheduler_deliver_at", "scheduler_delivered_at"}


def _cast_for(field: str) -> str:
    """Value-side cast prefix for string-typed parameters (ISO strings).

    PostgreSQL casts the PARAMETER, not the column: ``started_at = %s::timestamptz``
    is valid, while ``started_at::timestamptz = %s`` is a syntax error.
    """
    if field in _TIMESTAMPTZ_FIELDS:
        return "%s::timestamptz"
    return "%s"


def _prepare_value(field: str, value: Any) -> Any:
    """Adapt a field value for a parameterized query (JSONB dicts → JSON)."""
    if field in _JSONB_FIELDS and isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _build_update(
    table: str,
    allowed: set[str],
    fields: dict[str, Any],
) -> tuple[str, list[Any]]:
    """Build the SET clause from whitelisted keys (tasks table bumps updated_at)."""
    keys = [k for k in fields if k in allowed]
    if not keys:
        raise ValueError("no updatable fields")
    prepared = [(k, _prepare_value(k, fields[k])) for k in keys]
    # task_steps has no updated_at column; only tasks tracks it.
    if table == "tasks":
        sets = ", ".join(f"{k} = {_cast_for(k)}" for k, _ in prepared) + ", updated_at = NOW()"
    else:
        sets = ", ".join(f"{k} = {_cast_for(k)}" for k, _ in prepared)
    vals = [v for _, v in prepared]
    return sets, vals


def create_task(
    user_id: str,
    task_class: int,
    title: str,
    lang: str,
    response_style: str,
    doc_ids: list[str] | None = None,
    deep_analysis: bool = False,
    images: list[dict[str, str]] | None = None,
    scheduler_item_id: str | None = None,
    scheduler_deliver_at: Any = None,
) -> str:
    """Insert a queued task row; return the generated task id (UUID string).

    ``doc_ids`` are the documents attached to the task (the Documents-panel
    selection at submit time) — an empty list means "all of the user's
    documents", matching the chat behaviour.

    ``deep_analysis`` is the 🔬 toggle state at submit time: the planner is
    told to include a deep-analysis (``rlm``) step over those documents.

    ``images`` are the images attached in the task view (v12.6 vision): a list
    of ``{file_path, file_name}`` dicts saved under the user's upload folder.
    """
    task_id = str(uuid.uuid4())
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """INSERT INTO tasks (id, user_id, task_class, title, status, step_plan, next_step_index,
               accumulated_exec_seconds, lang, response_style, doc_ids, deep_analysis, images,
               scheduler_item_id, scheduler_deliver_at, created_at, updated_at)
               VALUES (%s, %s, %s, %s, 'queued', '{}'::jsonb, 0, 0, %s, %s, %s::jsonb, %s, %s::jsonb,
                       %s, %s::timestamptz, NOW(), NOW())""",
            (
                task_id,
                user_id,
                task_class,
                title,
                lang,
                response_style,
                _prepare_value("doc_ids", doc_ids or []),
                bool(deep_analysis),
                _prepare_value("images", images or []),
                scheduler_item_id,
                scheduler_deliver_at,
            ),
        )
    return task_id


def get_task(task_id: str) -> dict | None:
    """Fetch one task row as a dict, or None when absent."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM tasks WHERE id = %s", (task_id,))
        row = c.fetchone()
        return dict(row) if row else None


def get_tasks_by_user(user_id: str) -> list[dict]:
    """Every task of one user, newest first (the Tasks panel list)."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM tasks WHERE user_id = %s ORDER BY created_at DESC", (user_id,))
        return [dict(r) for r in c.fetchall()]


def delete_task(task_id: str) -> None:
    """Remove a task, its steps and the messages written under its own id.

    ``task_steps.task_id`` has ``ON DELETE CASCADE``; the explicit step delete
    is a safety net for databases without cascade enforcement.

    A step sub-task error is persisted as a chat message whose ``session_id`` is
    the task id (``_build_error_response``). Nothing reads those rows — the task
    view renders steps — and without this delete they outlive the task as orphan
    ``messages`` rows (15 found in a live database).
    """
    with get_db() as conn:
        c = conn.cursor()
        archive_task(c, task_id)
        c.execute("DELETE FROM messages WHERE session_id = %s", (task_id,))
        c.execute("DELETE FROM task_steps WHERE task_id = %s", (task_id,))
        c.execute("DELETE FROM tasks WHERE id = %s", (task_id,))


def update_task(task_id: str, **fields: Any) -> dict:
    """Update allowed task columns; return the fresh row (None when task is absent)."""
    sets, vals = _build_update("tasks", _TASK_FIELDS, fields)
    with get_db() as conn:
        c = conn.cursor()
        c.execute(f"UPDATE tasks SET {sets} WHERE id = %s RETURNING *", (*vals, task_id))
        row = c.fetchone()
        return dict(row) if row else None


def add_step(
    task_id: str,
    seq: int,
    kind: str,
    goal: str = "",
    tool: str = "",
    checkpoint_criteria: str = "",
    parent_step_id: str | None = None,
    extra: dict | None = None,
) -> str:
    """Insert a pending task_steps row; return the generated step id (UUID string)."""
    step_id = str(uuid.uuid4())
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """INSERT INTO task_steps (id, task_id, seq, parent_step_id, kind, status, goal, tool,
               checkpoint_criteria, attempts, extra) VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s, %s, 0, %s)""",
            (step_id, task_id, seq, parent_step_id, kind, goal, tool, checkpoint_criteria, json.dumps(extra or {})),
        )
    return step_id


def get_step(step_id: str) -> dict | None:
    """Fetch one task_steps row as a dict, or None when absent."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM task_steps WHERE id = %s", (step_id,))
        row = c.fetchone()
        return dict(row) if row else None


def update_step(step_id: str, **fields: Any) -> dict:
    """Update allowed task_steps columns; return the fresh row (None when absent).

    ``started_at``/``finished_at`` accept datetime objects or ISO strings
    (an explicit ::timestamptz cast makes the string form unambiguous).
    """
    sets, vals = _build_update("task_steps", _STEP_FIELDS, fields)
    with get_db() as conn:
        c = conn.cursor()
        c.execute(f"UPDATE task_steps SET {sets} WHERE id = %s RETURNING *", (*vals, step_id))
        row = c.fetchone()
        return dict(row) if row else None


def delete_pending_steps_after(task_id: str, seq: int) -> int:
    """Delete the task's still-pending step rows beyond position ``seq``.

    A plan rewrite replaces the remaining positions; the old rows must go with
    it, because the executor resolves a step row **by seq** and would otherwise
    reuse a superseded row's goal, tool and criteria. Terminal rows (anything
    already run) are never touched — a rewrite is forward-only.
    """
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "DELETE FROM task_steps WHERE task_id = %s AND seq > %s AND status = 'pending'",
            (task_id, int(seq)),
        )
        return int(c.rowcount or 0)


def move_extra_steps_after(task_id: str, seq: int, base: int) -> int:
    """Shift the task's terminal rows beyond ``seq`` above a grown plan.

    The plan positions occupy the low ``seq`` range and the rows the runtime
    appends (a clarifying answer, the final summary) live above them. A rewrite
    that grows the tail would reuse a ``seq`` one of them already holds — and
    the executor resolves a step row **by seq**, so the new step would take that
    row and overwrite the user's answer. ``base + seq`` keeps the moved rows
    above the new tail in their relative order.
    """
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE task_steps SET seq = %s + seq WHERE task_id = %s AND seq > %s AND status <> 'pending'",
            (int(base), task_id, int(seq)),
        )
        return int(c.rowcount or 0)


# ── Scheduled actions (v13.5) ─────────────────────────────────────────────────
#
# An action is an ordinary task that knows three extra things: which scheduled item
# started it, when its result is due, and whether that result has been delivered.
# Delivery is polled, not pushed, so every ending the engine has — completed, failed,
# expired, cancelled — is covered by one query instead of a hook on each path.

ACTIVE_SCHEDULER_STATUSES = ("queued", "active", "frozen", "awaiting_user")
TERMINAL_SCHEDULER_STATUSES = ("completed", "failed", "expired", "cancelled")


def active_scheduler_task(item_id: str) -> dict | None:
    """The item's task that is still owed a result, or None.

    ``frozen`` counts: it is the normal state while the user chats, and counting only
    ``active`` would pile a task on every firing during a long conversation.
    ``awaiting_user`` counts too — a task waiting for an answer is not finished, and a
    second one would ask the same question twice.
    """
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT * FROM tasks WHERE scheduler_item_id = %s AND status = ANY(%s)"
            " ORDER BY created_at DESC LIMIT 1",
            (item_id, list(ACTIVE_SCHEDULER_STATUSES)),
        )
        row = c.fetchone()
        return dict(row) if row else None


def due_scheduler_tasks(moment) -> list[dict]:
    """Terminal tasks whose result is due and has not gone out yet."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT * FROM tasks WHERE scheduler_item_id IS NOT NULL"
            " AND scheduler_delivered_at IS NULL"
            " AND status = ANY(%s)"
            " AND scheduler_deliver_at IS NOT NULL AND scheduler_deliver_at <= %s"
            " ORDER BY scheduler_deliver_at ASC LIMIT 50",
            (list(TERMINAL_SCHEDULER_STATUSES), moment),
        )
        return [dict(row) for row in c.fetchall()]


def overdue_scheduler_tasks(moment, max_lateness_minutes: int) -> list[dict]:
    """Queued tasks that are past their due time by more than the allowed lateness.

    A task that never started must not hold the single task slot all day; the caller
    cancels these through the ordinary cancel flag.
    """
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT * FROM tasks WHERE scheduler_item_id IS NOT NULL"
            " AND scheduler_delivered_at IS NULL"
            " AND status = ANY(%s)"
            " AND scheduler_deliver_at IS NOT NULL"
            " AND scheduler_deliver_at < %s - make_interval(mins => %s)",
            (list(ACTIVE_SCHEDULER_STATUSES), moment, int(max_lateness_minutes)),
        )
        return [dict(row) for row in c.fetchall()]


def mark_scheduler_delivered(task_id: str, moment) -> None:
    """Record that the result went out, so the next tick does not repeat it."""
    with get_db() as conn:
        conn.cursor().execute(
            "UPDATE tasks SET scheduler_delivered_at = %s, updated_at = NOW() WHERE id = %s",
            (moment, task_id),
        )


def task_result_text(task_id: str) -> str:
    """The task's closing ``final`` step, which is where every ending writes its text."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT result FROM task_steps WHERE task_id = %s AND kind = 'final'"
            " ORDER BY seq DESC LIMIT 1",
            (task_id,),
        )
        row = c.fetchone()
    return str((row or {}).get("result") or "").strip()
