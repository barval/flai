# tests/test_tasks_api.py
"""Internal Tasks API tests (v12.6, spec §5): list / create / messages /
answer / stop, owner scoping, class mapping and i18n keys.

The DB-backed tests are marked ``requires_db`` (they exercise the real
``tasks`` / ``task_steps`` tables); without a reachable PostgreSQL they skip.
"""

import base64
import os

import pytest

# A minimal 1×1 PNG (transparent pixel) for the multipart vision tests.
_PNG_1PX = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

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

    def test_doc_ids_round_trip(self, tasks_env, client, monkeypatch):
        """The Documents-panel selection rides with the request and is exposed
        in the summary; an empty list means «all of the user's documents»."""
        monkeypatch.setattr("app.routes.tasks.publish_task_update", lambda app, task: None)
        _login(client)

        created = client.post("/api/tasks", json={"request": "Найди в документах", "doc_ids": ["d1", "d2"]})
        assert created.get_json()["task"]["doc_ids"] == ["d1", "d2"]

        draft = client.post("/api/tasks", json={}).get_json()["task"]
        assert draft["doc_ids"] == []
        submitted = client.post(f"/api/tasks/{draft['id']}/request", json={"request": "x", "doc_ids": ["d9"]})
        assert submitted.get_json()["task"]["doc_ids"] == ["d9"]

    def test_list_reports_queue_position(self, tasks_env, client):
        """A queued row carries its global 1-based queue rank (the executor's
        own order: task_class asc, then FIFO); a non-queued row carries none."""
        _login(client)
        first = _seed_task(status="queued")
        second = _seed_task(status="queued")
        active = _seed_task(status="active")

        listed = {t["id"]: t for t in client.get("/api/tasks").get_json()["tasks"]}

        assert listed[first]["queue_position"] == 1
        assert listed[second]["queue_position"] == 2
        assert "queue_position" not in listed[active]

    def test_create_without_request_makes_draft(self, tasks_env, client):
        """«+» flow: no request → draft (executor never selects it)."""
        _login(client)
        response = client.post("/api/tasks", json={})

        assert response.status_code == 202
        task = response.get_json()["task"]
        assert task["status"] == "draft"
        assert task["title"] == ""

    def test_draft_request_submits_and_queues(self, tasks_env, client, monkeypatch):
        published = []
        monkeypatch.setattr("app.routes.tasks.publish_task_update", lambda app, task: published.append(task))
        _login(client)

        draft = client.post("/api/tasks", json={}).get_json()["task"]
        assert draft["status"] == "draft"

        submitted = client.post(f"/api/tasks/{draft['id']}/request", json={"request": "Summarize the report"})
        assert submitted.status_code == 202
        task = submitted.get_json()["task"]
        assert task["status"] == "queued"
        assert task["title"] == "Summarize the report"
        assert published[-1]["id"] == draft["id"]

    def test_draft_request_empty_rejected(self, tasks_env, client):
        _login(client)
        draft = client.post("/api/tasks", json={}).get_json()["task"]
        response = client.post(f"/api/tasks/{draft['id']}/request", json={"request": "   "})

        assert response.status_code == 400
        assert response.get_json()["error"].startswith("⚠️ ")

    def test_request_on_non_draft_conflicts(self, tasks_env, client):
        _login(client)
        task = client.post("/api/tasks", json={"request": "real request"}).get_json()["task"]
        assert task["status"] == "queued"
        response = client.post(f"/api/tasks/{task['id']}/request", json={"request": "again"})

        assert response.status_code == 409

    def test_draft_not_visible_to_executor_queries(self, tasks_env, client, test_app, monkeypatch):
        """A draft task holds no slot: the executor's selection never returns it."""
        from app.tasks.executor import get_in_progress_tasks, get_queued_task

        _login(client)
        draft = client.post("/api/tasks", json={}).get_json()["task"]

        assert draft["status"] == "draft"
        with test_app.app_context():
            assert get_queued_task() is None, "draft is not queued"
            assert all(t["id"] != draft["id"] for t in get_in_progress_tasks())

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
        assert response.get_json()["task"]["id"] == task_id
        assert messages[0]["role"] == "user" and messages[0]["content"] == "Summarize the report"
        plan_msg = next(m for m in messages if m["kind"] == "plan")
        assert plan_msg["content"].startswith("План из 2 шагов:")
        step_msg = next(m for m in messages if m["kind"] == "direct")
        assert step_msg["content"].startswith("Шаг 1 из 2:")
        assert "Found three sources" in step_msg["content"]
        # A tool-produced step names the tool (emoji + localized name) on its
        # OWN line above the answer; the client renders it as a second italic
        # service line, same font as the step header.
        assert step_msg["badge"].startswith("🔎") and step_msg["badge"].endswith(":")
        assert step_msg["content"].split("\n")[1] == step_msg["badge"]
        assert "Found three sources" in step_msg["content"]
        question_msg = next(m for m in messages if m["kind"] == "question")
        # The question shows as a plain assistant message (no step header, no
        # «Awaiting answer» marker — the waiting state lives in the live status).
        assert question_msg["content"] == "Which format?"

    def test_failed_step_shows_last_error(self, tasks_env, client):
        """A failed step with an empty result must show WHY it failed (chat
        parity: errors are visible, never silently blank)."""
        from app.tasks.store import add_step, update_step

        plan = {"task_request": "Do it", "steps": [{"seq": 1, "goal": "g", "tool": "camera"}]}
        task_id = _seed_task(status="active", plan=plan)
        step = add_step(task_id=task_id, seq=1, kind="subtask", goal="g", tool="camera")
        update_step(step, status="failed", last_error="Не удалось получить изображение с камеры кухня: Таймаут")
        _login(client)

        response = client.get(f"/api/tasks/{task_id}/messages")

        assert response.status_code == 200
        messages = response.get_json()["messages"]
        step_msg = next(m for m in messages if m["kind"] == "subtask")
        assert "Не удалось получить изображение" in step_msg["content"]
        assert step_msg["status"] == "failed"

    def test_failed_step_with_result_body_keeps_result(self, tasks_env, client):
        """A failed step that HAS a result body keeps it (no error appended)."""
        from app.tasks.store import add_step, update_step

        plan = {"task_request": "Do it", "steps": [{"seq": 1, "goal": "g", "tool": "llm"}]}
        task_id = _seed_task(status="active", plan=plan)
        step = add_step(task_id=task_id, seq=1, kind="direct", goal="g", tool="llm")
        update_step(step, status="failed", result="partial text", last_error="boom")
        _login(client)

        response = client.get(f"/api/tasks/{task_id}/messages")

        messages = response.get_json()["messages"]
        step_msg = next(m for m in messages if m["kind"] == "direct")
        assert "partial text" in step_msg["content"]
        assert "boom" not in step_msg["content"]

    def test_step_media_round_trip(self, tasks_env, client):
        """Media refs stored in the step's extra ride the messages payload."""
        import json

        from app.tasks.store import add_step, update_step

        plan = {"task_request": "Do it", "steps": [{"seq": 1, "goal": "g", "tool": "camera"}]}
        task_id = _seed_task(status="active", plan=plan)
        step = add_step(task_id=task_id, seq=1, kind="subtask", goal="g", tool="camera")
        media = {
            "file_path": f"{task_id}/snap.jpg",
            "file_name": "snap.jpg",
            "file_type": "image/jpeg",
            "file_size": 10,
        }
        update_step(step, status="passed", result="На снимке кошка.", extra=json.dumps({"media": media}))
        _login(client)

        response = client.get(f"/api/tasks/{task_id}/messages")

        messages = response.get_json()["messages"]
        step_msg = next(m for m in messages if m["kind"] == "subtask")
        assert step_msg["extra"]["media"]["file_path"] == f"{task_id}/snap.jpg"

    def test_messages_carry_created_at_for_timestamps(self, tasks_env, client):
        from app.tasks.store import add_step, update_step

        plan = {"task_request": "Do it", "steps": [{"seq": 1, "goal": "g", "tool": "llm"}]}
        task_id = _seed_task(status="active", plan=plan)
        step = add_step(task_id=task_id, seq=1, kind="direct", goal="g", tool="llm")
        update_step(step, status="passed", result="r", started_at="2026-10-11T10:00:00+00:00")
        _login(client)

        response = client.get(f"/api/tasks/{task_id}/messages")

        assert response.status_code == 200
        messages = response.get_json()["messages"]
        assert len(messages) == 3  # request + plan + step
        for message in messages:
            assert message["created_at"], f"message {message['kind']} has no created_at timestamp"

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

    def test_answer_records_documents(self, tasks_env, client):
        """An answer may carry the current Documents-panel selection — it
        re-scopes the task's remaining document steps."""
        from app.tasks.store import add_step, get_task, update_step

        plan = {
            "task_request": "Ask me",
            "steps": [{"seq": 1, "goal": "Q?", "tool": "llm", "user_input_expected": True}],
        }
        task_id = _seed_task(status="awaiting_user", plan=plan)
        question = add_step(task_id=task_id, seq=1, kind="question", goal="Q?", tool="llm")
        update_step(question, status="awaiting_user", result="Q?")
        _login(client)

        response = client.post(f"/api/tasks/{task_id}/answer", json={"answer": "вот документы", "doc_ids": ["d7"]})

        assert response.status_code == 202
        assert get_task(task_id)["doc_ids"] == ["d7"]

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

    def test_delete_removes_task_and_steps(self, tasks_env, client, test_app):
        from app.tasks.executor import get_task_steps
        from app.tasks.store import add_step, get_task, update_step

        task_id = _seed_task(status="active", plan={"steps": [{"seq": 1, "goal": "g", "tool": "llm"}]})
        step = add_step(task_id=task_id, seq=1, kind="direct", goal="g", tool="llm")
        update_step(step, status="passed", result="r")
        _login(client)

        response = client.post(f"/api/tasks/{task_id}/delete")

        assert response.status_code == 200
        assert response.get_json() == {"status": "ok"}
        assert get_task(task_id) is None
        assert get_task_steps(task_id) == []
        assert client.get(f"/api/tasks/{task_id}/messages").status_code == 404

    def test_delete_active_raises_cancel_flag(self, tasks_env, client, test_app):
        from app.tasks.store import get_task

        task_id = _seed_task(status="active", plan={"steps": [{"seq": 1, "goal": "g", "tool": "llm"}]})
        _login(client)

        response = client.post(f"/api/tasks/{task_id}/delete")

        assert response.status_code == 200
        # The executor must never resume a deleted task.
        test_app.request_queue.redis.set.assert_any_call(f"task:cancelled:{task_id}", "1", ex=3600)
        assert get_task(task_id) is None

    def test_delete_terminal_skips_cancel_flag(self, tasks_env, client, test_app):
        from app.tasks.store import get_task

        task_id = _seed_task(status="completed", plan={"steps": [{"seq": 1, "goal": "g", "tool": "llm"}]})
        _login(client)

        assert client.post(f"/api/tasks/{task_id}/delete").status_code == 200
        assert get_task(task_id) is None
        # No flag write for an already-terminal task.
        flag_calls = [
            c
            for c in test_app.request_queue.redis.set.call_args_list
            if c.args and str(c.args[0]).startswith(f"task:cancelled:{task_id}")
        ]
        assert flag_calls == []

    def test_delete_foreign_is_not_found(self, tasks_env, client):
        task_id = _seed_task(user_id="someone-else", status="queued")
        _login(client, login="owner")

        response = client.post(f"/api/tasks/{task_id}/delete")

        assert response.status_code == 404
        assert response.get_json()["error"].startswith("⚠️ ")

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

    def test_task_media_file_owned_via_tasks_table(self, tasks_env, client):
        """Task media (camera snapshots) live under the TASK id folder; the
        tasks table is a second ownership source for /api/files."""
        import os

        from app.tasks.store import create_task

        task_id = create_task(user_id="owner", task_class=3, title="t", lang="ru", response_style="neutral")
        with client.session_transaction() as sess:
            sess["login"] = "owner"
        # The file lives under <UPLOAD_FOLDER>/<task_id>/...
        upload_root = client.application.config["UPLOAD_FOLDER"]
        folder = os.path.join(upload_root, task_id)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "snap.txt"), "w", encoding="utf-8") as fh:
            fh.write("snapshot placeholder")

        response = client.get(f"/api/files/{task_id}/snap.txt")

        assert response.status_code == 200

        # A foreign user has no access to the same file.
        with client.session_transaction() as sess:
            sess["login"] = "intruder"
        assert client.get(f"/api/files/{task_id}/snap.txt").status_code == 403

    # ── Vision (v12.6): images attached in the task view ────────────────────

    def test_request_with_images_multipart_saves_and_queues(self, tasks_env, client, monkeypatch):
        """Multipart /request with images: saved to disk, names in summary,
        image dicts in the row; a vision step can then be planned."""
        import io

        monkeypatch.setattr("app.routes.tasks.publish_task_update", lambda app, task: None)
        _login(client)
        draft = client.post("/api/tasks", json={}).get_json()["task"]

        png = io.BytesIO(base64.b64decode(_PNG_1PX))
        response = client.post(
            f"/api/tasks/{draft['id']}/request",
            data={"request": "Что на картинке?", "deep_analysis": "false", "images": (png, "a.png", "image/png")},
            content_type="multipart/form-data",
        )

        assert response.status_code == 202
        task = response.get_json()["task"]
        assert task["status"] == "queued"
        assert task["images"] == ["a.png"]

        from app.tasks.store import get_task as get_task_row

        row = get_task_row(draft["id"])
        assert len(row["images"]) == 1
        assert row["images"][0]["file_name"] == "a.png"
        assert row["images"][0]["file_path"], "relative path stored"

    def test_request_rejects_non_image_file(self, tasks_env, client, monkeypatch):
        """A non-image upload is rejected with a localized ⚠️ 400."""
        import io

        monkeypatch.setattr("app.routes.tasks.publish_task_update", lambda app, task: None)
        _login(client, language="ru")
        draft = client.post("/api/tasks", json={}).get_json()["task"]

        response = client.post(
            f"/api/tasks/{draft['id']}/request",
            data={"request": "Что здесь?", "images": (io.BytesIO(b"hello"), "note.txt", "text/plain")},
            content_type="multipart/form-data",
        )

        assert response.status_code == 400
        assert response.get_json()["error"].startswith("⚠️ ")
        assert "изображени" in response.get_json()["error"]

    def test_answer_with_images_merges(self, tasks_env, client, monkeypatch):
        """Answering an awaiting question with an image adds it to the task."""
        import io

        monkeypatch.setattr("app.routes.tasks.publish_task_update", lambda app, task: None)
        _login(client)
        task_id = _seed_task(status="awaiting_user")
        from app.tasks.store import add_step, update_step

        question = add_step(task_id=task_id, seq=1, kind="question", goal="Какой город?", tool="llm")
        update_step(question, status="awaiting_user", result="Какой город?")

        response = client.post(
            f"/api/tasks/{task_id}/answer",
            data={
                "answer": "Вот фото этого города",
                "images": (io.BytesIO(base64.b64decode(_PNG_1PX)), "city.png", "image/png"),
            },
            content_type="multipart/form-data",
        )

        assert response.status_code == 202
        from app.tasks.store import get_task as get_task_row

        row = get_task_row(task_id)
        assert [i["file_name"] for i in row["images"]] == ["city.png"]

    def test_images_listed_in_summary_names_only(self, tasks_env, client):
        """The summary exposes image file NAMES — no paths, no bytes."""
        from app.tasks.store import create_task as _ct
        from app.tasks.store import update_task as _ut

        task_id = _ct(user_id="owner", task_class=3, title="t", lang="ru", response_style="neutral")
        _ut(task_id, images=[{"file_path": "x/a.png", "file_name": "a.png"}])
        _login(client)

        listed = {t["id"]: t for t in client.get("/api/tasks").get_json()["tasks"]}
        assert listed[task_id]["images"] == ["a.png"]


@pytest.mark.unit
class TestTasksApiI18n:
    def test_new_msgids_present_in_both_catalogs(self):
        new_msgids = [
            "Background tasks are disabled",
            "Task request is empty",
            "Answer is empty",
            "Task is not awaiting an answer",
            "Task is not a draft",
            "Plan of {n} steps:",
            "Step {n} of {m}:",
            "Awaiting answer",
            "Draft",
            "Draft — describe the task…",
            "No task selected — use «+» to create one",
            "Document search",
            "Image analysis",
            "Attached images: {names}",
            "Only image files can be attached to a task",
        ]
        for catalog in ("translations/en/LC_MESSAGES/messages.po", "translations/ru/LC_MESSAGES/messages.po"):
            with open(catalog, encoding="utf-8") as fh:
                content = fh.read()
            for msgid in new_msgids:
                assert f'msgid "{msgid}"' in content, f"{msgid} missing from {catalog}"
