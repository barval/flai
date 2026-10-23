# tests/test_tasks_token_totals.py
"""Every task response must report the same token totals.

A single-task response (the task view load, a request, an answer) once defaulted
to plan-only totals, so opening or stopping a task made the list item's counter
drop from the real value to the planner's own usage and back.
"""

import json
import uuid
from datetime import UTC, datetime

import pytest

from app.database import get_db
from app.userdb import create_user

pytestmark = pytest.mark.requires_db

STEP_USAGE = {"prompt_tokens": 700, "completion_tokens": 200}
PLAN_USAGE = {"prompt_tokens": 300, "completion_tokens": 100}
REVISION_USAGE = {"prompt_tokens": 50, "completion_tokens": 10}


@pytest.fixture
def user_client(client, test_app):
    """A client logged in as the task owner."""
    with test_app.app_context():
        create_user("u1", "pw", "U1")

    client.post("/login", data={"login": "u1", "password": "pw"})
    return client


def _seed_task(login: str = "u1") -> str:
    task_id = str(uuid.uuid4())
    plan = {
        "plan_meta": {"usage": PLAN_USAGE},
        "revision_meta": {"1": {"usage": REVISION_USAGE}},
        "steps": [{"seq": 1, "kind": "direct", "goal": "g", "tool": "llm"}],
    }
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO tasks (id, user_id, status, title, step_plan, created_at) "
            "VALUES (%s, %s, 'completed', 't', %s, %s)",
            (task_id, login, json.dumps(plan), datetime.now(UTC)),
        )
        cursor.execute(
            "INSERT INTO task_steps (id, task_id, seq, kind, status, extra, finished_at) "
            "VALUES (%s, %s, 1, 'direct', 'passed', %s, %s)",
            (str(uuid.uuid4()), task_id, json.dumps({"usage": STEP_USAGE}), datetime.now(UTC)),
        )
    return task_id


def test_task_view_reports_step_plan_and_revision_tokens(user_client, test_app):
    with test_app.app_context():
        task_id = _seed_task()

    payload = user_client.get(f"/api/tasks/{task_id}/messages").get_json()

    totals = payload["task"]["token_totals"]
    assert totals["prompt_tokens"] == 700 + 300 + 50
    assert totals["completion_tokens"] == 200 + 100 + 10


def test_task_view_and_list_report_the_same_totals(user_client, test_app):
    with test_app.app_context():
        task_id = _seed_task()

    view = user_client.get(f"/api/tasks/{task_id}/messages").get_json()["task"]["token_totals"]
    listed = user_client.get("/api/tasks").get_json()["tasks"][0]["token_totals"]

    assert view == listed


def test_event_payload_carries_flat_totals_with_plan_usage(test_app):
    with test_app.app_context():
        create_user("u1", "pw", "U1")
        task_id = _seed_task()
        from app.tasks.events import _task_event_payload
        from app.tasks.store import get_task

        payload = _task_event_payload(get_task(task_id))

    assert payload["token_totals"]["prompt_tokens"] == 700 + 300 + 50
    assert payload["token_totals"]["completion_tokens"] == 200 + 100 + 10
