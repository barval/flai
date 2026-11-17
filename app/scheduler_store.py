"""Scheduled items in PostgreSQL: one row per reminder or action."""

from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Any

from app.database import get_db


def _row(row) -> dict | None:
    return dict(row) if row else None


def create_item(
    user_id: str,
    kind: str,
    title: str,
    body: str,
    repeat: str = "once",
    at: datetime | None = None,
    time_of_day: time | None = None,
    weekday: int | None = None,
    day_of_month: int | None = None,
    every_minutes: int | None = None,
    timezone_name: str = "UTC",
) -> str:
    """Insert one item; ``next_run_at`` is the first firing, or None for a one-off with no time."""
    item_id = str(uuid.uuid4())
    with get_db() as conn:
        conn.cursor().execute(
            """
            INSERT INTO scheduler_items
                (id, user_id, kind, title, body, repeat, every_minutes, weekday, day_of_month,
                 time_of_day, timezone, next_run_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                item_id,
                user_id,
                kind,
                title,
                body,
                repeat,
                every_minutes,
                weekday,
                day_of_month,
                time_of_day,
                timezone_name,
                at,
            ),
        )
    return item_id


def get_item(item_id: str) -> dict | None:
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM scheduler_items WHERE id = %s", (item_id,))
        return _row(c.fetchone())


def list_items(user_id: str) -> list[dict]:
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT * FROM scheduler_items WHERE user_id = %s ORDER BY created_at DESC", (user_id,)
        )
        return [dict(r) for r in c.fetchall()]


def due_items(moment: datetime, limit: int = 200) -> list[dict]:
    """Enabled items whose time has come, oldest first, capped."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT * FROM scheduler_items
            WHERE enabled AND next_run_at IS NOT NULL AND next_run_at <= %s
            ORDER BY next_run_at ASC
            LIMIT %s
            """,
            (moment, limit),
        )
        return [dict(r) for r in c.fetchall()]


def update_item(item_id: str, **fields: Any) -> dict | None:
    allowed = {"title", "body", "repeat", "every_minutes", "weekday", "day_of_month",
               "time_of_day", "timezone", "next_run_at", "last_run_at", "enabled"}
    sets, values = [], []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"unknown scheduler field: {key}")
        sets.append(f"{key} = %s")
        values.append(value)
    if not sets:
        return get_item(item_id)
    values.append(item_id)
    with get_db() as conn:
        conn.cursor().execute(f"UPDATE scheduler_items SET {', '.join(sets)} WHERE id = %s", values)
    return get_item(item_id)


def delete_item(item_id: str) -> None:
    with get_db() as conn:
        conn.cursor().execute("DELETE FROM scheduler_items WHERE id = %s", (item_id,))
