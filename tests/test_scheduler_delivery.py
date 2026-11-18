# tests/test_scheduler_delivery.py
"""Where a reminder lands: its own chat, its own sentence, and a popup.

A reminder is a prepared sentence, so delivery is a database write and an event — no
model, no queue, nothing to wait for.
"""

import uuid
from datetime import UTC, datetime

import pytest

from app import db, scheduler, userdb

pytestmark = pytest.mark.requires_db

LOGIN = "scheduler_delivery_test"


class _Collector:
    def __init__(self):
        self.events = []

    def publish(self, user_id, event_type, data):
        self.events.append({"user_id": user_id, "type": event_type, "data": data})

    def of_type(self, event_type):
        return [event for event in self.events if event["type"] == event_type]


@pytest.fixture
def env(test_app):
    with test_app.app_context():
        from app.database import get_db, init_db

        init_db()

        def clear():
            with get_db() as conn:
                c = conn.cursor()
                c.execute(
                    "DELETE FROM messages WHERE session_id IN"
                    " (SELECT id FROM chat_sessions WHERE user_id = %s)",
                    (LOGIN,),
                )
                c.execute("DELETE FROM session_visits WHERE user_id = %s", (LOGIN,))
                c.execute("DELETE FROM chat_sessions WHERE user_id = %s", (LOGIN,))
                c.execute("DELETE FROM users WHERE login = %s", (LOGIN,))

        clear()
        userdb.create_user(LOGIN, "pw", "Name", language="ru")
        collector = _Collector()
        test_app.events_publisher = collector
        yield {"app": test_app, "publisher": collector}
        clear()


def _item(kind="reminder", **over):
    item = {
        "id": str(uuid.uuid4()),
        "user_id": LOGIN,
        "kind": kind,
        "title": "Report",
        "body": "🔔 Prepare the report — {when}",
        "repeat": "daily",
        "timezone": "Europe/Moscow",
        "next_run_at": datetime(2026, 11, 18, 6, 0, tzinfo=UTC),  # 09:00 in Moscow
        "enabled": True,
    }
    item.update(over)
    return item


def _session(kind="reminder"):
    with db.get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT * FROM chat_sessions WHERE user_id = %s AND scheduler_kind = %s",
            (LOGIN, kind),
        )
        return [dict(row) for row in c.fetchall()]


def _messages(session_id):
    return db.get_session_messages(session_id)


def test_a_reminder_opens_a_chat_named_for_reminders(env):
    scheduler.deliver(env["app"], _item(), datetime(2026, 11, 18, 6, 0, tzinfo=UTC))

    chats = _session("reminder")

    assert len(chats) == 1
    assert chats[0]["title"] == "🔔 Планировщик: напоминания"


def test_the_second_delivery_reuses_the_same_chat(env):
    item = _item()
    first = scheduler.deliver(env["app"], item, datetime(2026, 11, 18, 6, 0, tzinfo=UTC))
    scheduler.deliver(env["app"], item, datetime(2026, 11, 18, 6, 0, tzinfo=UTC))

    chats = _session("reminder")

    assert len(chats) == 1
    assert len(_messages(chats[0]["id"])) == 2
    assert first


def test_a_deleted_chat_is_recreated(env):
    item = _item()
    scheduler.deliver(env["app"], item, datetime(2026, 11, 18, 6, 0, tzinfo=UTC))
    old = _session("reminder")[0]["id"]
    db.delete_session_and_messages(old, LOGIN)

    scheduler.deliver(env["app"], item, datetime(2026, 11, 18, 6, 0, tzinfo=UTC))

    chats = _session("reminder")
    assert len(chats) == 1
    assert chats[0]["id"] != old
    assert len(_messages(chats[0]["id"])) == 1


def test_the_message_records_which_item_it_is(env):
    item = _item()
    message_id = scheduler.deliver(env["app"], item, datetime(2026, 11, 18, 6, 0, tzinfo=UTC))

    chat = _session("reminder")[0]
    message = _messages(chat["id"])[0]

    assert str(message["id"]) == str(message_id)
    assert message["role"] == "assistant"
    assert str(message["scheduler_item_id"]) == item["id"]
    assert message["scheduler_kind"] == "reminder"


def test_the_placeholder_is_filled_with_the_scheduled_time(env):
    """The sentence was written when the item was created; only the time is new."""
    scheduler.deliver(env["app"], _item(), datetime(2026, 11, 18, 6, 0, tzinfo=UTC))

    content = _messages(_session("reminder")[0]["id"])[0]["content"]

    assert content == "🔔 Prepare the report — 18 ноября, 09:00"


def test_a_reminder_pops_up(env):
    item = _item()
    scheduler.deliver(env["app"], item, datetime(2026, 11, 18, 6, 0, tzinfo=UTC))

    popups = env["publisher"].of_type("scheduler_popup")

    assert len(popups) == 1
    assert popups[0]["user_id"] == LOGIN
    assert str(popups[0]["data"]["item_id"]) == item["id"]
    assert popups[0]["data"]["session_id"] == _session("reminder")[0]["id"]
    assert "18 ноября, 09:00" in popups[0]["data"]["text"]


def test_an_action_does_not_pop_up(env):
    """An action's result is a message; only a reminder is an interruption."""
    item = _item(kind="action", body="Check the rooms")

    scheduler.deliver_message(env["app"], item, "Checked the rooms at 10:00")

    assert env["publisher"].of_type("scheduler_popup") == []
    assert _session("action")[0]["title"] == "⚙️ Планировщик: действия"


def test_the_item_zone_survives_a_server_in_another_one(env):
    """The time is the item's, not the process's: the zone is a snapshot."""
    utc_item = _item(timezone="UTC")
    scheduler.deliver(env["app"], utc_item, datetime(2026, 11, 18, 6, 0, tzinfo=UTC))

    content = _messages(_session("reminder")[0]["id"])[0]["content"]

    assert "06:00" in content
