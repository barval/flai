# tests/test_health_monitor.py
"""Tests for the crash-loop watchdog in app.tasks.health_monitor."""

import time
from unittest.mock import MagicMock, patch

import pytest

from app.tasks import health_monitor as hm


@pytest.fixture(autouse=True)
def reset_failure_tracking():
    """Reset global failure tracking between tests."""
    hm._failures.clear()
    hm._unhealthy.clear()
    yield
    hm._failures.clear()
    hm._unhealthy.clear()


class TestRecordFailure:
    """Test failure-counting logic."""

    def test_record_first_failure(self):
        """First failure → count is 1."""
        count = hm._record_failure("multimodal")
        assert count == 1

    def test_record_multiple_failures_in_window(self):
        """Multiple failures in window → count grows."""
        hm._record_failure("multimodal")
        hm._record_failure("multimodal")
        count = hm._record_failure("multimodal")
        assert count == 3

    def test_old_failures_evicted(self):
        """Failures older than WATCHDOG_FAILURE_WINDOW_S are evicted."""
        hm._record_failure("multimodal")
        # Fake old timestamp
        hm._failures["multimodal"][0] = time.time() - hm.WATCHDOG_FAILURE_WINDOW_S - 1
        count = hm._record_failure("multimodal")
        # Old one was evicted, only the new one remains
        assert count == 1

    def test_separate_modules_independent(self):
        """Each module has its own failure counter."""
        hm._record_failure("multimodal")
        hm._record_failure("multimodal")
        chat_count = hm._record_failure("multimodal")
        reasoning_count = hm._record_failure("reasoning")
        assert chat_count == 3
        assert reasoning_count == 1


class TestClearFailures:
    def test_clear_empties_counter(self):
        """_clear_failures removes the module's counter."""
        hm._record_failure("multimodal")
        assert "multimodal" in hm._failures
        hm._clear_failures("multimodal")
        assert "multimodal" not in hm._failures

    def test_clear_nonexistent_module_no_error(self):
        """Clearing a module that has no entries doesn't raise."""
        hm._clear_failures("nonexistent")  # should not raise


class TestGetRunning:
    @patch("requests.get")
    def test_returns_running_list(self, mock_get):
        """Returns the 'running' list from /running endpoint."""
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"running": [{"name": "multimodal", "model_id": "Qwen3"}]},
        )
        running = hm._get_running("http://swap:8080")
        assert running == [{"name": "multimodal", "model_id": "Qwen3"}]

    @patch("requests.get")
    def test_returns_empty_on_error(self, mock_get):
        """HTTP error → empty list (don't crash watchdog)."""
        mock_get.return_value = MagicMock(status_code=500)
        assert hm._get_running("http://swap:8080") == []

    @patch("requests.get", side_effect=ConnectionError)
    def test_returns_empty_on_network_error(self, mock_get):
        """Network error → empty list."""
        assert hm._get_running("http://swap:8080") == []


class TestTryHealthCheck:
    @patch("requests.post")
    def test_health_check_success(self, mock_post):
        """200 response → True."""
        mock_post.return_value = MagicMock(status_code=200)
        assert hm._try_health_check("http://swap:8080", "multimodal") is True

    @patch("requests.post")
    def test_health_check_500(self, mock_post):
        """500 response → False."""
        mock_post.return_value = MagicMock(status_code=500)
        assert hm._try_health_check("http://swap:8080", "multimodal") is False

    @patch("requests.post", side_effect=ConnectionError)
    def test_health_check_network_error(self, mock_post):
        """Network error → False."""
        assert hm._try_health_check("http://swap:8080", "multimodal") is False


class TestStartWatchdog:
    def test_starts_daemon_thread(self):
        """start_watchdog spawns a daemon thread."""
        with patch.object(hm.threading, "Thread") as mock_thread_cls:
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread
            hm.start_watchdog(MagicMock())
            mock_thread_cls.assert_called_once()
            mock_thread.start.assert_called_once()
            assert mock_thread_cls.call_args.kwargs["daemon"] is True
            assert mock_thread_cls.call_args.kwargs["name"] == "flai-watchdog"


class TestStopWatchdog:
    """The watchdog thread must be stoppable.

    Every ``create_app()`` starts one, so a test suite that builds hundreds of
    apps accumulates hundreds of unstoppable polling threads. They are daemon
    threads, so the process still exits, but they keep app contexts, Redis
    clients and a poll loop alive for the whole run.
    """

    def test_stop_watchdog_terminates_a_running_thread(self):
        app = MagicMock()
        with (
            patch.object(hm, "is_gpu_busy", return_value=False),
            patch.object(hm, "_get_running", return_value=[]),
        ):
            hm.start_watchdog(app)
            thread = app._watchdog_thread
            assert thread.is_alive()

            assert hm.stop_watchdog(app, timeout=5) is True
            assert not thread.is_alive()

    def test_stop_watchdog_without_start_is_a_noop(self):
        assert hm.stop_watchdog(MagicMock(), timeout=0) is False

    def test_restart_after_stop_starts_a_live_thread(self):
        app = MagicMock()
        with patch.object(hm, "_get_running", return_value=[]):
            hm.start_watchdog(app)
            hm.stop_watchdog(app, timeout=5)
            hm.start_watchdog(app)
            assert app._watchdog_thread.is_alive()
            hm.stop_watchdog(app, timeout=5)


class TestWatchdogGpuBusyGuard:
    """The watchdog must not health-check llama-swap while a GPU transaction
    (SD, video, or an ensure_vram_for unload+wait cycle) is in progress —
    a health check respawns the model being unloaded and starves the wait."""

    def test_is_gpu_busy_true_when_rm_busy(self):
        with patch("app.resource_manager.get_resource_manager") as grm:
            grm.return_value.is_gpu_busy.return_value = True
            assert hm.is_gpu_busy() is True

    def test_is_gpu_busy_false_when_idle(self):
        with patch("app.resource_manager.get_resource_manager") as grm:
            grm.return_value.is_gpu_busy.return_value = False
            assert hm.is_gpu_busy() is False

    def test_is_gpu_busy_swallows_errors(self):
        with patch("app.resource_manager.get_resource_manager", side_effect=RuntimeError("boom")):
            assert hm.is_gpu_busy() is False

    def test_watchdog_loop_skips_tick_when_gpu_busy(self):
        waits = []

        class FakeStop:
            def is_set(self):
                return False

            def wait(self, seconds):
                waits.append(seconds)
                if len(waits) >= 2:
                    raise KeyboardInterrupt
                return False

        with (
            patch.object(hm, "_stop_event_for", return_value=FakeStop()),
            patch.object(hm, "is_gpu_busy", return_value=True),
            patch.object(hm, "_queue_gpu_busy", return_value=False),
            patch.object(hm, "_get_running") as get_running,
            pytest.raises(KeyboardInterrupt),
        ):
            hm._watchdog_loop(MagicMock())

        assert waits == [30, hm.WATCHDOG_INTERVAL_S]
        get_running.assert_not_called()


class TestQueueGpuBusy:
    """The watchdog must also skip ticks while a worker holds _gpu_lock (a
    plain multimodal/reasoning call keeps it for minutes; health checks fired
    into the busy model time out and were counted as crash-loop failures)."""

    def test_true_when_queue_holds_gpu_lock(self):
        app = MagicMock()
        app.request_queue.is_gpu_task_active.return_value = True
        assert hm._queue_gpu_busy(app) is True

    def test_false_when_queue_idle(self):
        app = MagicMock()
        app.request_queue.is_gpu_task_active.return_value = False
        assert hm._queue_gpu_busy(app) is False

    def test_false_without_queue(self):
        """An app without a request_queue must not be treated as busy."""
        assert hm._queue_gpu_busy(object()) is False

    def test_false_on_exception(self):
        app = MagicMock()
        app.request_queue.is_gpu_task_active.side_effect = RuntimeError("boom")
        assert hm._queue_gpu_busy(app) is False

    def test_watchdog_loop_skips_tick_when_queue_busy(self):
        waits = []

        class FakeStop:
            def is_set(self):
                return False

            def wait(self, seconds):
                waits.append(seconds)
                if len(waits) >= 2:
                    raise KeyboardInterrupt
                return False

        with (
            patch.object(hm, "_stop_event_for", return_value=FakeStop()),
            patch.object(hm, "is_gpu_busy", return_value=False),
            patch.object(hm, "_queue_gpu_busy", return_value=True),
            patch.object(hm, "_get_running") as get_running,
            pytest.raises(KeyboardInterrupt),
        ):
            hm._watchdog_loop(MagicMock())

        assert waits == [30, hm.WATCHDOG_INTERVAL_S]
        get_running.assert_not_called()


class TestUnhealthyMarking:
    """The watchdog marks a module unhealthy on a crash loop but NEVER swaps
    the model — model choice is the admin's, and a hardcoded fallback that is
    missing on disk silently broke multimodal on a deployment."""

    def test_mark_adds_module_and_getter_returns_it(self):
        hm._mark_unhealthy("multimodal")
        assert hm._is_unhealthy("multimodal") is True
        assert hm.get_unhealthy_modules() == ["multimodal"]

    def test_clear_removes_module(self):
        hm._mark_unhealthy("multimodal")
        hm._clear_unhealthy("multimodal")
        assert hm.get_unhealthy_modules() == []

    def test_in_cooldown_right_after_mark(self):
        hm._mark_unhealthy("multimodal")
        assert hm._in_unhealthy_cooldown("multimodal") is True

    def test_cooldown_expires(self):
        hm._mark_unhealthy("multimodal")
        hm._unhealthy["multimodal"] = time.time() - hm.WATCHDOG_UNHEALTHY_COOLDOWN_S - 1
        assert hm._in_unhealthy_cooldown("multimodal") is False

    def test_loop_marks_unhealthy_without_swapping_model(self):
        waits = []
        # Never swap: the watchdog must not touch dry_load/_rollback.
        with patch("app.tasks.dry_load._rollback") as mock_rollback:

            class FakeStop:
                def is_set(self):
                    return False

                def wait(self, seconds):
                    waits.append(seconds)
                    if len(waits) >= 2:
                        raise KeyboardInterrupt
                    return False

            with (
                patch.object(hm, "_stop_event_for", return_value=FakeStop()),
                patch.object(hm, "is_gpu_busy", return_value=False),
                patch.object(hm, "_queue_gpu_busy", return_value=False),
                patch.object(hm, "_get_running", return_value=[{"name": "multimodal", "model_id": "Qwen3"}]),
                patch.object(hm, "_try_health_check", return_value=False),
                patch.object(hm, "WATCHDOG_FAILURE_THRESHOLD", 1),
                pytest.raises(KeyboardInterrupt),
            ):
                hm._watchdog_loop(MagicMock())

        assert "multimodal" in hm._unhealthy
        mock_rollback.assert_not_called()

    def test_healthy_check_clears_unhealthy_mark(self):
        waits = []

        class FakeStop:
            def is_set(self):
                return False

            def wait(self, seconds):
                waits.append(seconds)
                if len(waits) >= 2:
                    raise KeyboardInterrupt
                return False

        hm._mark_unhealthy("multimodal")
        hm._unhealthy["multimodal"] = time.time() - hm.WATCHDOG_UNHEALTHY_COOLDOWN_S - 1
        with (
            patch.object(hm, "_stop_event_for", return_value=FakeStop()),
            patch.object(hm, "is_gpu_busy", return_value=False),
            patch.object(hm, "_queue_gpu_busy", return_value=False),
            patch.object(hm, "_get_running", return_value=[{"name": "multimodal", "model_id": "Qwen3"}]),
            patch.object(hm, "_try_health_check", return_value=True),
            pytest.raises(KeyboardInterrupt),
        ):
            hm._watchdog_loop(MagicMock())

        assert hm.get_unhealthy_modules() == []

    def test_health_check_skipped_during_unhealthy_cooldown(self):
        waits = []

        class FakeStop:
            def is_set(self):
                return False

            def wait(self, seconds):
                waits.append(seconds)
                if len(waits) >= 2:
                    raise KeyboardInterrupt
                return False

        hm._mark_unhealthy("multimodal")
        with (
            patch.object(hm, "_stop_event_for", return_value=FakeStop()),
            patch.object(hm, "is_gpu_busy", return_value=False),
            patch.object(hm, "_queue_gpu_busy", return_value=False),
            patch.object(hm, "_get_running", return_value=[{"name": "multimodal", "model_id": "Qwen3"}]),
            patch.object(hm, "_try_health_check") as try_check,
            pytest.raises(KeyboardInterrupt),
        ):
            hm._watchdog_loop(MagicMock())

        try_check.assert_not_called()
