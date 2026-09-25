# modules/history.py
# User conversation-history search (lexical, PostgreSQL ILIKE).
"""Search the user's past conversation history by full-text word matching.

Used by two paths:
  * the ``search_history`` tool in the multimodal chat (app/tools.py);
  * the router ``[-HISTORY-]`` action, where the fast worker performs the
    search server-side and re-queues a reasoning task with the fragments.
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

from typing import Any

from app.database import get_db

logger = None  # replaced by logging.getLogger(__name__) at callers if needed

HISTORY_DEFAULT_LIMIT = 5
HISTORY_MAX_LIMIT = 10
HISTORY_FRAGMENT_CHARS = 300
HISTORY_MAX_WORDS = 6


def _split_words(query: str) -> list[str]:
    """Split a query into non-empty words (max HISTORY_MAX_WORDS)."""
    words = [w for w in query.split() if w][:HISTORY_MAX_WORDS]
    return words


def search_history(user_id: str, query: str, limit: int = HISTORY_DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Search user messages across all their sessions.

    Each word of the query must appear somewhere in the message content
    (AND semantics via repeated ILIKE). Results are returned newest-first.

    Args:
        user_id: The user login (chat_sessions.user_id stores the login).
        query: Search words.
        limit: Max number of fragments (clamped to HISTORY_MAX_LIMIT).

    Returns:
        List of dicts: id, session_id, session_title, role, text, timestamp.
    """
    words = _split_words(query)
    if not words or not user_id:
        return []
    limit = max(1, min(int(limit), HISTORY_MAX_LIMIT))

    params: list[Any] = [user_id]
    conditions = ["m.role IN ('user','assistant')"]
    for w in words:
        params.append(f"%{w}%")
        conditions.append("m.content ILIKE %s")
    params.append(limit)

    sql = f"""
        SELECT m.id, m.session_id, cs.title AS session_title, m.role, m.content, m.timestamp
        FROM messages m
        JOIN chat_sessions cs ON cs.id = m.session_id
        WHERE cs.user_id = %s AND {" AND ".join(conditions)}
        ORDER BY m.timestamp DESC, m.id DESC
        LIMIT %s
    """

    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(sql, tuple(params))
            rows = c.fetchall()
        found: list[dict[str, Any]] = []
        for row in rows:
            msg = dict(row)
            text = _plain_text(msg.get("content"))
            if not text:
                continue
            found.append(
                {
                    "id": msg.get("id"),
                    "session_id": msg.get("session_id"),
                    "session_title": msg.get("session_title"),
                    "role": msg.get("role"),
                    "text": _trim_fragment(text),
                    "timestamp": msg.get("timestamp"),
                }
            )
        return found
    except Exception:
        return []


def format_history_context(fragments: list[dict[str, Any]], max_chars: int = 0) -> str:
    """Format history-search fragments into a compact context string.

    Each fragment is rendered as ``[date - "session title" (role)]: text``.
    When ``max_chars`` is positive, trailing fragments are dropped once the
    total exceeds it (fragment texts are already capped at
    HISTORY_FRAGMENT_CHARS).
    """
    if not fragments:
        return ""
    lines = []
    for i, f in enumerate(fragments, 1):
        date = _format_date(f.get("timestamp"))
        title = (f.get("session_title") or "?").strip()
        role = f.get("role")
        role_label = "User" if role == "user" else "Assistant"
        text = f.get("text", "")
        lines.append(f'[{i}. {date} - "{title}" ({role_label})]:\n{text}')

    joined = "\n\n".join(lines)
    if max_chars > 0 and len(joined) > max_chars:
        # Drop trailing fragments until the total fits the budget.
        lines, total = [], 0
        for i, f in enumerate(fragments, 1):
            date = _format_date(f.get("timestamp"))
            title = (f.get("session_title") or "?").strip()
            role_label = "User" if f.get("role") == "user" else "Assistant"
            line = f'[{i}. {date} - "{title}" ({role_label})]:\n{f.get("text", "")}'
            next_total = total + len(line) + (2 if i > 1 else 0)
            if next_total > max_chars:
                break
            lines.append(line)
            total = next_total
        return "\n\n".join(lines)
    return joined


def _plain_text(content: Any) -> str:
    """Extract readable text from a message content (may be JSON for media)."""
    if content is None:
        return ""
    text = content if isinstance(content, str) else str(content)
    text = text.strip()
    if text.startswith("["):
        return text
    return text


def _trim_fragment(text: str) -> str:
    if len(text) <= HISTORY_FRAGMENT_CHARS:
        return text
    return text[: HISTORY_FRAGMENT_CHARS - 3].rstrip() + "..."


def _format_date(value: Any) -> str:
    if value is None:
        return "?"
    if hasattr(value, "strftime"):
        return str(value.strftime("%d.%m.%Y"))
    s = str(value)[:10]
    try:
        d, m, y = s.split("-")
        return f"{d}.{m}.{y}"
    except Exception:
        return s
