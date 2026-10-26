# app/settings_store.py
"""Persistence for admin-editable settings.

Values live in the ``settings`` table and override the .env defaults at startup
and immediately after a save. Every change is written to ``settings_audit``
(secrets are redacted) so an admin can see who changed what.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.database import get_db
from app.settings_registry import (
    SECRET_KEYS,
    build_schema,
    coerce,
    config_key,
    default_for,
    movable_keys,
)

logger = logging.getLogger(__name__)

AUDIT_RETENTION_DAYS = 45
REDACTED = "••••"


def load_overrides() -> dict[str, Any]:
    """Read every stored override; malformed rows are skipped, not fatal."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM settings")
        rows = cursor.fetchall()
    overrides: dict[str, Any] = {}
    for row in rows:
        try:
            overrides[row["key"]] = json.loads(row["value"])
        except (TypeError, ValueError):
            logger.warning(f"Ignoring malformed settings row {row['key']}")
    return overrides


def refresh_derived(app) -> None:
    """Recompute values that are derived from a setting (TIMEZONE → pytz object)."""
    import pytz

    raw = app.config.get("TIMEZONE_STR")
    if raw:
        try:
            app.config["TIMEZONE"] = pytz.timezone(str(raw))
        except Exception:  # noqa: BLE001 — an unknown zone keeps the previous one
            logger.warning(f"Unknown timezone in settings: {raw}")


def apply_overrides(app) -> int:
    """Apply the stored overrides onto ``app.config``; returns how many were applied."""
    applied = 0
    for key, value in load_overrides().items():
        if key in movable_keys():
            app.config[config_key(key)] = value
            applied += 1
    refresh_derived(app)
    return applied


def _audit(cursor, key: str, old_value: str | None, new_value: str | None, admin: str) -> None:
    if key in SECRET_KEYS:
        new_value = REDACTED if new_value else None
    cursor.execute(
        "INSERT INTO settings_audit (key, old_value, new_value, updated_by) VALUES (%s, %s, %s, %s)",
        (key, old_value, new_value, admin),
    )


def save_values(app, values: dict[str, Any], admin: str) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate and persist submitted values; returns (saved, errors by key)."""
    schema = {field["key"]: field for group in build_schema(app) for field in group["fields"]}
    saved: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for key, raw in values.items():
        field = schema.get(key)
        if field is None:
            errors[key] = "Unknown setting"
            continue
        value, error = coerce(key, raw, field["kind"])
        if error:
            errors[key] = error
            continue
        saved[key] = value

    if not saved:
        return {}, errors

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM settings WHERE key = ANY(%s)", (list(saved),))
        previous = {row["key"]: row["value"] for row in cursor.fetchall()}
        for key, value in saved.items():
            cursor.execute(
                "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (%s, %s, now(), %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
                "updated_at = now(), updated_by = EXCLUDED.updated_by",
                (key, json.dumps(value), admin),
            )
            _audit(cursor, key, previous.get(key), json.dumps(value), admin)

    for key, value in saved.items():
        app.config[config_key(key)] = value
    refresh_derived(app)
    return saved, errors


def reset_keys(app, keys: list[str], admin: str) -> int:
    """Drop overrides so the .env/built-in values apply again; returns the count."""
    wanted = [key for key in keys if key in movable_keys()]
    if not wanted:
        return 0
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM settings WHERE key = ANY(%s)", (wanted,))
        previous = {row["key"]: row["value"] for row in cursor.fetchall()}
        cursor.execute("DELETE FROM settings WHERE key = ANY(%s)", (wanted,))
        for key in wanted:
            _audit(cursor, key, previous.get(key), None, admin)
    for key in wanted:
        default = default_for(key)
        if default is not None:
            app.config[config_key(key)] = default
    refresh_derived(app)
    return len(wanted)


def prune_audit(days: int = AUDIT_RETENTION_DAYS) -> int:
    """Drop audit rows older than the retention window; returns the deleted count."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM settings_audit WHERE changed_at < now() - make_interval(days => %(days)s)",
            {"days": days},
        )
        return cursor.rowcount
