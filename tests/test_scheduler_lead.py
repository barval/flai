"""How early an action is queued, when the schedule has measured itself.

A constant assumes every check costs the same. Measured, they do not: a probe that takes
four minutes is queued five minutes early by one rule and too late by the other. The lead
is the median of the item's own completed runs plus a margin, with the configured value as
the floor and the answer when there is nothing to measure.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app import scheduler_store, userdb
from app.scheduler import action_lead_minutes

pytestmark = pytest.mark.requires_db

LOGIN = "sched_lead_test"
ZONE = "Europe/Moscow"


@pytest.fixture
def env(test_app, monkeypatch):
    with test_app.app_context():
        from app.database import get_db, init_db

        init_db()

        def clear():
            with get_db() as conn:
                c = conn.cursor()
                c.execute("DELETE FROM scheduler_items WHERE user_id = %s", (LOGIN,))
                c.execute("DELETE FROM users WHERE login = %s", (LOGIN,))

        clear()
        userdb.create_user(LOGIN, "pw", "Name", language="ru")
        monkeypatch.setitem(test_app.config, "TIMEZONE_STR", ZONE)
        yield test_app
        clear()


def _item(**over):
    repeat = over.get("repeat", "daily")
    every = over.get("every_minutes")
    item_id = scheduler_store.create_item(
        LOGIN,
        kind="action",
        title="Check",
        body="Check",
        repeat=repeat,
        every_minutes=every,
        at=datetime.now(UTC) + timedelta(hours=2),
        timezone_name=ZONE,
    )
    return scheduler_store.get_item(item_id)


def _run(item_id, seconds, status="completed"):
    from app.database import get_db

    with get_db() as conn:
        conn.cursor().execute(
            """
            INSERT INTO scheduler_runs (id, item_id, user_id, status, due_at, started_at, finished_at)
            VALUES (%s, %s, %s, %s, now(), now() - make_interval(secs => %s), now())
            """,
            (str(uuid.uuid4()), item_id, LOGIN, status, seconds),
        )


def test_without_history_the_configured_lead_is_used(env):
    item = _item()

    assert action_lead_minutes(item, 1) == 1


def test_the_lead_follows_the_median_duration(env):
    item = _item()
    for seconds in (240, 300, 600):
        _run(item["id"], seconds)

    # median 300 s + 30 s margin = 330 s, rounded up to whole minutes
    assert action_lead_minutes(item, 1) == 6


def test_the_measured_lead_never_exceeds_the_ceiling(env):
    item = _item()
    _run(item["id"], 3600)

    assert action_lead_minutes(item, 1) == 10


def test_a_frequent_action_still_never_leads_more_than_half_its_interval(env):
    item = _item(repeat="minutes", every_minutes=15)
    _run(item["id"], 600)

    assert action_lead_minutes(item, 1) == 7


def test_only_completed_runs_are_measured(env):
    item = _item()
    _run(item["id"], 3600, status="failed")

    assert action_lead_minutes(item, 1) == 1


def test_the_switch_turns_the_measurement_off(env, test_app):
    item = _item()
    _run(item["id"], 600)
    test_app.config["SCHEDULER_ADAPTIVE_LEAD"] = False

    assert action_lead_minutes(item, 3) == 3
