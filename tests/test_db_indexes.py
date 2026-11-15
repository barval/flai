"""Indexes the queues and the executor depend on."""

import pytest

pytestmark = pytest.mark.requires_db


def test_the_task_queue_poll_has_an_index(test_app):
    from app.database import get_db

    with test_app.app_context(), get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_tasks_queue'")
        row = c.fetchone()

    assert row is not None
    assert "status" in row["indexdef"]
    assert "task_class" in row["indexdef"]
