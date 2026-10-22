# tests/test_tasks_db.py
"""Tests for the Tasks subsystem DB schema and store helpers.

Marked ``requires_db``: they exercise real PostgreSQL tables (tasks,
task_steps) created by ``init_db()``. When no reachable PostgreSQL exists the
fixture reports an external error instead of failing the suite; the
importability test below always runs (no DB needed for import).
"""

import os
from datetime import UTC

import pytest

pytestmark = pytest.mark.requires_db

_db_available = (
    bool(os.environ.get("DATABASE_URL")) and os.environ["DATABASE_URL"] != "postgresql://localhost:5432/flai_test"
)


@pytest.fixture
def pg_conn():
    """Skip unless a real PostgreSQL is reachable; else return a connection."""
    if not _db_available:
        pytest.skip("No reachable PostgreSQL for tasks tests")
    try:
        from app.database import get_db_connection

        conn = get_db_connection()
    except Exception as exc:
        pytest.skip(f"PostgreSQL unreachable: {exc}")
    yield conn
    conn.close()


@pytest.fixture
def fresh_db(pg_conn):
    """Create tasks tables via init_db() and clean them before/after."""
    from app.database import init_db

    init_db()
    _cleanup(pg_conn)
    yield pg_conn
    _cleanup(pg_conn)
    pg_conn.close()


def _cleanup(conn, task_id=None):
    with conn.cursor() as cur:
        if task_id:
            cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
        else:
            cur.execute("DELETE FROM tasks")
    conn.commit()


def test_create_task_and_steps(test_app, fresh_db):
    """create_task + add_step + update_step round-trip on real PostgreSQL."""
    with test_app.app_context():
        from app.tasks.store import add_step, create_task, get_task, update_step

        db = fresh_db
        cur = db.cursor()
        task_id = create_task(user_id="tester", task_class=3, title="t", lang="ru", response_style="neutral")
        try:
            t = get_task(task_id)
            assert t["status"] == "queued" and t["task_class"] == 3
            step_id = add_step(task_id=task_id, seq=1, kind="direct", goal="g", tool="llm", checkpoint_criteria="c")
            s = update_step(step_id, status="passed", result="ok", exec_seconds=1.5)
            assert s["status"] == "passed" and s["exec_seconds"] == 1.5
            cur.execute("SELECT COUNT(*) AS cnt FROM task_steps WHERE task_id = %s", (task_id,))
            assert cur.fetchone()["cnt"] == 1
        finally:
            _cleanup(db, task_id)


def test_create_task_defaults(test_app, fresh_db):
    """New task row carries the schema defaults for fields create_task omits."""
    with test_app.app_context():
        from app.tasks.store import create_task, get_task

        task_id = create_task(user_id="tester", task_class=5, title="defaults", lang="en", response_style="friendly")
        try:
            t = get_task(task_id)
            assert t["step_plan"] == {}
            assert t["next_step_index"] == 0
            assert t["accumulated_exec_seconds"] == 0
            assert t["started_at"] is None and t["completed_at"] is None
            assert t["abort_reason"] is None
            assert t["lang"] == "en" and t["response_style"] == "friendly"
        finally:
            _cleanup(fresh_db, task_id)


def test_add_step_defaults(test_app, fresh_db):
    """add_step defaults (pending status, empty strings) land in the row."""
    with test_app.app_context():
        from app.tasks.store import add_step, create_task, get_step

        task_id = create_task(user_id="tester", task_class=3, title="t", lang="ru", response_style="neutral")
        try:
            step_id = add_step(task_id=task_id, seq=2, kind="llm", goal="g")
            s = get_step(step_id)
            assert s["status"] == "pending"
            assert s["tool"] == "" and s["checkpoint_criteria"] == ""
            assert s["attempts"] == 0 and s["parent_step_id"] is None
            assert s["task_id"] == task_id and s["seq"] == 2
        finally:
            _cleanup(fresh_db, task_id)


def test_update_step_datetime_and_iso_strings(test_app, fresh_db):
    """started_at/finished_at accept datetime objects AND ISO strings."""
    from datetime import datetime

    with test_app.app_context():
        from app.tasks.store import add_step, create_task, update_step

        task_id = create_task(user_id="tester", task_class=3, title="t", lang="ru", response_style="neutral")
        try:
            step_id = add_step(task_id=task_id, seq=1, kind="direct", goal="g")
            dt = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)
            s = update_step(step_id, started_at=dt, finished_at="2026-10-11T13:00:00+00:00")
            assert s["started_at"].year == 2026 and s["finished_at"].hour == 13
        finally:
            _cleanup(fresh_db, task_id)


def test_update_step_rejects_disallowed_keys(test_app, fresh_db):
    """update_step raises ValueError when no allowed key is present."""
    with test_app.app_context():
        from app.tasks.store import add_step, create_task, update_step

        task_id = create_task(user_id="tester", task_class=3, title="t", lang="ru", response_style="neutral")
        try:
            step_id = add_step(task_id=task_id, seq=1, kind="direct", goal="g")
            with pytest.raises(ValueError):
                update_step(step_id, goal="nope")
        finally:
            _cleanup(fresh_db, task_id)


def test_update_step_extra_jsonb(test_app, fresh_db):
    """extra (JSONB) accepts a dict and round-trips as a dict."""
    with test_app.app_context():
        from app.tasks.store import add_step, create_task, update_step

        task_id = create_task(user_id="tester", task_class=3, title="t", lang="ru", response_style="neutral")
        try:
            step_id = add_step(task_id=task_id, seq=1, kind="direct", goal="g")
            s = update_step(step_id, extra={"k": 1, "nested": {"v": "x"}})
            assert s["extra"] == {"k": 1, "nested": {"v": "x"}}
        finally:
            _cleanup(fresh_db, task_id)


def test_update_task_fields(test_app, fresh_db):
    """update_task updates allowed task columns and returns the row."""
    with test_app.app_context():
        from app.tasks.store import create_task, get_task, update_task

        task_id = create_task(user_id="tester", task_class=3, title="t", lang="ru", response_style="neutral")
        try:
            plan = [{"seq": 1, "kind": "direct", "goal": "g"}]
            t = update_task(
                task_id,
                status="running",
                step_plan=plan,
                next_step_index=1,
                accumulated_exec_seconds=2.5,
                started_at="2026-10-11T12:00:00+00:00",
            )
            assert t["status"] == "running" and t["step_plan"] == plan
            assert t["next_step_index"] == 1 and t["accumulated_exec_seconds"] == 2.5
            t2 = get_task(task_id)
            assert t2["updated_at"] >= t2["created_at"]
        finally:
            _cleanup(fresh_db, task_id)


def test_update_task_rejects_disallowed_keys(test_app, fresh_db):
    """update_task raises ValueError when no allowed key is present."""
    with test_app.app_context():
        from app.tasks.store import create_task, update_task

        task_id = create_task(user_id="tester", task_class=3, title="t", lang="ru", response_style="neutral")
        try:
            with pytest.raises(ValueError):
                update_task(task_id, user_id="nope")
        finally:
            _cleanup(fresh_db, task_id)


def test_delete_task_cascades_steps(test_app, fresh_db):
    """task_steps rows are removed together with the parent task (FK cascade)."""
    with test_app.app_context():
        from app.tasks.store import add_step, create_task

        task_id = create_task(user_id="tester", task_class=3, title="t", lang="ru", response_style="neutral")
        try:
            add_step(task_id=task_id, seq=1, kind="direct", goal="g")
            cur = fresh_db.cursor()
            cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
            cur.execute("SELECT COUNT(*) AS cnt FROM task_steps WHERE task_id = %s", (task_id,))
            assert cur.fetchone()["cnt"] == 0
        finally:
            _cleanup(fresh_db, task_id)


def test_delete_task_removes_messages_written_under_its_id(test_app, fresh_db):
    """A failed step of a FLAI Task leaves a chat message keyed by the task id.

    Nothing reads those rows (the task view renders steps), so deleting the task
    must delete them too — 15 orphans were found in a live database.
    """
    with test_app.app_context():
        from app.db import save_message
        from app.tasks.store import create_task, delete_task

        task_id = create_task(user_id="tester", task_class=3, title="t", lang="ru", response_style="neutral")
        try:
            save_message(task_id, "assistant", "⚠️ step failed", model_name="system", model_type="system")
            cur = fresh_db.cursor()
            cur.execute("SELECT COUNT(*) AS cnt FROM messages WHERE session_id = %s", (task_id,))
            assert cur.fetchone()["cnt"] == 1

            delete_task(task_id)

            cur.execute("SELECT COUNT(*) AS cnt FROM messages WHERE session_id = %s", (task_id,))
            assert cur.fetchone()["cnt"] == 0
        finally:
            _cleanup(fresh_db, task_id)


def test_store_module_importable(test_app):
    """The store module imports standalone (no DB needed for import)."""
    import importlib

    mod = importlib.import_module("app.tasks.store")
    assert hasattr(mod, "create_task")
    assert hasattr(mod, "add_step")
    assert hasattr(mod, "get_task")
    assert hasattr(mod, "update_step")
    assert hasattr(mod, "update_task")


def test_delete_pending_steps_after_keeps_terminal_rows(test_app, fresh_db):
    """A rewritten plan tail must not leave stale pending rows behind: the
    executor reuses a row found by seq and would inherit the old step's goal."""
    with test_app.app_context():
        from app.tasks.executor import get_task_steps
        from app.tasks.store import add_step, create_task, delete_pending_steps_after, get_task, update_step

        db = fresh_db
        task_id = create_task(user_id="tester", task_class=3, title="t", lang="ru", response_style="neutral")
        try:
            first = add_step(task_id=task_id, seq=1, kind="direct", goal="keep me", tool="llm")
            second = add_step(task_id=task_id, seq=2, kind="direct", goal="drop me", tool="llm")
            third = add_step(task_id=task_id, seq=3, kind="direct", goal="drop me too", tool="llm")
            update_step(first, status="passed", result="done")

            assert delete_pending_steps_after(task_id, 1) == 2
            rows = {int(r["seq"]): r for r in get_task_steps(task_id)}
            assert set(rows) == {1}, "only the terminal prefix row survives"
            assert rows[1]["status"] == "passed"
            assert get_task(task_id) is not None
            assert second and third  # ids exist; they are simply gone from the table
        finally:
            _cleanup(db, task_id)


def test_full_backup_includes_task_tables(test_app):
    """FULL_TABLES lists tasks and task_steps after the chat tables group."""
    from app.routes.backups import FULL_TABLES

    assert "tasks" in FULL_TABLES and "task_steps" in FULL_TABLES
    assert FULL_TABLES.index("tasks") > FULL_TABLES.index("chat_sessions")
    assert FULL_TABLES.index("task_steps") > FULL_TABLES.index("chat_sessions")
