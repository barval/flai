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
    # Column order is the point: equality on status, then the selection's order.
    assert "(status, task_class, created_at)" in row["indexdef"]


def test_the_unused_full_text_column_is_gone(test_app):
    """`tsv` served ts_rank_cd; BM25 over lemmas replaced it and nothing reads it."""
    from app.database import get_db

    with test_app.app_context(), get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'rag_chunks' AND column_name = 'tsv'"
        )
        assert c.fetchone() is None
        c.execute("SELECT indexname FROM pg_indexes WHERE indexname = 'idx_rag_chunks_tsv'")
        assert c.fetchone() is None
