# tests/test_scheduler_runs.py
"""One row per firing: what the tick creates, what the worker claims, what is delivered."""

from datetime import UTC, datetime, timedelta

import pytest

from app import scheduler_runs, scheduler_store, userdb

pytestmark = pytest.mark.requires_db

LOGIN = "scheduler_runs_test"
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


@pytest.fixture
def env(test_app):
    with test_app.app_context():
        from app.database import get_db, init_db

        init_db()

        def clear():
            with get_db() as conn:
                c = conn.cursor()
                c.execute("DELETE FROM scheduler_runs WHERE user_id = %s", (LOGIN,))
                c.execute("DELETE FROM scheduler_items WHERE user_id = %s", (LOGIN,))
                c.execute("DELETE FROM users WHERE login = %s", (LOGIN,))

        clear()
        userdb.create_user(LOGIN, "pw", "Name", language="ru")
        yield test_app
        clear()


def _item():
    item_id = scheduler_store.create_item(
        LOGIN,
        kind="action",
        title="Rate",
        body="Find the rate",
        repeat="minutes",
        every_minutes=5,
        at=NOW,
        timezone_name="Europe/Moscow",
    )
    return scheduler_store.get_item(item_id)


def test_a_run_is_queued_and_delivered_once(env):
    item = _item()
    run_id = scheduler_runs.create_run(item["id"], LOGIN, NOW)

    assert scheduler_runs.has_open_run(item["id"]) is True
    assert scheduler_runs.due_runs(NOW) == []

    scheduler_runs.finish_run(run_id, scheduler_runs.STATUS_COMPLETED, result="76.4")
    assert scheduler_runs.has_open_run(item["id"]) is False

    due = scheduler_runs.due_runs(NOW + timedelta(seconds=1))
    assert [str(row["id"]) for row in due] == [run_id]

    scheduler_runs.mark_delivered(run_id, NOW)
    assert scheduler_runs.due_runs(NOW + timedelta(seconds=2)) == []


def test_an_open_run_blocks_the_next_firing(env):
    item = _item()
    scheduler_runs.create_run(item["id"], LOGIN, NOW)

    assert scheduler_runs.has_open_run(item["id"]) is True


def test_a_run_is_not_delivered_before_its_time(env):
    item = _item()
    run_id = scheduler_runs.create_run(item["id"], LOGIN, NOW + timedelta(minutes=10))
    scheduler_runs.finish_run(run_id, scheduler_runs.STATUS_COMPLETED, result="x")

    assert scheduler_runs.due_runs(NOW) == []


def test_a_queued_run_older_than_the_limit_is_overdue(env):
    item = _item()
    run_id = scheduler_runs.create_run(item["id"], LOGIN, NOW - timedelta(hours=2))

    overdue = scheduler_runs.overdue_runs(NOW, 60)

    assert [str(row["id"]) for row in overdue] == [run_id]


def test_cancelling_an_item_silences_its_runs(env):
    item = _item()
    run_id = scheduler_runs.create_run(item["id"], LOGIN, NOW)

    assert scheduler_runs.cancel_for_item(item["id"], NOW) == 1
    assert scheduler_runs.get_run(run_id)["status"] == scheduler_runs.STATUS_CANCELLED
    assert scheduler_runs.due_runs(NOW + timedelta(minutes=5)) == []


def test_a_restart_cancels_what_was_running(env):
    item = _item()
    run_id = scheduler_runs.create_run(item["id"], LOGIN, NOW)
    scheduler_runs.mark_running(run_id)

    assert scheduler_runs.recover_after_restart(NOW) == 1
    assert scheduler_runs.get_run(run_id)["status"] == scheduler_runs.STATUS_CANCELLED
    assert scheduler_runs.has_open_run(item["id"]) is False


def test_a_cancelled_run_is_not_revived_by_a_late_writer(env):
    """The tick cancels an overdue run; a worker finishing it afterwards must not undo that."""
    item = _item()
    run_id = scheduler_runs.create_run(item["id"], LOGIN, NOW)
    scheduler_runs.mark_running(run_id)
    scheduler_runs.finish_run(run_id, scheduler_runs.STATUS_CANCELLED, error="scheduler_late")

    changed = scheduler_runs.finish_run(
        run_id, scheduler_runs.STATUS_COMPLETED, result="76.4", from_statuses=(scheduler_runs.STATUS_RUNNING,)
    )

    run = scheduler_runs.get_run(run_id)
    assert changed is False
    assert run["status"] == scheduler_runs.STATUS_CANCELLED
    assert run["error"] == "scheduler_late"
    assert run["result"] is None
    # It is owed a notice ("cancelled"), never the answer the late writer carried.
    pending = scheduler_runs.due_runs(NOW + timedelta(minutes=5))
    assert [row["status"] for row in pending] == [scheduler_runs.STATUS_CANCELLED]


def test_a_finish_does_not_erase_the_reason_it_was_cancelled(env):
    item = _item()
    run_id = scheduler_runs.create_run(item["id"], LOGIN, NOW)
    scheduler_runs.mark_running(run_id)
    scheduler_runs.finish_run(run_id, scheduler_runs.STATUS_CANCELLED, error="scheduler_late")

    # The runner's own cancellation path carries no error string.
    scheduler_runs.finish_run(run_id, scheduler_runs.STATUS_CANCELLED, rounds=2)

    run = scheduler_runs.get_run(run_id)
    assert run["error"] == "scheduler_late"
    assert run["rounds"] == 2


def test_a_running_row_whose_worker_died_is_swept(env):
    """A transient failure after `running` would otherwise silence the item until a restart."""
    item = _item()
    run_id = scheduler_runs.create_run(item["id"], LOGIN, NOW)
    scheduler_runs.mark_running(run_id)
    with scheduler_runs.get_db() as conn:
        conn.cursor().execute(
            "UPDATE scheduler_runs SET started_at = %s - make_interval(secs => 4000) WHERE id = %s",
            (NOW, run_id),
        )

    assert scheduler_runs.sweep_stale_running(NOW, 1800) == 1
    run = scheduler_runs.get_run(run_id)
    assert run["status"] == scheduler_runs.STATUS_CANCELLED
    assert run["error"] == "run_timed_out"
    assert scheduler_runs.has_open_run(item["id"]) is False
