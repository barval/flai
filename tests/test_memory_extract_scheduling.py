# tests/test_memory_extract_scheduling.py
"""Memory extraction is the lowest-priority job: chat preempts it."""

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def _queue():
    """A queue without __init__ (it would need a live Redis)."""
    from app.queue import RedisRequestQueue

    app = MagicMock()
    app.config = {"MEMORY_EXTRACT_QUIET_S": 45}
    app.logger = MagicMock()
    app.modules = {}
    queue = RedisRequestQueue.__new__(RedisRequestQueue)
    queue.app = app
    queue.logger = MagicMock()
    queue.redis = MagicMock()
    queue.queue_key = "request_queue"
    queue.slow_queue_key = "slow_request_queue"
    queue.background_queue_key = "background_queue"
    return queue, queue.redis


def test_a_chat_request_raises_the_interrupt_flag():
    queue, redis = _queue()
    with patch("app.tasks.gate.chat_activity_now"):
        queue._mark_chat_activity({"type": "text", "data": {"type": "text"}})

    redis.set.assert_called_with(queue.MEMORY_EXTRACT_INTERRUPT_KEY, "1", ex=45)


def test_idle_work_does_not_raise_the_interrupt_flag():
    """A merge task is not a user request and must not keep itself waiting."""
    queue, redis = _queue()
    with patch("app.tasks.gate.chat_activity_now"):
        queue._mark_chat_activity({"type": "fact_merge_task", "data": {"type": "fact_merge_task"}})

    redis.set.assert_not_called()


def test_gate_closes_while_a_chat_request_is_recent():
    queue, redis = _queue()
    redis.llen.side_effect = [3, 0, 0]  # background, fast, slow
    redis.exists.return_value = 1

    assert queue.background_gate_open() is False


def test_gate_closes_when_a_user_task_is_queued():
    queue, redis = _queue()
    redis.llen.side_effect = [3, 0, 1]  # a slow-queue task is waiting
    redis.exists.return_value = 0

    assert queue.background_gate_open() is False


def test_gate_opens_when_everything_is_idle():
    queue, redis = _queue()
    redis.llen.side_effect = [3, 0, 0]
    redis.exists.return_value = 0

    assert queue.background_gate_open() is True


def test_gate_is_closed_when_there_is_nothing_to_do():
    queue, redis = _queue()
    redis.llen.side_effect = [0, 0, 0]
    redis.exists.return_value = 0

    assert queue.background_gate_open() is False
