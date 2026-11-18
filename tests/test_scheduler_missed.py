# tests/test_scheduler_missed.py
"""What happens to firings that happened while nobody was watching.

A daily reminder missed for a week must not deliver seven messages, and an
every-fifteen-minutes one must not deliver six hundred and seventy-two. The policy is
per kind and lives in the admin panel, because a reminder and an action deserve
different answers: one is a sentence, the other is work.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app import db, scheduler, scheduler_store, userdb

pytestmark = pytest.mark.requires_db

LOGIN = "scheduler_missed_test"
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


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
        userdb.create_user(LOGIN, "pw", "Name", service_class=2, language="ru")
        monkeypatch.setattr(scheduler, "_redis", lambda app: None)
        test_app.events_publisher = _Collector()
        test_app.config["SCHEDULER_TICK_ENABLED"] = False
        yield {"app": test_app}
        clear()


def _every_15_minutes(missed=4):
    """An item whose firings were last seen ``missed`` quarter-hours ago."""
    item_id = scheduler_store.create_item(
        LOGIN,
        kind="reminder",
        title="Report",
        body="🔔 Report — {when}",
        repeat="minutes",
        every_minutes=15,
        at=NOW - timedelta(minutes=15 * missed),
        timezone_name="Europe/Moscow",
    )
    return item_id


def _messages():
    with db.get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id FROM chat_sessions WHERE user_id = %s AND scheduler_kind = 'reminder'",
            (LOGIN,),
        )
        rows = [dict(row) for row in c.fetchall()]
    if not rows:
        return []
    return db.get_session_messages(rows[0]["id"])


@pytest.mark.parametrize(
    ("policy", "expected_messages"),
    [("all", 5), ("last", 1), ("none", 0)],
)
def test_the_policy_decides_how_many_missed_firings_arrive(env, policy, expected_messages):
    env["app"].config["SCHEDULER_MISSED_REMINDER"] = policy
    _every_15_minutes(missed=4)  # 4 missed firings plus the one due now

    counts = scheduler.tick(env["app"], now=NOW)

    assert len(_messages()) == expected_messages
    assert counts["delivered"] == expected_messages


def test_last_names_how_many_were_skipped(env):
    env["app"].config["SCHEDULER_MISSED_REMINDER"] = "last"
    _every_15_minutes(missed=4)

    scheduler.tick(env["app"], now=NOW)

    content = _messages()[0]["content"]
    assert "Пропущено срабатываний: 4" in content
    assert content.rstrip().endswith("15:00")  # Moscow, the item's own clock


def test_all_delivers_each_firing_with_its_own_time(env):
    env["app"].config["SCHEDULER_MISSED_REMINDER"] = "all"
    _every_15_minutes(missed=2)

    scheduler.tick(env["app"], now=NOW)

    contents = [message["content"] for message in _messages()]
    assert len(contents) == 3
    assert contents[0].rstrip().endswith("14:30")
    assert contents[-1].rstrip().endswith("15:00")


def test_none_still_advances_the_item(env):
    env["app"].config["SCHEDULER_MISSED_REMINDER"] = "none"
    item_id = _every_15_minutes(missed=4)

    scheduler.tick(env["app"], now=NOW)

    item = scheduler_store.get_item(item_id)
    assert _messages() == []
    assert item["next_run_at"] > NOW


def test_all_past_the_cap_behaves_as_last_and_says_so(env):
    env["app"].config["SCHEDULER_MISSED_REMINDER"] = "all"
    env["app"].config["SCHEDULER_CATCHUP_MAX"] = 3
    item_id = _every_15_minutes(missed=10)

    scheduler.tick(env["app"], now=NOW)

    messages = _messages()
    assert len(messages) == 1
    assert "Пропущено срабатываний: 10" in messages[0]["content"]
    assert scheduler_store.get_item(item_id)["next_run_at"] > NOW


def test_an_action_catches_up_once_and_not_with_a_pile(env):
    env["app"].config["SCHEDULER_MISSED_ACTION"] = "all"
    item_id = scheduler_store.create_item(
        LOGIN,
        kind="action",
        title="Rooms",
        body="Check every room",
        repeat="minutes",
        every_minutes=15,
        at=NOW - timedelta(minutes=45),
        timezone_name="Europe/Moscow",
    )

    scheduler.tick(env["app"], now=NOW)

    with db.get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM scheduler_runs WHERE item_id = %s", (item_id,))
        runs = [dict(row) for row in c.fetchall()]
    assert len(runs) == 1
    # The newest missed firing, not the oldest: work cannot be run four times over.
    assert runs[0]["due_at"] == NOW


def test_a_missed_action_can_be_skipped_entirely(env):
    env["app"].config["SCHEDULER_MISSED_ACTION"] = "none"
    item_id = scheduler_store.create_item(
        LOGIN,
        kind="action",
        title="Rooms",
        body="Check every room",
        repeat="minutes",
        every_minutes=15,
        at=NOW - timedelta(minutes=45),
        timezone_name="Europe/Moscow",
    )

    scheduler.tick(env["app"], now=NOW)

    with db.get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT count(*) AS n FROM tasks WHERE scheduler_item_id = %s", (item_id,))
        assert c.fetchone()["n"] == 0
    assert scheduler_store.get_item(item_id)["next_run_at"] > NOW


def test_the_settings_group_owns_the_policy(env):
    from app.settings_registry import GROUPS, build_schema

    assert "scheduler" in GROUPS
    keys = GROUPS["scheduler"][1]
    assert "SCHEDULER_MISSED_REMINDER" in keys
    assert "SCHEDULER_MISSED_ACTION" in keys

    schema = {field["key"]: field for group in build_schema(env["app"]) for field in group["fields"] if group["id"] == "scheduler"}
    assert schema["SCHEDULER_MISSED_REMINDER"]["value"] == "last"
    assert schema["SCHEDULER_MISSED_ACTION"]["kind"] == "str"


def test_an_unknown_policy_falls_back_to_last(env):
    env["app"].config["SCHEDULER_MISSED_REMINDER"] = "sometimes"
    _every_15_minutes(missed=4)

    scheduler.tick(env["app"], now=NOW)

    assert len(_messages()) == 1


def test_a_firing_inside_the_grace_period_is_not_missed(env):
    env["app"].config["SCHEDULER_MISSED_REMINDER"] = "none"
    scheduler_store.create_item(
        LOGIN,
        kind="reminder",
        title="Now",
        body="🔔 Now, {when}",
        repeat="once",
        at=NOW - timedelta(seconds=40),
        timezone_name="Europe/Moscow",
    )

    scheduler.tick(env["app"], now=NOW)

    assert len(_messages()) == 1  # on time, so the policy does not apply
