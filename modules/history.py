# modules/history.py
# User conversation-history search (ranked lexical search, PostgreSQL ILIKE).
"""Search the user's past conversation history with ranked lexical matches.

Used by two paths:
  * the ``history_search`` tool in the multimodal chat (app/tools.py);
  * the router ``[-HISTORY-]`` action, where the fast worker performs the
    search server-side and re-queues a reasoning task with the fragments.
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import re
from typing import Any

from app.database import get_db

logger = None  # replaced by logging.getLogger(__name__) at callers if needed

HISTORY_DEFAULT_LIMIT = 5
HISTORY_MAX_LIMIT = 10
HISTORY_FRAGMENT_CHARS = 300
HISTORY_MAX_WORDS = 6
HISTORY_OVERVIEW_EDGE_MESSAGES_PER_SESSION = 1

_STOP_WORDS = {
    "about",
    "all",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "but",
    "by",
    "can",
    "did",
    "do",
    "does",
    "for",
    "from",
    "had",
    "has",
    "have",
    "he",
    "her",
    "here",
    "him",
    "his",
    "how",
    "i",
    "in",
    "into",
    "is",
    "it",
    "its",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "she",
    "that",
    "the",
    "their",
    "them",
    "there",
    "they",
    "this",
    "to",
    "us",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "will",
    "with",
    "you",
    "your",
    "а",
    "без",
    "бы",
    "был",
    "была",
    "были",
    "было",
    "в",
    "во",
    "вот",
    "все",
    "всё",
    "для",
    "до",
    "за",
    "и",
    "из",
    "или",
    "к",
    "как",
    "когда",
    "кто",
    "ли",
    "между",
    "мы",
    "на",
    "над",
    "не",
    "ни",
    "но",
    "о",
    "об",
    "от",
    "по",
    "под",
    "при",
    "про",
    "с",
    "со",
    "так",
    "также",
    "то",
    "того",
    "тоже",
    "ты",
    "тобой",
    "у",
    "что",
    "чем",
    "чём",
    "через",
    "эта",
    "это",
    "я",
    "да",
    "если",
}


def _split_words(query: str) -> list[str]:
    """Extract language-neutral search terms from a natural-language query."""
    normalized = query.lower().translate(str.maketrans({"ё": "е"}))
    words = re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)
    terms = [word for word in words if len(word) > 1 and word not in _STOP_WORDS]
    return terms[:HISTORY_MAX_WORDS]


def search_history(
    user_id: str,
    query: str,
    limit: int = HISTORY_DEFAULT_LIMIT,
    exclude_message_id: int | None = None,
    exclude_session_id: str | None = None,
    max_message_chars: int = 20000,
) -> list[dict[str, Any]]:
    """Search messages in prior conversations for lexical topic matches.

    Matches are ranked by the number of normalized query terms found. Search
    handles Russian and English text in either interface profile; it does not
    translate queries between languages.

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

    russian_vector = "to_tsvector('russian', translate(m.search_text, 'ёЁ', 'ее'))"
    english_vector = "to_tsvector('english', m.search_text)"
    simple_vector = "to_tsvector('simple', translate(m.search_text, 'ёЁ', 'ее'))"
    vectors = (russian_vector, english_vector, simple_vector)
    term_matches = [
        "("
        + " OR ".join(
            f"{vector} @@ plainto_tsquery('{config}', %s)"
            for config, vector in zip(("russian", "english", "simple"), vectors, strict=True)
        )
        + ")"
        for _ in words
    ]
    rank_params = [word for word in words for _ in vectors]
    where_params = list(rank_params)
    exclusion = " AND m.id <> %s" if exclude_message_id is not None else ""
    params: list[Any] = [user_id]
    if exclude_message_id is not None:
        params.append(exclude_message_id)
    if exclude_session_id is not None:
        exclusion += " AND m.session_id <> %s"
        params.append(exclude_session_id)
    params.extend(rank_params)
    params.extend(where_params)
    params.append(limit)

    rank_expression = " + ".join(f"CASE WHEN {condition} THEN 1 ELSE 0 END" for condition in term_matches)
    matches_expression = " OR ".join(term_matches)

    sql = f"""
        WITH ranked_messages AS (
            SELECT m.id, m.session_id, cs.title AS session_title, m.role, m.content, m.timestamp,
                   left(translate(m.content || ' ' || coalesce(cs.title, ''), 'ёЁ', 'ее'), %s) AS search_text
            FROM messages m
            JOIN chat_sessions cs ON cs.id = m.session_id
            WHERE cs.user_id = %s AND m.role IN ('user','assistant'){exclusion}
        )
        SELECT m.id, m.session_id, m.session_title, m.role, m.content, m.timestamp,
               ({rank_expression}) AS match_rank
        FROM ranked_messages m
        WHERE ({matches_expression})
        ORDER BY match_rank DESC, m.timestamp DESC, m.id DESC
        LIMIT %s
    """

    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(sql, (max_message_chars, *params))
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


def get_history_overview(
    user_id: str,
    exclude_message_id: int | None = None,
    max_chars: int = 5000,
    exclude_session_id: str | None = None,
) -> str:
    """Build a compact overview from summaries or representative messages per session."""
    if not user_id:
        return ""

    params: list[Any] = [user_id]
    if exclude_session_id is not None:
        params.append(exclude_session_id)
    if exclude_message_id is not None:
        params.append(exclude_message_id)
    session_exclusion = " AND id <> %s" if exclude_session_id is not None else ""
    message_exclusion = " AND m.id <> %s" if exclude_message_id is not None else ""
    sql = f"""
        WITH owned_sessions AS (
            SELECT id, title, summary, updated_at
            FROM chat_sessions
            WHERE user_id = %s{session_exclusion}
        ), ranked_messages AS (
            SELECT m.id, m.session_id, cs.title AS session_title,
                   m.content, m.timestamp,
                   row_number() OVER (PARTITION BY cs.id ORDER BY m.timestamp, m.id) AS first_rank,
                   row_number() OVER (PARTITION BY cs.id ORDER BY m.timestamp DESC, m.id DESC) AS last_rank
            FROM messages m
            JOIN owned_sessions cs ON cs.id = m.session_id
            WHERE (cs.summary IS NULL OR cs.summary = '') AND m.role = 'user'{message_exclusion}
        )
        , overview_items AS (
            SELECT id AS session_id, title AS session_title, summary,
                   NULL::text AS content, updated_at AS timestamp, 0 AS sort_order
            FROM owned_sessions
            WHERE summary IS NOT NULL AND summary <> ''
            UNION ALL
            SELECT session_id, session_title, NULL::text AS summary, content, timestamp, 1 AS sort_order
            FROM ranked_messages
            WHERE first_rank <= %s OR last_rank <= %s
        )
        SELECT session_id, session_title, summary, content, timestamp
        FROM overview_items
        ORDER BY session_title, sort_order, timestamp
    """
    params.extend((HISTORY_OVERVIEW_EDGE_MESSAGES_PER_SESSION, HISTORY_OVERVIEW_EDGE_MESSAGES_PER_SESSION))
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()
    except Exception:
        return ""

    sessions: dict[str, dict[str, Any]] = {}
    for row in rows:
        msg = dict(row)
        text = _plain_text(msg.get("content"))
        summary = (msg.get("summary") or "").strip()
        if not text and not summary:
            continue
        session_id = str(msg.get("session_id"))
        session = sessions.setdefault(
            session_id,
            {
                "title": msg.get("session_title") or "?",
                "summary": msg.get("summary") or "",
                "messages": [],
                "timestamp": msg.get("timestamp"),
            },
        )
        if summary:
            session["summary"] = summary
        elif text:
            session["messages"].append(_trim_fragment(text))

    if not sessions:
        return ""

    per_session_chars = max(1, max_chars // len(sessions))
    sections = []
    for session in sessions.values():
        header = f'[{session["timestamp"] or "?"} - "{session["title"]}"]'
        body = session["summary"] or "\n".join(f"- {message}" for message in session["messages"])
        section = f"{header}\n{body}"
        if len(section) > per_session_chars:
            section = section[:per_session_chars]
        sections.append(section)
    return "\n\n".join(sections)[:max_chars]


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
    """Extract readable text from serialized chat content, omitting media payloads."""
    if content is None:
        return ""
    from app.db import _extract_text_content

    return _extract_text_content(str(content))


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
