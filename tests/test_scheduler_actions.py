# tests/test_scheduler_actions.py
"""An action is a task on the Tasks engine: enqueued ahead of its time, held until it.

A reminder is a sentence; an action is work that queues behind chat and may therefore run
late. These tests pin the four things that keep it honest: one task per firing, no pile-up
while the previous one runs, a result held until its time, and a task that never started
being cancelled instead of delivered a day late.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app import db, scheduler, scheduler_store, userdb
from app.tasks import store

pytestmark = pytest.mark.requires_db

LOGIN = "scheduler_action_test"


class _Collector:
    def __init__(self):
        self.events = []

    def publish(self, user_id, event_type, data):
        self.events.append({"user_id": user_id, "type": event_type, "data": data})


@pytest.fixture
def env(test_app, monkeypatch):
    with test_app.app_context():
        from app.database import get_db, init_db

        init_db()

        def clear():
            with get_db() as conn:
                c = conn.cursor()
                c.execute("DELETE FROM tasks WHERE user_id = %s", (LOGIN,))
                c.execute("DELETE FROM scheduler_items WHERE user_id = %s", (LOGIN,))
                c.execute(
                    "DELETE FROM messages WHERE session_id IN"
                    " (SELECT id FROM chat_sessions WHERE user_id = %s)",
                    (LOGIN,),
                )
                c.execute("DELETE FROM session_visits WHERE user_id = %s", (LOGIN,))
                c.execute("DELETE FROM chat_sessions WHERE user_id = %s", (LOGIN,))
                c.execute("DELETE FROM users WHERE login = %s", (LOGIN,))

        clear()
        userdb.create_user(LOGIN, "pw", "Name", service_class=1, language="ru")
        monkeypatch.setattr(scheduler, "_redis", lambda app: None)
        test_app.config["SCHEDULER_TICK_ENABLED"] = False
        test_app.events_publisher = _Collector()
        yield {"app": test_app}
        clear()


TARGET = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)  # 12:00 in Moscow


def _item(kind="action", **over):
    """A real row, because the tick finds the delivered task through it."""
    item_id = scheduler_store.create_item(
        LOGIN,
        kind=kind,
        title="Rooms",
        body="Check every room for cats",
        repeat="once",
        at=TARGET,
        timezone_name="Europe/Moscow",
    )
    item = scheduler_store.get_item(item_id)
    item.update(over)
    return item


def _silence(item_id):
    """A one-off has already fired: keep the tick from enqueuing it again."""
    scheduler_store.update_item(item_id, enabled=False, next_run_at=None)


def _actions_chat_messages():
    with db.get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id FROM chat_sessions WHERE user_id = %s AND scheduler_kind = 'action'",
            (LOGIN,),
        )
        rows = [dict(row) for row in c.fetchall()]
    if not rows:
        return []
    return db.get_session_messages(rows[0]["id"])


def _task_id(item, target):
    task_id = scheduler.enqueue_action(env_app(), item, target)
    assert task_id
    return task_id


def env_app():
    from flask import current_app

    return current_app._get_current_object()


def test_an_action_becomes_one_task_at_the_users_class(env):
    item = _item()
    target = item["next_run_at"]

    task_id = _task_id(item, target)

    task = store.get_task(task_id)
    assert task["user_id"] == LOGIN
    assert task["task_class"] == 4  # service_class 1 -> work class 4
    assert str(task["scheduler_item_id"]) == item["id"]
    assert task["scheduler_deliver_at"] == target
    assert task["title"] == "Rooms"


@pytest.mark.parametrize("status", ["queued", "active", "frozen", "awaiting_user"])
def test_a_running_action_is_not_started_twice(env, status):
    item = _item()
    first = _task_id(item, item["next_run_at"])
    store.update_task(first, status=status)

    assert scheduler.enqueue_action(env_app(), item, item["next_run_at"]) is None


def test_a_finished_action_does_not_block_the_next_firing(env):
    item = _item()
    first = _task_id(item, item["next_run_at"])
    store.update_task(first, status="completed")

    assert scheduler.enqueue_action(env_app(), item, item["next_run_at"]) is not None


def test_an_early_result_waits_for_its_time(env):
    item = _item()
    target = item["next_run_at"]
    task_id = _task_id(item, target)
    _silence(item["id"])
    store.update_task(task_id, status="completed")
    from app.tasks.executor import add_final_step

    add_final_step(task_id, "All rooms are empty")

    early = scheduler.tick(env["app"], now=target - timedelta(minutes=3))
    due = scheduler.tick(env["app"], now=target + timedelta(minutes=1))

    assert early["delivered"] == 0
    assert due["delivered"] == 1
    messages = _actions_chat_messages()
    assert len(messages) == 1
    assert "All rooms are empty" in messages[0]["content"]
    assert messages[0]["scheduler_kind"] == "action"


def test_a_delivered_result_is_not_delivered_again(env):
    item = _item()
    target = item["next_run_at"]
    task_id = _task_id(item, target)
    _silence(item["id"])
    store.update_task(task_id, status="completed")
    from app.tasks.executor import add_final_step

    add_final_step(task_id, "All rooms are empty")

    scheduler.tick(env["app"], now=target + timedelta(minutes=1))
    second = scheduler.tick(env["app"], now=target + timedelta(minutes=2))

    assert second["delivered"] == 0
    assert len(_actions_chat_messages()) == 1


def test_a_late_result_says_so(env):
    item = _item()
    target = item["next_run_at"]
    task_id = _task_id(item, target)
    _silence(item["id"])
    store.update_task(task_id, status="completed")
    from app.tasks.executor import add_final_step

    add_final_step(task_id, "All rooms are empty")

    scheduler.tick(env["app"], now=target + timedelta(minutes=14))

    content = _actions_chat_messages()[0]["content"]
    assert "12:14" in content and "12:00" in content


def test_a_cancelled_action_delivers_its_notice(env):
    item = _item()
    target = item["next_run_at"]
    task_id = _task_id(item, target)
    _silence(item["id"])
    store.update_task(task_id, status="cancelled")

    scheduler.tick(env["app"], now=target + timedelta(minutes=1))

    content = _actions_chat_messages()[0]["content"]
    assert "⚠️ " in content
    assert "отменено" in content


def test_a_task_that_never_started_is_cancelled_when_it_is_too_late(env):
    item = _item()
    target = item["next_run_at"]
    task_id = _task_id(item, target)
    _silence(item["id"])

    now = target + timedelta(hours=3)
    scheduler.tick(env["app"], now=now)

    task = store.get_task(task_id)
    assert task["status"] == "cancelled"
    assert task["abort_reason"] == "scheduler_late"
    content = _actions_chat_messages()[0]["content"]
    assert "⚠️ " in content


def test_the_actions_chat_is_the_one_for_actions(env):
    item = _item()
    target = item["next_run_at"]
    task_id = _task_id(item, target)
    _silence(item["id"])
    store.update_task(task_id, status="completed")
    from app.tasks.executor import add_final_step

    add_final_step(task_id, "All rooms are empty")

    scheduler.tick(env["app"], now=target + timedelta(minutes=1))

    with db.get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT title FROM chat_sessions WHERE user_id = %s AND scheduler_kind = 'action'", (LOGIN,))
        assert c.fetchone()["title"] == "🛠️ Планировщик: действия"


def test_the_lead_is_capped_by_the_interval(env):
    """A ten-minute lead under a five-minute cadence queues the next run too early."""
    item = {"kind": "action", "repeat": "minutes", "every_minutes": 5}
    assert scheduler.action_lead_minutes(item, 10) == 2

    assert scheduler.action_lead_minutes({"kind": "action", "repeat": "minutes", "every_minutes": 10}, 10) == 5

    assert scheduler.action_lead_minutes({"kind": "action", "repeat": "minutes", "every_minutes": 15}, 10) == 7

    # A long cadence keeps the configured lead: half of a day is not a lead.
    assert scheduler.action_lead_minutes({"kind": "action", "repeat": "daily"}, 10) == 10
    assert scheduler.action_lead_minutes({"kind": "action", "repeat": "once"}, 10) == 10


def test_a_frequent_action_is_not_enqueued_before_half_its_interval(env):
    target = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
    item = {
        "kind": "action",
        "repeat": "minutes",
        "every_minutes": 10,
        "next_run_at": target,
    }

    assert scheduler.is_due_now(item, target - timedelta(minutes=8), 10) is False
    assert scheduler.is_due_now(item, target - timedelta(minutes=5), 10) is True

    reminder = {"kind": "reminder", "repeat": "minutes", "every_minutes": 10, "next_run_at": target}
    assert scheduler.is_due_now(reminder, target - timedelta(minutes=8), 10) is False
    assert scheduler.is_due_now(reminder, target, 10) is True
