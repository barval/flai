"""Whether a skill is enabled, kept in the database and not in the file.

A flag in the frontmatter would let an editor change behaviour without the panel
knowing, and a disabled skill must leave both the catalogue and the set of names
the request kernel is allowed to return. One place answers that question for
every reader; an absent row means enabled, so an upgrade switches nothing off.
"""

from __future__ import annotations

import logging

from app.database import get_db

logger = logging.getLogger(__name__)


def disabled_names() -> set[str]:
    """Names that were switched off; empty when the table cannot be read."""
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT skill_name FROM skill_settings WHERE enabled = FALSE")
            return {row["skill_name"] for row in c.fetchall()}
    except Exception as e:  # noqa: BLE001 - a broken table must not hide the catalogue
        logger.warning(f"skill_settings could not be read: {e}")
        return set()


def enabled_map() -> dict[str, bool]:
    """Every stored flag, for the admin panel's list."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT skill_name, enabled FROM skill_settings")
        return {row["skill_name"]: bool(row["enabled"]) for row in c.fetchall()}


def set_enabled(name: str, enabled: bool) -> None:
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO skill_settings (skill_name, enabled)
            VALUES (%s, %s)
            ON CONFLICT (skill_name) DO UPDATE
                SET enabled = EXCLUDED.enabled, updated_at = CURRENT_TIMESTAMP
            """,
            (name, enabled),
        )
