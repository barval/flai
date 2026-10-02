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
    yield
    hm._failures.clear()


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
            patch.object(hm, "_get_running") as get_running,
            pytest.raises(KeyboardInterrupt),
        ):
            hm._watchdog_loop(MagicMock())

        assert waits == [30, hm.WATCHDOG_INTERVAL_S]
        get_running.assert_not_called()
