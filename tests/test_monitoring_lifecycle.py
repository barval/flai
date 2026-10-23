# tests/test_monitoring_lifecycle.py
"""Backups keep the monitoring history; account deletion erases it."""

from datetime import UTC, datetime

import pytest

from app.database import get_db
from app.monitoring.snapshots import write_snapshot
from app.routes.backups import FULL_TABLES
from app.userdb import create_user, delete_user

pytestmark = pytest.mark.requires_db


def test_full_backup_includes_monitoring_tables():
    assert "storage_snapshots" in FULL_TABLES
    assert "monitoring_archive" in FULL_TABLES


def test_delete_user_removes_snapshots_and_archive(test_app):
    with test_app.app_context():
        create_user("u1", "pw", "U1")
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO user_storage (user_id, used_bytes) VALUES ('u1', 100) "
                "ON CONFLICT (user_id) DO UPDATE SET used_bytes = EXCLUDED.used_bytes"
            )
            cursor.execute(
                "INSERT INTO monitoring_archive (user_id, kind, occurred_at) VALUES ('u1', 'message', now())"
            )
        write_snapshot(datetime(2020, 10, 23, 12, 5, tzinfo=UTC))

        assert delete_user("u1") is True

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS n FROM monitoring_archive WHERE user_id = 'u1'")
            assert cursor.fetchone()["n"] == 0
            cursor.execute("SELECT COUNT(*) AS n FROM storage_snapshots WHERE user_id = 'u1'")
            assert cursor.fetchone()["n"] == 0
