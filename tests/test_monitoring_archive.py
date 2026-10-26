# tests/test_monitoring_archive.py
"""The monitoring archive must capture activity before its rows are deleted."""

from datetime import UTC, datetime

import pytest
import pytz

from app.database import get_db
from app.db import delete_document, delete_session_and_messages
from app.monitoring.archive import prune_archive
from app.tasks.store import delete_task
from app.userdb import create_user

pytestmark = pytest.mark.requires_db

LOCAL_TS = "2020-10-23 12:05:00"
TASK_AT = datetime(2020, 10, 23, 12, 30, tzinfo=UTC)


def _seed_session(login: str = "u1", session_id: str = "s1") -> str:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO chat_sessions (id, user_id, title, created_at) VALUES (%s, %s, 't', %s)",
            (session_id, login, LOCAL_TS),
        )
        cursor.execute(
            "INSERT INTO messages (session_id, role, content, timestamp, prompt_tokens, completion_tokens, file_path) "
            "VALUES (%s, 'user', 'hi', %s, 1500, 500, 'u1/a.png')",
            (session_id, LOCAL_TS),
        )
        cursor.execute(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (%s, 'assistant', 'yo', %s)",
            (session_id, LOCAL_TS),
        )
    return session_id


def _archive_rows(login: str = "u1") -> list[dict]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT kind, out_tokens, in_tokens, file_count FROM monitoring_archive "
            "WHERE user_id = %s ORDER BY kind",
            (login,),
        )
        return cursor.fetchall()


def test_deleting_a_session_archives_its_activity(test_app):
    with test_app.app_context():
        test_app.config["TIMEZONE"] = pytz.UTC
        create_user("u1", "pw", "U1")
        session_id = _seed_session()

        assert delete_session_and_messages(session_id, "u1", upload_folder=None) is True

        rows = _archive_rows()

    assert [row["kind"] for row in rows] == ["message", "message", "session"]
    assert sum(row["out_tokens"] for row in rows) == 1500
    assert sum(row["in_tokens"] for row in rows) == 500
    assert sum(row["file_count"] for row in rows) == 1


def test_account_deletion_does_not_archive(test_app):
    with test_app.app_context():
        test_app.config["TIMEZONE"] = pytz.UTC
        create_user("u1", "pw", "U1")
        session_id = _seed_session()

        delete_session_and_messages(session_id, "u1", upload_folder=None, archive=False)

        assert _archive_rows() == []


def test_deleting_a_document_archives_it(test_app):
    with test_app.app_context():
        test_app.config["TIMEZONE"] = pytz.UTC
        create_user("u1", "pw", "U1")
        with get_db() as conn:
            conn.cursor().execute(
                "INSERT INTO documents (id, user_id, filename, file_ext, uploaded_at) "
                "VALUES ('d1', 'u1', 'report.pdf', '.pdf', %s)",
                (LOCAL_TS,),
            )

        delete_document("d1", "u1")

        rows = _archive_rows()

    assert [row["kind"] for row in rows] == ["document"]


def test_deleting_a_task_archives_counts_tokens_and_plan(test_app):
    with test_app.app_context():
        test_app.config["TIMEZONE"] = pytz.UTC
        create_user("u1", "pw", "U1")
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO tasks (id, user_id, status, step_plan, created_at) "
                "VALUES ('00000000-0000-0000-0000-000000000001', 'u1', 'passed', %s, %s)",
                ('{"plan_meta": {"usage": {"prompt_tokens": 300, "completion_tokens": 100}}}', TASK_AT),
            )
            cursor.execute(
                "INSERT INTO task_steps (id, task_id, seq, kind, status, extra, finished_at) "
                "VALUES ('00000000-0000-0000-0000-000000000002', "
                "'00000000-0000-0000-0000-000000000001', 1, 'direct', 'passed', %s, %s)",
                ('{"usage": {"prompt_tokens": 700, "completion_tokens": 200}}', TASK_AT),
            )

        delete_task("00000000-0000-0000-0000-000000000001")

        rows = _archive_rows()

    assert [row["kind"] for row in rows] == ["task", "task_plan", "task_step"]
    assert sum(row["out_tokens"] for row in rows) == 1000
    assert sum(row["in_tokens"] for row in rows) == 300


def test_prune_archive_drops_only_old_rows(test_app):
    with test_app.app_context():
        with get_db() as conn:
            conn.cursor().execute(
                "INSERT INTO monitoring_archive (user_id, kind, occurred_at) VALUES "
                "('u1', 'message', now() - interval '60 days'), "
                "('u1', 'message', now() - interval '10 days')"
            )

        assert prune_archive() == 1
        assert len(_archive_rows()) == 1


def test_deleting_a_session_with_a_null_timestamp_still_works(test_app):
    """A legacy row without a timestamp must not make the deletion fail."""
    with test_app.app_context():
        test_app.config["TIMEZONE"] = pytz.UTC
        create_user("u1", "pw", "U1")
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO chat_sessions (id, user_id, title, created_at) VALUES ('s1', 'u1', 't', NULL)"
            )
            cursor.execute(
                "INSERT INTO messages (session_id, role, content, timestamp) VALUES ('s1', 'user', 'hi', NULL)"
            )

        assert delete_session_and_messages("s1", "u1", upload_folder=None) is True

        assert len(_archive_rows()) == 2


def test_clear_history_archives_the_messages(client, test_app):
    """Clearing a chat deletes its messages; the archive must keep them."""
    import uuid

    session_id = str(uuid.uuid4())
    with test_app.app_context():
        test_app.config["TIMEZONE"] = pytz.UTC
        create_user("u1", "pw", "U1")
        _seed_session(session_id=session_id)

    with client.session_transaction() as flask_session:
        flask_session["login"] = "u1"
        flask_session["current_session"] = session_id

    response = client.post("/api/clear_history")
    assert response.status_code == 200, response.data

    with test_app.app_context():
        rows = _archive_rows()
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS n FROM messages WHERE session_id = %s", (session_id,))
            assert cursor.fetchone()["n"] == 0

    assert [row["kind"] for row in rows] == ["message", "message"]
    assert sum(row["out_tokens"] for row in rows) == 1500
