"""Preserve monitoring history for rows that are about to be deleted.

Every helper takes an open cursor, so the archive insert joins the caller's
transaction: the activity is recorded if and only if the delete happens.

Account deletion is the one exemption — it must not leave any trace, so it
never archives and erases the archive instead.
"""

from app.database import get_db
from app.monitoring.timez import app_timezone

ARCHIVE_KINDS = ("session", "message", "task", "task_step", "task_plan", "document")
ARCHIVE_RETENTION_DAYS = 45

# The token expressions are shared with the live aggregation so the archive and
# the live rows always mean the same thing (parity with the Users tab).
TASK_STEP_OUT_SQL = (
    "COALESCE((ts.extra->'usage_total'->>'prompt_tokens')::bigint,"
    " (ts.extra->'usage'->>'prompt_tokens')::bigint, 0)"
    " + COALESCE((ts.extra->'checkpoint_usage_total'->>'prompt_tokens')::bigint,"
    " (ts.extra->'checkpoint_usage'->>'prompt_tokens')::bigint, 0)"
)
TASK_STEP_IN_SQL = (
    "COALESCE((ts.extra->'usage_total'->>'completion_tokens')::bigint,"
    " (ts.extra->'usage'->>'completion_tokens')::bigint, 0)"
    " + COALESCE((ts.extra->'checkpoint_usage_total'->>'completion_tokens')::bigint,"
    " (ts.extra->'checkpoint_usage'->>'completion_tokens')::bigint, 0)"
)
PLAN_OUT_SQL = "COALESCE((u.usage->>'prompt_tokens')::bigint, 0)"
PLAN_IN_SQL = "COALESCE((u.usage->>'completion_tokens')::bigint, 0)"


def archive_session(cursor, session_id: str) -> int:
    """Archive the session row itself."""
    cursor.execute(
        """
        INSERT INTO monitoring_archive (user_id, kind, occurred_at)
        SELECT user_id, 'session', COALESCE(created_at AT TIME ZONE %(tz)s, now())
        FROM chat_sessions WHERE id = %(sid)s
        """,
        {"tz": app_timezone(), "sid": session_id},
    )
    return cursor.rowcount


def archive_messages(cursor, session_id: str) -> int:
    """Archive every message of a session, with its tokens and attachment flag."""
    cursor.execute(
        """
        INSERT INTO monitoring_archive (user_id, kind, occurred_at, out_tokens, in_tokens, file_count)
        SELECT cs.user_id, 'message', COALESCE(m.timestamp AT TIME ZONE %(tz)s, now()),
               COALESCE(m.prompt_tokens, 0), COALESCE(m.completion_tokens, 0),
               CASE WHEN m.file_path IS NOT NULL AND m.file_path <> '' THEN 1 ELSE 0 END
        FROM messages m JOIN chat_sessions cs ON cs.id = m.session_id
        WHERE m.session_id = %(sid)s
        """,
        {"tz": app_timezone(), "sid": session_id},
    )
    return cursor.rowcount


def archive_documents(cursor, user_id: str, doc_ids: list[str]) -> int:
    """Archive the given documents of one user; an empty list is a no-op."""
    if not doc_ids:
        return 0
    cursor.execute(
        """
        INSERT INTO monitoring_archive (user_id, kind, occurred_at)
        SELECT user_id, 'document', COALESCE(uploaded_at AT TIME ZONE %(tz)s, now())
        FROM documents WHERE user_id = %(uid)s AND id = ANY(%(ids)s)
        """,
        {"tz": app_timezone(), "uid": user_id, "ids": list(doc_ids)},
    )
    return cursor.rowcount


def archive_task(cursor, task_id: str) -> int:
    """Archive the task, its step tokens and its planner/revision tokens."""
    cursor.execute(
        """
        INSERT INTO monitoring_archive (user_id, kind, occurred_at)
        SELECT user_id, 'task', created_at FROM tasks WHERE id = %(tid)s
        """,
        {"tid": task_id},
    )
    inserted = cursor.rowcount

    cursor.execute(
        f"""
        INSERT INTO monitoring_archive (user_id, kind, occurred_at, out_tokens, in_tokens)
        SELECT t.user_id, 'task_step', ts.finished_at, {TASK_STEP_OUT_SQL}, {TASK_STEP_IN_SQL}
        FROM task_steps ts JOIN tasks t ON t.id = ts.task_id
        WHERE ts.task_id = %(tid)s AND ts.finished_at IS NOT NULL
        """,
        {"tid": task_id},
    )
    inserted += cursor.rowcount

    cursor.execute(
        f"""
        INSERT INTO monitoring_archive (user_id, kind, occurred_at, out_tokens, in_tokens)
        SELECT t.user_id, 'task_plan', t.created_at, {PLAN_OUT_SQL}, {PLAN_IN_SQL}
        FROM tasks t
        CROSS JOIN LATERAL (
            SELECT t.step_plan->'plan_meta'->'usage' AS usage
            UNION ALL
            SELECT rev.value->'usage'
            FROM jsonb_each(COALESCE(t.step_plan->'revision_meta', '{{}}'::jsonb)) AS rev
        ) u
        WHERE t.id = %(tid)s
        """,
        {"tid": task_id},
    )
    inserted += cursor.rowcount
    return inserted


def prune_archive(days: int = ARCHIVE_RETENTION_DAYS) -> int:
    """Drop archive rows older than the retention window; returns the deleted count."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM monitoring_archive WHERE occurred_at < now() - make_interval(days => %(days)s)",
            {"days": days},
        )
        return cursor.rowcount
