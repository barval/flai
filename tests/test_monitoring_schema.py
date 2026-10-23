# tests/test_monitoring_schema.py
"""DDL order guards.

``init_db()`` is a linear script: a ``CREATE INDEX`` on a table that is created
further down fails the whole upgrade on a fresh database (and in CI), so the
order is asserted on the source instead of only on an already-migrated schema.
"""

from pathlib import Path

SOURCE = Path("app/database.py").read_text(encoding="utf-8")


def test_monitoring_indexes_come_after_their_tables():
    for table, index in (
        ("messages", "idx_messages_timestamp"),
        ("tasks", "idx_tasks_created_at"),
        ("documents", "idx_documents_uploaded_at"),
        ("monitoring_archive", "idx_monitoring_archive_user_time"),
    ):
        table_at = SOURCE.index(f"CREATE TABLE IF NOT EXISTS {table} (")
        index_at = SOURCE.index(f"CREATE INDEX IF NOT EXISTS {index}")
        assert index_at > table_at, f"{index} must be created after {table}"
