# tests/test_tasks_queue_guard.py
"""Tests for the slow-worker gate guard for Tasks-subsystem subtask requests.

Task 8a: tagged subtasks (data.task_step_subtask, user_class >= 3) dequeued by
the slow worker are deferred to ``task:deferred_subtasks`` while the scheduler
gate is closed (chat active / GPU busy / queues occupied); the executor calls
``requeue_deferred_subtasks()`` once per cycle to move them back onto the slow
queue when the gate opens. Untagged tasks and low user classes never hit the
guard; any guard failure is fail-open (the task is processed normally).
"""

import os
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

import app.tasks.executor as ex
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

    def test_guard_allows_tagged_task_when_gate_open(self):
        """8b (8a review finding 1): gate open ⇒ a tagged task is processed normally."""
        queue, fake = _make_queue()
        fake.data[LAST_CHAT_KEY] = str(time.time() - 61)  # quiet window expired
        assert queue._defer_subtask_if_gate_closed(_subtask_task()) is False
        assert DEFERRED_SUBTASKS_KEY not in fake.lists


# ── Task 8b: subtask completion polling (executor) ────────────────────────

_db_available = (
    bool(os.environ.get("DATABASE_URL")) and os.environ["DATABASE_URL"] != "postgresql://localhost:5432/flai_test"
)


def _db_marker():
    pass


def _mk_task(store, **kwargs):
    """Reuse test_tasks_executor's task seeder (same DB-backed semantics)."""
    from tests.test_tasks_executor import _db_available  # noqa: F401 — sentinel shared
    from tests.test_tasks_executor import _mk_task as mk

    return mk(store, **kwargs)


def _cycle(test_app, gate=True):
    """Drive one executor cycle manually (no thread)."""
    executor = ex.TaskExecutor(app=test_app)
    executor._process_one_cycle(gate_open=gate)
    return executor


@pytest.mark.unit
class TestSubtaskPolling:
    """Executor polls a queued subtask's result and resumes the step loop."""

    @pytest.fixture
    def poll_env(self, test_app, monkeypatch):
        """DB-backed executor env (no thread) + a stub queue recording calls."""
        if not _db_available:
            pytest.skip("No reachable PostgreSQL for tasks polling tests")
        from app.database import get_db, init_db

        try:
            init_db()
            with get_db() as conn:
                c = conn.cursor()
                c.execute("SELECT id FROM tasks LIMIT 1")
                c.fetchone()
        except Exception as exc:
            pytest.skip(f"PostgreSQL unreachable: {exc}")

        from app.tasks import store

        with get_db() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM task_steps")
            c.execute("DELETE FROM tasks")
        # No Redis flag is ever set in these tests (the shared mock answers None).
        test_app.request_queue.redis.get.return_value = None

        # Stop the create_app()-started singleton so manual drives are deterministic.
        ex.stop_task_executor(timeout=2)
        with ex._executor_lock:
            ex._EXECUTOR = None

        calls = {"requeue": 0, "cancel": []}

        class _Q:
            redis = MagicMock()

            def requeue_deferred_subtasks(self):
                calls["requeue"] += 1
                return 0

            def cancel_task(self, task_id):
                calls["cancel"].append(task_id)
                return True

            def _is_task_cancelled(self, task_id):
                return False

        monkeypatch.setattr(ex, "_get_request_queue", lambda: _Q())
        yield test_app, calls, store

        with get_db() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM task_steps")
            c.execute("DELETE FROM tasks")

    @staticmethod
    def _seed_subtask(store_mod, tool="image_gen", status="subtask_queued", queue_id="sub-1"):
        """Task with a one-subtask-step plan whose step row carries a queue id."""
        plan = {"title": "Media", "steps": [{"seq": 1, "goal": "Make an image", "tool": tool}]}
        task_id = _mk_task(store_mod, step_plan=plan)
        row_id = store_mod.add_step(task_id=task_id, seq=1, kind="subtask", goal="Make an image", tool=tool)
        store_mod.update_step(row_id, status=status, extra={"subtask_queue_id": queue_id})
        return task_id, row_id

    def test_deferred_requeue_wired_into_cycle(self, poll_env):
        """_process_one_cycle calls requeue_deferred_subtasks before selection."""
        test_app, calls, store = poll_env
        ex.TaskExecutor(app=test_app)._process_one_cycle(gate_open=True)
        assert calls["requeue"] >= 1, "requeue must be invoked once per cycle"

    def test_subtask_poll_ready(self, poll_env, monkeypatch):
        """Result ready ⇒ step passed, result stored, index advances, budget grows."""
        test_app, calls, store = poll_env
        task_id, row_id = self._seed_subtask(store)
        # started_at 2s in the past so the wall-clock budget approximation > 0.
        store.update_step(row_id, started_at=(datetime.now(UTC) - timedelta(seconds=2)).isoformat())
        monkeypatch.setattr(ex, "_run_final_summary", lambda *a, **k: "final summary text")

        executor = ex.TaskExecutor(app=test_app)
        monkeypatch.setattr(
            executor,
            "_peek_subtask_result",
            lambda queue_id: {"response": "image done", "file_path": "/uploads/x.png"},
        )
        executor._process_one_cycle(gate_open=True)

        task_row = store.get_task(task_id)
        assert task_row["next_step_index"] == 1, "poll consumed the ready subtask"
        assert task_row["accumulated_exec_seconds"] > 0, "budget grew by the subtask approximation"
        step = store.get_step(row_id)
        assert step["status"] == "passed"
        # The artifact line is appended to the result text (the verifier judges
        # text only) — the bare file_path when the envelope carries no file_name.
        assert step["result"] == "image done\n/uploads/x.png"
        assert step["extra"].get("media", {}).get("file_path") == "/uploads/x.png"
        assert task_row["status"] == "completed", "final step ran after the subtask passed"

    def test_subtask_poll_error(self, poll_env, monkeypatch):
        """Error result ⇒ step failed (attempts untouched), task continues."""
        test_app, calls, store = poll_env
        task_id, row_id = self._seed_subtask(store)
        monkeypatch.setattr(ex, "_run_final_summary", lambda *a, **k: "final summary text")

        executor = ex.TaskExecutor(app=test_app)
        monkeypatch.setattr(
            executor,
            "_peek_subtask_result",
            lambda queue_id: {"status": "error", "error": "⚠️ Image generation timeout"},
        )
        executor._process_one_cycle(gate_open=True)

        task_row = store.get_task(task_id)
        assert task_row["next_step_index"] == 1, "a failed subtask advances the loop"
        step = store.get_step(row_id)
        assert step["status"] == "failed"
        assert "Image generation timeout" in (step["last_error"] or "")
        assert int(step["attempts"] or 0) == 0, "a queue failure is a tool error, not an attempt"
        assert task_row["status"] == "completed", "task continues to the final summary"

    def test_subtask_poll_not_ready_then_ready(self, poll_env, monkeypatch):
        """Not-ready poll flips the step to subtask_running; a later poll completes it."""
        test_app, calls, store = poll_env
        task_id, row_id = self._seed_subtask(store)
        monkeypatch.setattr(ex, "_run_final_summary", lambda *a, **k: "final summary text")

        executor = ex.TaskExecutor(app=test_app)
        states = iter([None, None, {"response": "done"}])
        monkeypatch.setattr(executor, "_peek_subtask_result", lambda queue_id: next(states))

        # Cycle 1: not ready → step subtask_running, index held.
        executor._process_one_cycle(gate_open=True)
        step = store.get_step(row_id)
        assert step["status"] == "subtask_running", "first poll flips queued → running"
        t = store.get_task(task_id)
        assert t["status"] == "active" and t["next_step_index"] == 0, "polling holds the index"

        # Cycle 2 (still not ready → poll budget exhausted), cycle 3: ready.
        executor._process_one_cycle(gate_open=True)
        executor._process_one_cycle(gate_open=True)
        step = store.get_step(row_id)
        assert step["status"] == "passed" and step["result"] == "done"
        assert store.get_task(task_id)["next_step_index"] == 1

    def test_subtask_poll_cancel_flag(self, poll_env, monkeypatch):
        """Step-cancel flag ⇒ queue task cancelled, step interrupted/chat, task frozen."""
        test_app, calls, store = poll_env
        task_id, row_id = self._seed_subtask(store)

        # The shared conftest mock answers None; give it a real flag response.
        test_app.request_queue.redis.get.side_effect = lambda key: 1 if "step_cancel" in key else None
        started = datetime.now(UTC) - timedelta(seconds=2)
        store.update_step(row_id, status="subtask_running", started_at=started)

        executor = ex.TaskExecutor(app=test_app)
        monkeypatch.setattr(executor, "_peek_subtask_result", lambda queue_id: None)
        executor._process_one_cycle(gate_open=True)

        assert calls["cancel"] == ["sub-1"], "the queue task was cancelled"
        step = store.get_step(row_id)
        assert step["status"] == "interrupted" and step["interrupt_reason"] == "chat"
        assert abs(float(step["exec_seconds"] or 0) - 2.0) < 0.5, "budget approximation = now − started_at"
        t = store.get_task(task_id)
        assert t["status"] == "frozen" and t["abort_reason"] == "chat", "frozen while the user chats"
        assert t["next_step_index"] == 0, "index held — the step re-runs on resume"
        # The flag was consumed (consumer-clears rule).
        test_app.request_queue.redis.get.side_effect = None
        test_app.request_queue.redis.delete.assert_any_call("task:step_cancel:sub-1".replace("sub-1", row_id))

    def test_subtask_poll_exhausted_no_result(self, poll_env, monkeypatch):
        """Poll budget exhausted, queue task gone ⇒ step failed (result expired)."""
        test_app, calls, store = poll_env
        task_id, row_id = self._seed_subtask(store)
        monkeypatch.setattr(ex, "_run_final_summary", lambda *a, **k: "final summary text")
        # Force a tiny poll budget so exhaustion is immediate.
        test_app.config["TASK_POLL_INTERVAL_S"] = 0.01
        test_app.config["TASK_SUBTASK_POLL_CYCLES"] = 1

        executor = ex.TaskExecutor(app=test_app)
        monkeypatch.setattr(executor, "_peek_subtask_result", lambda queue_id: None)
        executor._process_one_cycle(gate_open=True)

        step = store.get_step(row_id)
        assert step["status"] == "failed", "exhausted polls without a result ⇒ failed"
        assert "expired" in (step["last_error"] or "").lower()
        assert store.get_task(task_id)["next_step_index"] == 1, "task continues past the dead subtask"

    def test_poll_budget_holds_index_when_queue_alive(self, poll_env, monkeypatch):
        """Poll budget exhausted BUT the queue task is alive ⇒ keep polling (index held)."""
        test_app, calls, store = poll_env
        task_id, row_id = self._seed_subtask(store)
        test_app.config["TASK_POLL_INTERVAL_S"] = 0.01
        test_app.config["TASK_SUBTASK_POLL_CYCLES"] = 1

        executor = ex.TaskExecutor(app=test_app)
        monkeypatch.setattr(executor, "_peek_subtask_result", lambda queue_id: None)
        monkeypatch.setattr(executor, "_subtask_alive", lambda queue_id: True)
        executor._process_one_cycle(gate_open=True)

        step = store.get_step(row_id)
        assert step["status"] == "subtask_running", "still running — nothing expired"
        t = store.get_task(task_id)
        assert t["status"] == "active" and t["next_step_index"] == 0, "index held for the next cycle"

    def test_subtask_alive_sees_queued_list(self, poll_env, monkeypatch):
        """A subtask still waiting in the slow queue list is ALIVE, not expired."""
        test_app, calls, store = poll_env
        task_id, row_id = self._seed_subtask(store)
        test_app.config["TASK_POLL_INTERVAL_S"] = 0.01
        test_app.config["TASK_SUBTASK_POLL_CYCLES"] = 1

        class _FakeRedis:
            @staticmethod
            def hexists(key, member):  # noqa: ARG002
                return False  # nobody is processing it yet

            @staticmethod
            def lrange(key, start, end):  # noqa: ARG002
                return ["sub-1"] if key == "slow_request_queue" else []

        class _Q:
            processing_key = "processing_requests"
            slow_processing_key = "slow_processing_requests"
            background_processing_key = "background_processing"
            crawl_processing_key = "request_queue:crawl:processing"
            queue_key = "request_queue"
            slow_queue_key = "slow_request_queue"
            background_queue_key = "background_queue"
            crawl_queue_key = "request_queue:crawl"
            redis = _FakeRedis()

            @staticmethod
            def requeue_deferred_subtasks():
                return 0

        # The real liveness method (NOT patched): hexists all miss, the id sits
        # in the slow queue list ⇒ alive ⇒ the step is held, not failed.
        monkeypatch.setattr(ex, "_get_request_queue", lambda: _Q())
        executor = ex.TaskExecutor(app=test_app)
        monkeypatch.setattr(executor, "_peek_subtask_result", lambda queue_id: None)
        executor._process_one_cycle(gate_open=True)

        step = store.get_step(row_id)
        assert step["status"] == "subtask_running", "queued subtask held — not declared expired"
        assert store.get_task(task_id)["next_step_index"] == 0, "index held for the next cycle"
