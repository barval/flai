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
    "started_at",
    "completed_at",
    "abort_reason",
    "lang",
}
_STEP_FIELDS = {
    "status",
    "result",
    "prompt",
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
_JSONB_FIELDS = {"step_plan", "extra", "doc_ids", "images", "audio"}
_TIMESTAMPTZ_FIELDS = {"started_at", "finished_at", "completed_at"}


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
               accumulated_exec_seconds, lang, response_style, doc_ids, deep_analysis, images, created_at, updated_at)
               VALUES (%s, %s, %s, %s, 'queued', '{}'::jsonb, 0, 0, %s, %s, %s::jsonb, %s, %s::jsonb, NOW(), NOW())""",
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
    """Remove a task and its steps.

    ``task_steps.task_id`` has ``ON DELETE CASCADE``; the explicit step delete
    is a safety net for databases without cascade enforcement.
    """
    with get_db() as conn:
        c = conn.cursor()
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
) -> str:
    """Insert a pending task_steps row; return the generated step id (UUID string)."""
    step_id = str(uuid.uuid4())
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """INSERT INTO task_steps (id, task_id, seq, parent_step_id, kind, status, goal, tool,
               checkpoint_criteria, attempts) VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s, %s, 0)""",
            (step_id, task_id, seq, parent_step_id, kind, goal, tool, checkpoint_criteria),
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
