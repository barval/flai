# tests/test_monitoring_snapshots.py
"""Tests for the hourly storage snapshots."""

from datetime import UTC, datetime

import pytest

from app.database import get_db
from app.monitoring.snapshots import storage_series, write_snapshot

pytestmark = pytest.mark.requires_db

NOON = datetime(2020, 10, 23, 12, 5, tzinfo=UTC)
AFTERNOON = datetime(2020, 10, 23, 13, 5, tzinfo=UTC)


def _set_storage(login: str, used_bytes: int) -> None:
    with get_db() as conn:
        conn.cursor().execute(
            "INSERT INTO user_storage (user_id, used_bytes) VALUES (%s, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET used_bytes = EXCLUDED.used_bytes",
            (login, used_bytes),
        )


def test_snapshot_is_written_once_per_hour(test_app):
    with test_app.app_context():
        _set_storage("u1", 2048)

        assert write_snapshot(NOON) == 1
        assert write_snapshot(NOON.replace(minute=55)) == 0
        assert write_snapshot(AFTERNOON) == 1


def test_storage_series_carries_the_last_known_value_forward(test_app):
    with test_app.app_context():
        _set_storage("u1", 100)
        write_snapshot(datetime(2020, 10, 23, 10, 5, tzinfo=UTC))
        _set_storage("u1", 300)
        write_snapshot(NOON)
        starts = [datetime(2020, 10, 23, hour, 0, tzinfo=UTC) for hour in (9, 10, 11, 12)]

        values, since = storage_series(["u1"], starts, datetime(2020, 10, 23, 13, 0, tzinfo=UTC))

    assert values == [None, 100, 100, 300]
    assert since == datetime(2020, 10, 23, 10, 0, tzinfo=UTC)


def test_storage_series_without_logins_is_empty():
    starts = [datetime(2020, 10, 23, hour, 0, tzinfo=UTC) for hour in (9, 10)]

    assert storage_series([], starts, datetime(2020, 10, 23, 11, 0, tzinfo=UTC)) == ([None, None], None)
