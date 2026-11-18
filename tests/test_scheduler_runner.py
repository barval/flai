# tests/test_scheduler_runner.py
"""One action: a short tool loop, an answer, and a run row that says what happened.

The model is faked at the seam (`scheduler_runner._ask_model`), so these tests pin the
runner's own behaviour: the answer is stored, `start_task` ends the run with the tool's
confirmation, cancellation stops it between rounds, and the round ceiling holds.
"""

from datetime import UTC, datetime

import pytest

from app import scheduler_runs, scheduler_store, userdb
from app.engine.loop import Turn

pytestmark = pytest.mark.requires_db

LOGIN = "scheduler_runner_test"
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


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
                c.execute("DELETE FROM users WHERE login = %s", (LOGIN,))

        clear()
        userdb.create_user(LOGIN, "pw", "Name", service_class=2, language="ru")
        test_app.config["SCHEDULER_RUN_ROUNDS"] = 3
        test_app.config["SCHEDULER_RUN_TIMEOUT_S"] = 180
        yield test_app
        clear()


def _run(body="Узнай курс доллара", **over):
    item_id = scheduler_store.create_item(
        LOGIN,
        kind="action",
        title="Курс",
        body=body,
        repeat="once",
        at=NOW,
        timezone_name="Europe/Moscow",
    )
    for key, value in over.items():
        scheduler_store.update_item(item_id, **{key: value})
    run_id = scheduler_runs.create_run(item_id, LOGIN, NOW)
    scheduler_runs.mark_running(run_id)
    return run_id


def test_a_run_answers_and_stores_its_result(env, monkeypatch):
    from app import scheduler_runner

    run_id = _run()
    monkeypatch.setattr(scheduler_runner, "_ask_model", lambda *a, **k: Turn(text="76.4 ₽"))

    outcome = scheduler_runner.run_service_action(env, run_id)

    run = scheduler_runs.get_run(run_id)
    assert outcome["status"] == "completed"
    assert run["status"] == scheduler_runs.STATUS_COMPLETED
    assert run["result"] == "76.4 ₽"
    assert run["rounds"] == 1


def test_start_task_ends_the_run_with_the_confirmation(env, monkeypatch):
    from app import scheduler_runner
    from app.tasks import store as task_store

    run_id = _run(body="Запусти задачу: снимки всех комнат и события в файл")
    calls = {"rounds": 0}

    def fake_model(app, messages, tools, tool_choice, language):
        calls["rounds"] += 1
        return Turn(
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "start_task",
                        "arguments": '{"goal": "Снимки всех комнат и события в файл", "title": "Снимки комнат"}',
                    },
                }
            ]
        )

    monkeypatch.setattr(scheduler_runner, "_ask_model", fake_model)

    scheduler_runner.run_service_action(env, run_id)

    run = scheduler_runs.get_run(run_id)
    assert run["status"] == scheduler_runs.STATUS_COMPLETED
    assert run["result"] == "🛠️ Снимки комнат — задача запущена, результат появится в «Задачах»"
    assert calls["rounds"] == 1, "no round runs after the task is started"
    tasks = task_store.get_tasks_by_user(LOGIN)
    assert [task["title"] for task in tasks] == ["🛠️ Планировщик: Снимки комнат"]


def test_a_cancelled_run_stops_between_rounds(env, monkeypatch):
    from app import scheduler_runner

    run_id = _run()
    seen = {"rounds": 0}

    def fake_model(app, messages, tools, tool_choice, language):
        seen["rounds"] += 1
        # The person deleted the item while the first round was running.
        scheduler_runs.finish_run(run_id, scheduler_runs.STATUS_CANCELLED)
        return Turn(
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "get_current_time", "arguments": "{}"},
                }
            ]
        )

    monkeypatch.setattr(scheduler_runner, "_ask_model", fake_model)

    outcome = scheduler_runner.run_service_action(env, run_id)

    assert outcome["status"] == "cancelled"
    assert seen["rounds"] == 1
    assert scheduler_runs.get_run(run_id)["status"] == scheduler_runs.STATUS_CANCELLED


def test_the_round_ceiling_is_enforced(env, monkeypatch):
    from app import scheduler_runner

    run_id = _run()
    env.config["SCHEDULER_RUN_ROUNDS"] = 2
    seen = {"rounds": 0}

    def fake_model(app, messages, tools, tool_choice, language):
        seen["rounds"] += 1
        return Turn(
            tool_calls=[
                {
                    "id": f"call-{seen['rounds']}",
                    "type": "function",
                    "function": {"name": "get_current_time", "arguments": "{}"},
                }
            ]
        )

    monkeypatch.setattr(scheduler_runner, "_ask_model", fake_model)

    scheduler_runner.run_service_action(env, run_id)

    assert seen["rounds"] == 2
    assert scheduler_runs.get_run(run_id)["rounds"] == 2


def test_a_failed_model_call_fails_the_run(env, monkeypatch):
    from app import scheduler_runner

    run_id = _run()
    monkeypatch.setattr(
        scheduler_runner, "_ask_model", lambda *a, **k: Turn(error="the model could not be called (Timeout)")
    )

    outcome = scheduler_runner.run_service_action(env, run_id)

    run = scheduler_runs.get_run(run_id)
    assert outcome["status"] == "failed"
    assert run["status"] == scheduler_runs.STATUS_FAILED
    assert "could not be called" in run["error"]


def test_a_deleted_item_cancels_the_run(env, monkeypatch):
    from app import scheduler_runner

    run_id = _run()
    item = scheduler_store.get_item(scheduler_runs.get_run(run_id)["item_id"])
    scheduler_store.delete_item(item["id"])
    monkeypatch.setattr(scheduler_runner, "_ask_model", lambda *a, **k: Turn(text="никогда"))

    outcome = scheduler_runner.run_service_action(env, run_id)

    assert outcome["status"] == "cancelled"


def test_a_run_that_outlives_its_budget_stops_before_the_next_round(env, monkeypatch):
    """The deadline is checked before a round, not enforced inside one."""
    import time as time_module

    from app import scheduler_runner

    run_id = _run()
    env.config["SCHEDULER_RUN_TIMEOUT_S"] = 1
    calls = {"rounds": 0}

    def slow_model(app, messages, tools, tool_choice, language):
        calls["rounds"] += 1
        time_module.sleep(1.1)
        return Turn(
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "get_current_time", "arguments": "{}"},
                }
            ]
        )

    monkeypatch.setattr(scheduler_runner, "_ask_model", slow_model)

    outcome = scheduler_runner.run_service_action(env, run_id)

    run = scheduler_runs.get_run(run_id)
    assert outcome["status"] == "failed"
    assert "time budget" in run["error"]
    assert calls["rounds"] == 1, "the second round must not be asked for"
