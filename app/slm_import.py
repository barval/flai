# app/slm_import.py
"""Incremental import of messages into SuperLocalMemory (SLM).

Tracks import progress per user via slm_import_progress table,
so repeated runs only process new messages since the last checkpoint.
"""

import logging
import time
from typing import Any

from app.database import get_db

logger = logging.getLogger(__name__)

BATCH_SIZE = 50
INTER_BATCH_DELAY = 1.0


def _get_last_message_id(user_id: str) -> int:
    """Get the last imported message ID for a user. Returns 0 if none."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT last_message_id FROM slm_import_progress WHERE user_id = %s", (user_id,))
        row = c.fetchone()
        return row["last_message_id"] if row else 0


def _update_progress(user_id: str, last_message_id: int, total_imported: int) -> None:
    """Update import checkpoint for a user."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """INSERT INTO slm_import_progress (user_id, last_message_id, total_imported, updated_at)
               VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
               ON CONFLICT (user_id) DO UPDATE SET
                   last_message_id = EXCLUDED.last_message_id,
                   total_imported = slm_import_progress.total_imported + %s,
                   updated_at = CURRENT_TIMESTAMP""",
            (user_id, last_message_id, total_imported, total_imported),
        )


def _get_users_with_messages() -> list[str]:
    """Get all user IDs that have chat sessions with messages."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """SELECT DISTINCT cs.user_id
               FROM chat_sessions cs
               JOIN messages m ON m.session_id = cs.id
               WHERE m.role IN ('user', 'assistant')
               ORDER BY cs.user_id"""
        )
        return [row["user_id"] for row in c.fetchall()]


def _extract_clean_text(content: str, model_name: str | None = None) -> str | None:
    """Extract clean text from message content, returning None if message should be skipped."""
    if not content or not content.strip():
        return None
    text = content.strip()
    # Skip system messages
    if model_name == "system":
        return None
    # Unwrap JSON content
    if text.startswith("[") or text.startswith("{"):
        from app.db import _extract_text_content

        extracted = _extract_text_content(text)
        if not extracted or extracted == text:
            return None
        text = extracted
    # Skip very short messages
    if len(text) < 10:
        return None
    return text


def import_user_messages(
    slm: Any,
    user_id: str,
    since_message_id: int = 0,
    dry_run: bool = False,
    batch_size: int = BATCH_SIZE,
    delay: float = INTER_BATCH_DELAY,
) -> tuple[int, int, int]:
    """Import unprocessed messages for a user since the given checkpoint.

    Args:
        slm: SlmModule instance.
        user_id: User login to import messages for.
        since_message_id: Only import messages with id > this value (0 = all).
        dry_run: If True, count without saving.
        batch_size: Messages per batch.
        delay: Seconds to wait between batches.

    Returns:
        (imported_count, skipped_count, last_message_id)
    """
    imported = 0
    skipped = 0
    last_id = 0

    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """SELECT m.id, m.session_id, m.role, m.content, m.model_name, cs.user_id
               FROM messages m
               JOIN chat_sessions cs ON m.session_id = cs.id
               WHERE m.role = 'user'
               AND cs.user_id = %s
               AND m.id > %s
               ORDER BY m.id ASC""",
            (user_id, since_message_id),
        )
        rows = c.fetchall()

    if not rows:
        return (0, 0, since_message_id)

    for i, row in enumerate(rows):
        msg_id = row["id"]
        role = row["role"]
        content = row["content"]
        session_id = row["session_id"]
        model_name = row.get("model_name")

        text = _extract_clean_text(content, model_name)
        if text is None:
            skipped += 1
            continue

        last_id = msg_id

        if dry_run:
            continue

        success = slm.remember(
            text,
            metadata={"session_id": session_id, "message_id": msg_id, "role": role, "source": "auto_import"},
            profile=user_id,
        )
        if success:
            imported += 1
        else:
            skipped += 1
            logger.warning("SLM import failed for user=%s msg_id=%s", user_id, msg_id)

        # Update checkpoint every batch_size messages
        if (i + 1) % batch_size == 0:
            _update_progress(user_id, last_id, imported)
            if delay > 0:
                time.sleep(delay)

    # Final checkpoint update
    if not dry_run and last_id > since_message_id:
        _update_progress(user_id, last_id, imported)

    return (imported, skipped, last_id)


def import_all_users(
    slm: Any,
    dry_run: bool = False,
    batch_size: int = BATCH_SIZE,
    delay: float = INTER_BATCH_DELAY,
) -> dict[str, tuple[int, int, int]]:
    """Import unprocessed messages for all users.

    Returns dict mapping user_id -> (imported, skipped, last_message_id).
    """
    results: dict[str, tuple[int, int, int]] = {}
    for user_id in _get_users_with_messages():
        since = _get_last_message_id(user_id)
        imported, skipped, last_id = import_user_messages(
            slm, user_id, since_message_id=since, dry_run=dry_run, batch_size=batch_size, delay=delay
        )
        results[user_id] = (imported, skipped, last_id)
        logger.info(
            "SLM import for %s: %d imported, %d skipped, up to msg %d (since checkpoint %d)",
            user_id,
            imported,
            skipped,
            last_id,
            since,
        )
    return results
