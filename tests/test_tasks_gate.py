# tests/test_tasks_gate.py
"""Tests for the Tasks scheduler gate: chat activity signal + gate predicate."""

import time
from unittest.mock import MagicMock, patch

import pytest

from app.queue import RedisRequestQueue
from app.tasks.gate import LAST_CHAT_KEY, chat_activity_now, is_gate_open


class FakeGateRedis:
    """Minimal Redis double: set/get/llen/hlen/delete + a pipeline for add_request."""

    def __init__(self):
        self.data = {}
        pipe = MagicMock()
        pipe.execute.return_value = [1, 1, 1]
        self._pipe = pipe

    def set(self, key, value):
        self.data[key] = value

    def get(self, key):
        return self.data.get(key)

    def llen(self, key):
        return 0

    def hlen(self, key):
        return 0

    def delete(self, key):
        self.data.pop(key, None)

    def pipeline(self):
        return self._pipe


@pytest.mark.unit
class TestChatActivitySignal:
    """chat_activity_now writes the sliding-window timestamp."""

    def test_writes_timestamp(self):
        r = FakeGateRedis()
        before = time.time()
        chat_activity_now(r)
        written = float(r.data[LAST_CHAT_KEY])
        assert before - 1 <= written <= time.time() + 1

    def test_sliding_window_closes_gate(self):
        r = FakeGateRedis()
        chat_activity_now(r)
        assert not is_gate_open(r, quiet_s=60, gpu_busy=lambda: False, queues_empty=lambda: True)

    def test_sliding_window_opens_after_quiet(self):
        r = FakeGateRedis()
        r.data[LAST_CHAT_KEY] = str(time.time() - 61)
        assert is_gate_open(r, quiet_s=60, gpu_busy=lambda: False, queues_empty=lambda: True)

    def test_no_activity_recorded_opens_gate(self):
        r = FakeGateRedis()
        assert is_gate_open(r, quiet_s=60, gpu_busy=lambda: False, queues_empty=lambda: True)


@pytest.mark.unit
class TestGatePredicate:
    """gpu_busy and queues_empty callables close the gate regardless of quiet time."""

    def test_gpu_busy_closes_gate(self):
        r = FakeGateRedis()
        r.data[LAST_CHAT_KEY] = str(time.time() - 999)
        assert not is_gate_open(r, quiet_s=60, gpu_busy=lambda: True, queues_empty=lambda: True)

    def test_occupied_queues_close_gate(self):
        r = FakeGateRedis()
        r.data[LAST_CHAT_KEY] = str(time.time() - 999)
        assert not is_gate_open(r, quiet_s=60, gpu_busy=lambda: False, queues_empty=lambda: False)


def _make_queue():
    """Build RedisRequestQueue with a fake Redis and no workers (test_queue.py pattern)."""
    fake = FakeGateRedis()
    app = MagicMock()
    app.config = {"REDIS_URL": "redis://localhost:6379/0", "SECRET_KEY": "test-secret-key"}
    with patch("app.queue.redis.from_url", return_value=fake):
        queue = RedisRequestQueue(app, start_workers=False)
    return queue, fake


@pytest.mark.unit
class TestAddRequestChatActivityHook:
    """add_request records chat activity for chat-bearing types only."""

    def test_chat_text_marks_activity(self):
        queue, fake = _make_queue()
        queue.add_request("user1", "sess1", {"type": "text", "text": "hi"}, 2)
        assert LAST_CHAT_KEY in fake.data

    def test_image_chat_marks_activity(self):
        queue, fake = _make_queue()
        queue.add_request("user1", "sess1", {"type": "image", "text": "look"}, 2)
        assert LAST_CHAT_KEY in fake.data

    def test_requeued_reasoning_marks_activity(self):
        queue, fake = _make_queue()
        queue.add_request("user1", "sess1", {"type": "reasoning_task", "text": "think"}, 2)
        assert LAST_CHAT_KEY in fake.data

    def test_api_embedding_does_not_mark(self):
        queue, fake = _make_queue()
        queue.add_request("user1", "api", {"type": "api_embedding", "input": ["x"]}, 2)
        assert LAST_CHAT_KEY not in fake.data

    def test_api_transcribe_does_not_mark(self):
        queue, fake = _make_queue()
        queue.add_request("user1", "api", {"type": "api_transcribe", "file_data": "x"}, 2)
        assert LAST_CHAT_KEY not in fake.data

    def test_background_task_does_not_mark(self):
        queue, fake = _make_queue()
        queue.add_request("user1", "system", {"user_id": "u"}, 2, task_type="fact_merge_task")
        assert LAST_CHAT_KEY not in fake.data

    def test_task_step_subtask_does_not_mark(self):
        queue, fake = _make_queue()
        queue.add_request("user1", "sess1", {"type": "image_gen", "text": "draw", "task_step_subtask": "s1"}, 2)
        assert LAST_CHAT_KEY not in fake.data

    def test_redis_failure_is_swallowed(self):
        queue, fake = _make_queue()

        def boom(key, value):
            raise RuntimeError("redis down")

        fake.set = boom
        task_id, info = queue.add_request("user1", "sess1", {"type": "text", "text": "hi"}, 2)
        assert task_id
        assert info["queue_type"] == "fast"
