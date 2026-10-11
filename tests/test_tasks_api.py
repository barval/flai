# tests/test_tasks_api.py
"""Internal Tasks API tests (v12.6, spec §5): list / create / messages /
answer / stop, owner scoping, class mapping and i18n keys.

The DB-backed tests are marked ``requires_db`` (they exercise the real
``tasks`` / ``task_steps`` tables); without a reachable PostgreSQL they skip.
"""

import os

import pytest

_db_available = (
    bool(os.environ.get("DATABASE_URL")) and os.environ["DATABASE_URL"] != "postgresql://localhost:5432/flai_test"
)


@pytest.fixture
def tasks_env(test_app):
    """Store-backed environment: stop the executor singleton, clean tasks tables."""
    if not _db_available:
        pytest.skip("No reachable PostgreSQL for tasks API tests")
    import app.tasks.executor as ex
    from app.database import init_db

    try:
        init_db()
        ex.get_task("00000000-0000-0000-0000-000000000000")
    except Exception as exc:
        pytest.skip(f"PostgreSQL unreachable: {exc}")

    # The route reads Redis only for the cancel flag; the shared mock answers None.
    test_app.request_queue.redis.get.return_value = None
    test_app.request_queue.redis.reset_mock()
    test_app.request_queue.redis.get.return_value = None
    ex.stop_task_executor(timeout=2)
    with ex._executor_lock:
        ex._EXECUTOR = None

    _clean(test_app)
    yield ex
    _clean(test_app)


def _clean(test_app):
    from app.database import get_db

    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM task_steps")
        c.execute("DELETE FROM tasks")


def _login(client, login="owner", *, is_admin=False, service_class=2, language="ru", response_style="neutral"):
    with client.session_transaction() as sess:
        sess["login"] = login
        sess["user_id"] = login
        sess["name"] = login
        sess["service_class"] = service_class
        sess["language"] = language
        sess["response_style"] = response_style
        if is_admin:
            sess["is_admin"] = True


def _seed_task(user_id="owner", status="queued", plan=None):
    from app.tasks.store import create_task, update_task

    task_id = create_task(
        user_id=user_id, task_class=3, title="Summarize the report", lang="ru", response_style="neutral"
    )
    if plan is not None or status != "queued":
        update_task(task_id, status=status, step_plan=plan or {})
    return task_id


@pytest.mark.requires_db
class TestTasksApi:
    def test_list_requires_login(self, client):
        response = client.get("/api/tasks")

        assert response.status_code == 401
        assert response.get_json()["error"].startswith("⚠️ ")

    def test_create_and_list(self, tasks_env, client, monkeypatch):
        published = []
        monkeypatch.setattr("app.routes.tasks.publish_task_update", lambda app, task: published.append(task))
        _login(client, service_class=1)

        created = client.post("/api/tasks", json={"request": "Summarize the report"})

        assert created.status_code == 202
        task = created.get_json()["task"]
        assert task["status"] == "queued"
        assert task["task_class"] == 4  # service_class 1 → queue class 4
        assert task["title"] == "Summarize the report"
        assert task["total_steps"] == 0
        assert published and published[0]["id"] == task["id"]

        listed = client.get("/api/tasks")
        assert listed.status_code == 200
        ids = [t["id"] for t in listed.get_json()["tasks"]]
        assert task["id"] in ids

    def test_create_requires_request(self, tasks_env, client):
        _login(client)
        response = client.post("/api/tasks", json={"request": "   "})

        assert response.status_code == 400
        assert response.get_json()["error"].startswith("⚠️ ")

    def test_create_rejected_when_disabled(self, tasks_env, client, test_app):
        test_app.config["TASKS_ENABLED"] = False
        _login(client)
        response = client.post("/api/tasks", json={"request": "do it"})

        assert response.status_code == 403
        assert response.get_json()["error"].startswith("⚠️ ")

    def test_messages_render_plan_steps_and_question(self, tasks_env, client):
        from app.tasks.store import add_step, update_step

        plan = {
            "title": "T",
            "task_request": "Summarize the report",
            "steps": [
                {"seq": 1, "goal": "Find sources", "tool": "search", "checkpoint_criteria": "x"},
                {"seq": 2, "goal": "Which format?", "tool": "llm", "user_input_expected": True},
            ],
        }
        task_id = _seed_task(status="awaiting_user", plan=plan)
        step1 = add_step(task_id=task_id, seq=1, kind="direct", goal="Find sources", tool="search")
        update_step(step1, status="passed", result="Found three sources")
        question = add_step(task_id=task_id, seq=2, kind="question", goal="Which format?", tool="llm")
        update_step(question, status="awaiting_user", result="Which format?")
        _login(client)

        response = client.get(f"/api/tasks/{task_id}/messages")

        assert response.status_code == 200
        messages = response.get_json()["messages"]
        assert messages[0]["role"] == "user" and messages[0]["content"] == "Summarize the report"
        plan_msg = next(m for m in messages if m["kind"] == "plan")
        assert plan_msg["content"].startswith("План из 2 шагов:")
        step_msg = next(m for m in messages if m["kind"] == "direct")
        assert step_msg["content"].startswith("Шаг 1 из 2:")
        assert "Found three sources" in step_msg["content"]
        question_msg = next(m for m in messages if m["kind"] == "question")
        assert "Шаг 2 из 2:" in question_msg["content"] and "Ожидает ответа" in question_msg["content"]

    def test_answer_creates_user_input_step(self, tasks_env, client):
        from app.tasks.executor import get_task_steps
        from app.tasks.store import add_step, update_step

        plan = {
            "task_request": "Ask me",
            "steps": [{"seq": 1, "goal": "Q?", "tool": "llm", "user_input_expected": True}],
        }
        task_id = _seed_task(status="awaiting_user", plan=plan)
        question = add_step(task_id=task_id, seq=1, kind="question", goal="Q?", tool="llm")
        update_step(question, status="awaiting_user", result="Q?")
        _login(client)

        response = client.post(f"/api/tasks/{task_id}/answer", json={"answer": "JSON please"})

        assert response.status_code == 202
        rows = get_task_steps(task_id)
        user_inputs = [r for r in rows if r["kind"] == "user_input"]
        assert len(user_inputs) == 1
        assert user_inputs[0]["result"] == "JSON please"
        assert user_inputs[0]["parent_step_id"] == question

    def test_answer_without_question_conflicts(self, tasks_env, client):
        task_id = _seed_task(status="active", plan={"steps": [{"seq": 1, "goal": "g", "tool": "llm"}]})
        _login(client)

        response = client.post(f"/api/tasks/{task_id}/answer", json={"answer": "x"})

        assert response.status_code == 409
        assert response.get_json()["error"].startswith("⚠️ ")

    def test_stop_queued_cancels_immediately(self, tasks_env, client, test_app):
        task_id = _seed_task(status="queued")
        _login(client)

        response = client.post(f"/api/tasks/{task_id}/stop")

        assert response.status_code == 200
        assert response.get_json()["status"] == "cancelled"
        from app.tasks.store import get_task

        assert get_task(task_id)["status"] == "cancelled"
        test_app.request_queue.redis.set.assert_any_call(f"task:cancelled:{task_id}", "1", ex=3600)

    def test_stop_active_only_raises_flag(self, tasks_env, client, test_app):
        task_id = _seed_task(status="active", plan={"steps": [{"seq": 1, "goal": "g", "tool": "llm"}]})
        _login(client)

        response = client.post(f"/api/tasks/{task_id}/stop")

        assert response.status_code == 200
        from app.tasks.store import get_task

        assert get_task(task_id)["status"] == "active"  # the executor interrupts the step
        test_app.request_queue.redis.set.assert_any_call(f"task:cancelled:{task_id}", "1", ex=3600)

    def test_foreign_task_is_not_found(self, tasks_env, client):
        from app.tasks.store import add_step

        task_id = _seed_task(
            user_id="someone-else", status="active", plan={"steps": [{"seq": 1, "goal": "g", "tool": "llm"}]}
        )
        add_step(task_id=task_id, seq=1, kind="question", goal="Q?", tool="llm")
        _login(client, login="owner")

        assert client.get(f"/api/tasks/{task_id}/messages").status_code == 404
        assert client.post(f"/api/tasks/{task_id}/answer", json={"answer": "x"}).status_code == 404
        assert client.post(f"/api/tasks/{task_id}/stop").status_code == 404

    def test_admin_can_read_foreign_task(self, tasks_env, client):
        task_id = _seed_task(user_id="someone-else", status="active", plan={"task_request": "theirs"})
        _login(client, login="root", is_admin=True)

        response = client.get(f"/api/tasks/{task_id}/messages")

        assert response.status_code == 200
        assert response.get_json()["messages"][0]["content"] == "theirs"


@pytest.mark.unit
class TestTasksApiI18n:
    def test_new_msgids_present_in_both_catalogs(self):
        new_msgids = [
            "Background tasks are disabled",
            "Task request is empty",
            "Answer is empty",
            "Task is not awaiting an answer",
            "Plan of {n} steps:",
            "Step {n} of {m}:",
            "Awaiting answer",
        ]
        for catalog in ("translations/en/LC_MESSAGES/messages.po", "translations/ru/LC_MESSAGES/messages.po"):
            with open(catalog, encoding="utf-8") as fh:
                content = fh.read()
            for msgid in new_msgids:
                assert f'msgid "{msgid}"' in content, f"{msgid} missing from {catalog}"
