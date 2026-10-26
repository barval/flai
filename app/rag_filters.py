# app/rag_filters.py
"""Resolve a user-facing search scope into the documents retrieval may see.

A scope is expressed with cheap, stable facts about the documents — folder,
upload date, file type, an explicit list — and resolved in PostgreSQL. The
retrieval layer only ever receives the resulting document ids, so no payload
change and no re-indexing is needed, and moving a document between folders
takes effect immediately.

An empty result means "the filter matched nothing" and must stay empty: falling
back to the whole corpus would answer from documents the user excluded.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from app.database import get_db

logger = logging.getLogger(__name__)


def _as_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        logger.warning(f"Ignoring unparsable scope date: {value!r}")
        return None


def resolve_scope(user_id: str, scope: dict | None) -> list[str] | None:
    """Return the document ids a scope allows, or ``None`` when no scope is set.

    Supported keys: ``docs`` (ids), ``folder`` (id; empty string = unfiled),
    ``from`` / ``to`` (upload dates, inclusive), ``ext`` (extensions without a
    dot). Conditions combine with AND.
    """
    if not scope:
        return None

    docs = [str(value) for value in (scope.get("docs") or []) if value]
    folder = scope.get("folder")
    date_from = _as_date(scope.get("from"))
    date_to = _as_date(scope.get("to"))
    exts = [str(value).lower().lstrip(".") for value in (scope.get("ext") or []) if value]

    if not (docs or folder is not None or date_from or date_to or exts):
        return None

    sql = "SELECT id FROM documents WHERE user_id = %s"
    params: list = [user_id]
    if docs:
        sql += " AND id = ANY(%s)"
        params.append(docs)
    if folder is not None:
        if str(folder) == "":
            sql += " AND folder_id IS NULL"
        else:
            sql += " AND folder_id = %s"
            params.append(str(folder))
    if date_from:
        sql += " AND uploaded_at >= %s"
        params.append(date_from)
    if date_to:
        # ``to`` is inclusive: everything uploaded before the next day.
        sql += " AND uploaded_at < %s"
        params.append(date_to + timedelta(days=1))
    if exts:
        # Stored extensions carry the dot (".pdf"); the scope does not.
        sql += " AND lower(ltrim(file_ext, '.')) = ANY(%s)"
        params.append(exts)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return [str(row["id"]) for row in cursor.fetchall()]
