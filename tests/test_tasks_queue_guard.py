# tests/test_tasks_queue_guard.py
"""Tests for the slow-worker gate guard for Tasks-subsystem subtask requests.

Task 8a: tagged subtasks (data.task_step_subtask, user_class >= 3) dequeued by
the slow worker are deferred to ``task:deferred_subtasks`` while the scheduler
gate is closed (chat active / GPU busy / queues occupied); the executor calls
``requeue_deferred_subtasks()`` once per cycle to move them back onto the slow
queue when the gate opens. Untagged tasks and low user classes never hit the
guard; any guard failure is fail-open (the task is processed normally).
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from app.queue import RedisRequestQueue
from app.tasks.executor import DEFERRED_SUBTASKS_KEY
from app.tasks.gate import LAST_CHAT_KEY


class FakeGuardRedis:
    """Minimal Redis double: strings + lists + a pipeline stub."""

    def __init__(self):
        self.data = {}
        self.lists = {}
        pipe = MagicMock()
        pipe.execute.return_value = [1, 1, 1]
        self._pipe = pipe

    def set(self, key, value):
        self.data[key] = value

    def get(self, key):
        return self.data.get(key)

    def llen(self, key):
        return len(self.lists.get(key, []))

    def hlen(self, key):
        return 0

    def delete(self, key):
        self.data.pop(key, None)
        self.lists.pop(key, None)

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    def lpop(self, key):
        lst = self.lists.get(key)
        return lst.pop(0) if lst else None

    def pipeline(self):
        return self._pipe


def _make_queue():
    """Build RedisRequestQueue with a fake Redis and no workers (test_queue.py pattern)."""
    fake = FakeGuardRedis()
    app = MagicMock()
    app.config = {"REDIS_URL": "redis://localhost:6379/0", "SECRET_KEY": "test-secret-key"}
    with patch("app.queue.redis.from_url", return_value=fake):
        queue = RedisRequestQueue(app, start_workers=False)
    return queue, fake


def _subtask_task(user_class=3, step_id="step-1", task_id="task-1"):
    """A minimal slow-queue media sub-task tagged for the Tasks subsystem."""
    return {
        "id": task_id,
        "user_id": "tester",
        "session_id": "sess-1",
        "data": {"type": "image", "text": "draw a cat", "task_step_subtask": step_id},
        "user_class": user_class,
        "lang": "ru",
        "timestamp": time.time(),
    }


@pytest.mark.unit
class TestSlowWorkerSubtaskGuard:
    """_defer_subtask_if_gate_closed: tagged tasks wait while the gate is closed."""

    @pytest.fixture(autouse=True)
    def _stub_resource_manager(self):
        """get_resource_manager() must not run hardware detection in tests."""
        rm = MagicMock()
        rm.is_gpu_busy = lambda: False
        with patch("app.resource_manager.get_resource_manager", return_value=rm):
            yield rm

    def test_guard_defers_tagged_task_when_gate_closed(self):
        """Slow worker pushes tagged task to task:deferred_subtasks instead of running it."""
        queue, fake = _make_queue()
        fake.data[LAST_CHAT_KEY] = str(time.time())  # fresh chat activity → gate closed
        task = _subtask_task()
        assert queue._defer_subtask_if_gate_closed(task) is True
        deferred = fake.lists[DEFERRED_SUBTASKS_KEY]
        assert len(deferred) == 1
        # The deferred payload round-trips through the HMAC serializer.
        restored = queue._deserialize(deferred[0])
        assert restored["id"] == "task-1"
        assert restored["data"]["task_step_subtask"] == "step-1"

    def test_guard_untouched_for_chat(self):
        """Untagged tasks never hit the guard (chat NEVER defers)."""
        queue, fake = _make_queue()
        fake.data[LAST_CHAT_KEY] = str(time.time())
        chat_task = {
            "id": "chat-1",
            "user_id": "tester",
            "session_id": "sess-1",
            "data": {"type": "image", "text": "draw a cat"},
            "user_class": 3,
            "lang": "ru",
            "timestamp": time.time(),
        }
        assert queue._defer_subtask_if_gate_closed(chat_task) is False
        assert DEFERRED_SUBTASKS_KEY not in fake.lists

    def test_guard_fail_open_on_gate_error(self):
        """A failing gate check processes the task normally (fail-open)."""
        queue, fake = _make_queue()
        fake.data[LAST_CHAT_KEY] = str(time.time())
        with patch("app.tasks.gate.is_gate_open", side_effect=RuntimeError("gate boom")):
            assert queue._defer_subtask_if_gate_closed(_subtask_task()) is False
        assert DEFERRED_SUBTASKS_KEY not in fake.lists

    def test_guard_not_applied_low_class(self):
        """Tagged tasks below user_class 3 are processed without deferral."""
        queue, fake = _make_queue()
        fake.data[LAST_CHAT_KEY] = str(time.time())
        assert queue._defer_subtask_if_gate_closed(_subtask_task(user_class=0)) is False
        assert DEFERRED_SUBTASKS_KEY not in fake.lists


@pytest.mark.unit
class TestRequeueDeferredSubtasks:
    """requeue_deferred_subtasks: gate open moves deferred tasks to the slow queue."""

    @pytest.fixture(autouse=True)
    def _stub_resource_manager(self):
        """get_resource_manager() must not run hardware detection in tests."""
        rm = MagicMock()
        rm.is_gpu_busy = lambda: False
        with patch("app.resource_manager.get_resource_manager", return_value=rm):
            yield rm

    def test_requeue_deferred_moves_to_slow_queue(self):
        """Gate open: every deferred sub-task is LPOPed onto the slow queue in order."""
        queue, fake = _make_queue()
        fake.data[LAST_CHAT_KEY] = str(time.time() - 61)  # quiet window expired → gate open
        fake.lists[DEFERRED_SUBTASKS_KEY] = [
            queue._serialize(_subtask_task(task_id="t1", step_id="s1")),
            queue._serialize(_subtask_task(task_id="t2", step_id="s2")),
        ]
        assert queue.requeue_deferred_subtasks() == 2
        slow = fake.lists[queue.slow_queue_key]
        assert len(slow) == 2
        assert queue._deserialize(slow[0])["id"] == "t1"
        assert queue._deserialize(slow[1])["id"] == "t2"
        assert fake.llen(DEFERRED_SUBTASKS_KEY) == 0

    def test_requeue_deferred_gate_closed_returns_zero(self):
        """Gate closed: the deferred list is left untouched and 0 is returned."""
        queue, fake = _make_queue()
        fake.data[LAST_CHAT_KEY] = str(time.time())  # gate closed
        fake.lists[DEFERRED_SUBTASKS_KEY] = [queue._serialize(_subtask_task())]
        assert queue.requeue_deferred_subtasks() == 0
        assert queue.slow_queue_key not in fake.lists
        assert fake.llen(DEFERRED_SUBTASKS_KEY) == 1
