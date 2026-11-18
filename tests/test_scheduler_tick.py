# tests/test_scheduler_tick.py
"""The tick: one daemon thread, one lease, one firing per due item."""

from datetime import UTC, datetime, timedelta

import pytest

from app import scheduler, scheduler_store

pytestmark = pytest.mark.requires_db

LOGIN = "scheduler_tick_test"


@pytest.fixture
def env(test_app, monkeypatch):
    with test_app.app_context():
        from app.database import get_db, init_db

        init_db()

        def clear():
            with get_db() as conn:
                conn.cursor().execute("DELETE FROM scheduler_items WHERE user_id = %s", (LOGIN,))

        clear()
        delivered = []
        monkeypatch.setattr(scheduler, "deliver", lambda app, item, fired_at: delivered.append(item["id"]) or "msg-id")
        monkeypatch.setattr(scheduler, "_redis", lambda app: None)  # single process: no lease
        test_app.config["SCHEDULER_TICK_ENABLED"] = False
        yield {"app": test_app, "delivered": delivered}
        clear()


def _now() -> datetime:
    return datetime.now(UTC)


def test_a_due_reminder_is_delivered_once_and_advances(env):
    item_id = scheduler_store.create_item(
        LOGIN,
        kind="reminder",
        title="Отчёт",
        body="🔔 Подготовить {when}",
        repeat="daily",
        time_of_day=(_now() - timedelta(minutes=1)).time().replace(second=0, microsecond=0),
    )
    scheduler_store.update_item(item_id, next_run_at=_now() - timedelta(minutes=1))

    first = scheduler.tick(env["app"])
    second = scheduler.tick(env["app"])

    assert first["delivered"] == 1
    assert second["delivered"] == 0
    assert env["delivered"] == [item_id]
    assert scheduler_store.get_item(item_id)["next_run_at"] > _now()


def test_a_one_off_is_disabled_after_firing(env):
    item_id = scheduler_store.create_item(LOGIN, kind="reminder", title="Раз", body="🔔 {when}", repeat="once")
    scheduler_store.update_item(item_id, next_run_at=_now() - timedelta(minutes=1))

    scheduler.tick(env["app"])

    item = scheduler_store.get_item(item_id)
    assert item["enabled"] is False
    assert item["next_run_at"] is None


def test_a_future_item_is_not_touched(env):
    scheduler_store.create_item(LOGIN, kind="reminder", title="Позже", body="🔔 {when}", repeat="once")
    item_id = scheduler_store.list_items(LOGIN)[0]["id"]
    scheduler_store.update_item(item_id, next_run_at=_now() + timedelta(hours=1))

    assert scheduler.tick(env["app"])["delivered"] == 0


def test_the_lease_lets_one_tick_through(env, monkeypatch):
    """Two workers must not deliver the same item twice."""
    class _Busy:
        def set(self, *a, **kw):
            return None

        def delete(self, *a, **kw):
            return 1

    monkeypatch.setattr(scheduler, "_redis", lambda app: _Busy())
    item_id = scheduler_store.create_item(LOGIN, kind="reminder", title="Лиза", body="🔔 {when}", repeat="once")
    scheduler_store.update_item(item_id, next_run_at=_now() - timedelta(minutes=1))

    assert scheduler.tick(env["app"])["delivered"] == 0
    assert env["delivered"] == []
