# tests/_monitoring_fixtures.py
"""Shared, pinned monitoring fixture data."""

from datetime import UTC, datetime, timedelta

from app.database import get_db
from app.userdb import create_user

# The data must land inside every queried window, so the timestamps follow the
# wall clock instead of being fixed (the endpoint has no injectable clock).
NOW = datetime.now(UTC).replace(microsecond=0)
LOCAL_TS = (NOW - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")  # naive local; tests use UTC


def seed_monitoring_data() -> None:
    """One session, two messages (one attachment each), one task step, one plan,
    two documents (one of them the chat attachment) — the numbers the tests pin."""
    create_user("u1", "pw", "U1")
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO chat_sessions (id, user_id, title, created_at) VALUES ('s1', 'u1', 't', %s)",
            (LOCAL_TS,),
        )
        cursor.execute(
            "INSERT INTO messages (session_id, role, content, timestamp, prompt_tokens, completion_tokens, file_path) "
            "VALUES ('s1', 'user', 'hi', %s, 1500, 500, 'u1/a.png')",
            (LOCAL_TS,),
        )
        cursor.execute(
            "INSERT INTO messages (session_id, role, content, timestamp, file_path) "
            "VALUES ('s1', 'user', 'doc', %s, 'u1/b.pdf')",
            (LOCAL_TS,),
        )
        cursor.execute(
            "INSERT INTO documents (id, user_id, filename, file_ext, uploaded_at) "
            "VALUES ('d1', 'u1', 'report.pdf', '.pdf', %s), ('d2', 'u1', 'b.pdf', '.pdf', %s)",
            (LOCAL_TS, LOCAL_TS),
        )
        cursor.execute(
            "INSERT INTO tasks (id, user_id, status, step_plan, created_at) "
            "VALUES ('00000000-0000-0000-0000-000000000001', 'u1', 'passed', %s, %s)",
            ('{"plan_meta": {"usage": {"prompt_tokens": 300, "completion_tokens": 100}}}', NOW),
        )
        cursor.execute(
            "INSERT INTO task_steps (id, task_id, seq, kind, status, extra, finished_at) "
            "VALUES ('00000000-0000-0000-0000-000000000002', "
            "'00000000-0000-0000-0000-000000000001', 1, 'direct', 'passed', %s, %s)",
            ('{"usage": {"prompt_tokens": 700, "completion_tokens": 200}}', NOW),
        )
        cursor.execute(
            "INSERT INTO user_storage (user_id, used_bytes) VALUES ('u1', 2048) "
            "ON CONFLICT (user_id) DO UPDATE SET used_bytes = EXCLUDED.used_bytes"
        )
