# tests/test_tasks_admin.py
"""Admin Users tab: the Tasks count column (v12.6, spec §5 "Admin → Users").

The users payload gains ``tasks_count`` (COUNT over the tasks table by
user_id = login, same shape as sessions_count); the Users table gains a
localized "Tasks" column right after Sessions. Classes 3/4/5 stay hidden —
the admin class editor keeps offering only the user-facing 0/1/2.
"""

import os
import pathlib
import re

import pytest

_db_available = (
    bool(os.environ.get("DATABASE_URL")) and os.environ["DATABASE_URL"] != "postgresql://localhost:5432/flai_test"
)

ADMIN_JS = pathlib.Path("app/static/js/admin.js").read_text(encoding="utf-8")
ADMIN_HTML = pathlib.Path("app/templates/admin.html").read_text(encoding="utf-8")
EN_PO = pathlib.Path("translations/en/LC_MESSAGES/messages.po").read_text(encoding="utf-8")
RU_PO = pathlib.Path("translations/ru/LC_MESSAGES/messages.po").read_text(encoding="utf-8")


class TestAdminTasksColumn:
    """Payload + UI wiring for the Tasks count column."""

    def test_users_payload_includes_tasks_count(self):
        src = pathlib.Path("app/routes/admin.py").read_text(encoding="utf-8")
        assert "tasks_count" in src

    def test_tasks_count_sql_counts_tasks_by_login(self):
        src = pathlib.Path("app/routes/admin.py").read_text(encoding="utf-8")
        assert re.search(r"FROM\s+tasks\s+WHERE\s+user_id\s*=\s*%s", src)

    def test_column_header_after_sessions(self):
        idx_sessions = ADMIN_HTML.index('data-sort="sessions_count"')
        idx_tasks = ADMIN_HTML.index('data-sort="tasks_count"')
        assert idx_sessions < idx_tasks

    def test_admin_js_renders_tasks_cell_after_sessions(self):
        idx_sessions = ADMIN_JS.index("sessionsCell")
        idx_tasks = ADMIN_JS.index("tasksCell")
        assert idx_sessions < idx_tasks
        assert "user.tasks_count" in ADMIN_JS

    def test_ru_translations_present(self):
        assert 'msgid "Tasks"' in EN_PO
        assert 'msgid "Tasks"' in RU_PO
        assert "Задачи" in RU_PO


@pytest.mark.requires_db
class TestAdminTasksPayload:
    """DB-backed payload check against the real tasks table."""

    @pytest.fixture
    def tasks_env(self, test_app):
        if not _db_available:
            pytest.skip("No reachable PostgreSQL for admin tasks tests")
        from app.database import init_db

        try:
            init_db()
            from app.tasks.store import get_task

            get_task("00000000-0000-0000-0000-000000000000")
        except Exception as exc:
            pytest.skip(f"PostgreSQL unreachable: {exc}")

    def test_tasks_count_in_users_payload(self, tasks_env, client, test_app):
        from app.tasks.store import create_task, delete_task
        from app.userdb import create_user, delete_user, get_user_by_login

        login = "tasks-admin-ui"
        # A dedicated row guarantees a non-zero count regardless of suite order.
        task_id = create_task(user_id=login, task_class=4, title="T", lang="ru", response_style="neutral")
        try:
            with test_app.app_context():
                if not get_user_by_login(login):
                    create_user(login=login, password="pw-tasks-admin-123456", name="Tasks Admin UI")
            with client.session_transaction() as sess:
                sess["login"] = login
                sess["is_admin"] = True

            response = client.get("/admin/api/users")
            assert response.status_code == 200
            payload = response.get_json()
            row = next((u for u in payload if u["login"] == login), None)
            assert row is not None, "user must exist in the admin payload"
            assert row["tasks_count"] == 1
        finally:
            delete_task(task_id)
            with test_app.app_context():
                if get_user_by_login(login):
                    delete_user(login)
