# tests/test_scheduler_actions.py
"""An action is a service run: queued ahead of its time, held until it, delivered as a message.

A run is not a task. These tests pin the four things that keep it honest: one run per
firing, no pile-up while the previous one is open, a result held until its time, and a run
that never started being cancelled instead of delivered a day late.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app import db, scheduler, scheduler_runs, scheduler_store, userdb

pytestmark = pytest.mark.requires_db

LOGIN = "scheduler_action_test"
TARGET = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)  # 12:00 in Moscow


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
                c.execute("DELETE FROM scheduler_runs WHERE user_id = %s", (LOGIN,))
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
        queued = []
        monkeypatch.setattr(
            test_app.request_queue,
            "add_service_run",
            lambda run_id, user_id, task_class, lang="ru": queued.append(run_id) or "queue-id",
        )
        test_app.events_publisher = _Collector()
        test_app.config["SCHEDULER_TICK_ENABLED"] = False
        yield {"app": test_app, "queued": queued}
        clear()


def _item(kind="action", **over):
    """A real row, because the tick finds the delivered run through it."""
    item_id = scheduler_store.create_item(
        LOGIN,
        kind=kind,
        title="Rate",
        body="Find the rate",
        repeat="once",
        at=TARGET,
        timezone_name="Europe/Moscow",
    )
    item = scheduler_store.get_item(item_id)
    item.update(over)
    return item


def _silence(item_id):
    """A one-off has already fired: keep the tick from queueing it again."""
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


def _env_app():
    from flask import current_app

    return current_app._get_current_object()


def test_an_action_creates_a_run_and_not_a_task(env):
    item = _item()

    run_id = scheduler.enqueue_run(_env_app(), item, item["next_run_at"])

    assert run_id
    assert scheduler_runs.get_run(run_id)["status"] == scheduler_runs.STATUS_QUEUED
    assert env["queued"] == [run_id]
    with db.get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT count(*) AS n FROM tasks WHERE user_id = %s", (LOGIN,))
        assert c.fetchone()["n"] == 0


@pytest.mark.parametrize("status", [scheduler_runs.STATUS_QUEUED, scheduler_runs.STATUS_RUNNING])
def test_an_open_run_skips_the_next_firing(env, status):
    item = _item()
    run_id = scheduler.enqueue_run(_env_app(), item, item["next_run_at"])
    if status == scheduler_runs.STATUS_RUNNING:
        scheduler_runs.mark_running(run_id)

    assert scheduler.enqueue_run(_env_app(), item, item["next_run_at"]) is None


def test_a_finished_run_does_not_block_the_next_firing(env):
    item = _item()
    run_id = scheduler.enqueue_run(_env_app(), item, item["next_run_at"])
    scheduler_runs.finish_run(run_id, scheduler_runs.STATUS_COMPLETED, result="76.4")

    assert scheduler.enqueue_run(_env_app(), item, item["next_run_at"]) is not None


def test_an_early_result_waits_for_its_time(env):
    item = _item()
    run_id = scheduler.enqueue_run(_env_app(), item, TARGET)
    _silence(item["id"])
    scheduler_runs.finish_run(run_id, scheduler_runs.STATUS_COMPLETED, result="76.4 ₽")

    early = scheduler.tick(env["app"], now=TARGET - timedelta(minutes=3))
    due = scheduler.tick(env["app"], now=TARGET + timedelta(minutes=1))

    assert early["delivered"] == 0
    assert due["delivered"] == 1
    messages = _actions_chat_messages()
    assert len(messages) == 1
    assert "76.4" in messages[0]["content"]
    assert messages[0]["scheduler_kind"] == "action"


def test_a_delivered_result_is_not_delivered_again(env):
    item = _item()
    run_id = scheduler.enqueue_run(_env_app(), item, TARGET)
    _silence(item["id"])
    scheduler_runs.finish_run(run_id, scheduler_runs.STATUS_COMPLETED, result="76.4 ₽")

    scheduler.tick(env["app"], now=TARGET + timedelta(minutes=1))
    second = scheduler.tick(env["app"], now=TARGET + timedelta(minutes=2))

    assert second["delivered"] == 0
    assert len(_actions_chat_messages()) == 1


def _finished_at(run_id, moment):
    """Pin the completion time: the sentence names when the work ended, not when the tick ran."""
    with db.get_db() as conn:
        conn.cursor().execute("UPDATE scheduler_runs SET finished_at = %s WHERE id = %s", (moment, run_id))


def test_a_late_result_says_so(env):
    item = _item()
    run_id = scheduler.enqueue_run(_env_app(), item, TARGET)
    _silence(item["id"])
    scheduler_runs.finish_run(run_id, scheduler_runs.STATUS_COMPLETED, result="76.4 ₽")
    _finished_at(run_id, TARGET + timedelta(minutes=14))

    scheduler.tick(env["app"], now=TARGET + timedelta(minutes=14))

    content = _actions_chat_messages()[0]["content"]
    assert "12:14" in content and "12:00" in content


def test_a_cancelled_run_delivers_its_notice(env):
    item = _item()
    run_id = scheduler.enqueue_run(_env_app(), item, TARGET)
    _silence(item["id"])
    scheduler_runs.finish_run(run_id, scheduler_runs.STATUS_CANCELLED)

    scheduler.tick(env["app"], now=TARGET + timedelta(minutes=1))

    content = _actions_chat_messages()[0]["content"]
    assert "⚠️ " in content
    assert "отменено" in content


def test_a_run_that_never_started_is_cancelled_when_it_is_too_late(env):
    item = _item()
    run_id = scheduler.enqueue_run(_env_app(), item, TARGET)
    _silence(item["id"])

    scheduler.tick(env["app"], now=TARGET + timedelta(hours=3))

    run = scheduler_runs.get_run(run_id)
    assert run["status"] == scheduler_runs.STATUS_CANCELLED
    assert run["error"] == "scheduler_late"
    content = _actions_chat_messages()[0]["content"]
    assert "⚠️ " in content
    assert "не успело" in content


def test_the_actions_chat_is_the_one_for_actions(env):
    item = _item()
    run_id = scheduler.enqueue_run(_env_app(), item, TARGET)
    _silence(item["id"])
    scheduler_runs.finish_run(run_id, scheduler_runs.STATUS_COMPLETED, result="76.4 ₽")

    scheduler.tick(env["app"], now=TARGET + timedelta(minutes=1))

    with db.get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT title FROM chat_sessions WHERE user_id = %s AND scheduler_kind = 'action'", (LOGIN,))
        assert c.fetchone()["title"] == "🛠️ Планировщик: действия"


def test_a_run_is_hidden_from_the_queue_status_but_counted(env):
    """The status panel is the person's own work; a run is not theirs to watch."""
    from app.queue import RedisRequestQueue

    assert "service_run" in RedisRequestQueue._HIDDEN_FROM_STATUS_TYPES
    # Hidden, not background: the counter is incremented on enqueue, so exempting the type
    # from the decrement would drift every user's counter upward, one run at a time.
    assert "service_run" not in RedisRequestQueue._BACKGROUND_TASK_TYPES


def test_a_run_does_not_publish_a_result_event(env):
    """result_completed hides the chat stop button; a run finishing must not do that."""
    collected = []

    class _Collector:
        def publish(self, user_id, event_type, data):
            collected.append(event_type)

    import app.queue as queue_module

    original = queue_module.get_events_publisher
    queue_module.get_events_publisher = lambda: _Collector()
    try:
        env["app"].request_queue._publish_result_event(
            {"id": "t1", "user_id": LOGIN, "type": "service_run", "data": {"type": "service_run"}},
            "completed",
            {},
        )
    finally:
        queue_module.get_events_publisher = original

    assert collected == []


def test_a_dropped_run_is_closed_so_the_next_firing_can_go(env):
    """A run dropped by the queue must not keep the overlap rule closed for an hour."""
    item = _item()
    run_id = scheduler.enqueue_run(_env_app(), item, item["next_run_at"])
    task = {"id": "queue-id", "type": "service_run", "data": {"run_id": run_id}, "lang": "ru"}

    closed = env["app"].request_queue._finalize_dropped_service_run(task, "queue_timeout")

    assert closed is True
    run = scheduler_runs.get_run(run_id)
    assert run["status"] == scheduler_runs.STATUS_CANCELLED
    assert run["error"] == "queue_timeout"
    assert scheduler_runs.has_open_run(item["id"]) is False
