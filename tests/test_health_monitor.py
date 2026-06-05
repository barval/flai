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
        count = hm._record_failure("chat")
        assert count == 1

    def test_record_multiple_failures_in_window(self):
        """Multiple failures in window → count grows."""
        hm._record_failure("chat")
        hm._record_failure("chat")
        count = hm._record_failure("chat")
        assert count == 3

    def test_old_failures_evicted(self):
        """Failures older than WATCHDOG_FAILURE_WINDOW_S are evicted."""
        hm._record_failure("chat")
        # Fake old timestamp
        hm._failures["chat"][0] = time.time() - hm.WATCHDOG_FAILURE_WINDOW_S - 1
        count = hm._record_failure("chat")
        # Old one was evicted, only the new one remains
        assert count == 1

    def test_separate_modules_independent(self):
        """Each module has its own failure counter."""
        hm._record_failure("chat")
        hm._record_failure("chat")
        chat_count = hm._record_failure("chat")
        reasoning_count = hm._record_failure("reasoning")
        assert chat_count == 3
        assert reasoning_count == 1


class TestClearFailures:
    def test_clear_empties_counter(self):
        """_clear_failures removes the module's counter."""
        hm._record_failure("chat")
        assert "chat" in hm._failures
        hm._clear_failures("chat")
        assert "chat" not in hm._failures

    def test_clear_nonexistent_module_no_error(self):
        """Clearing a module that has no entries doesn't raise."""
        hm._clear_failures("nonexistent")  # should not raise


class TestGetRunning:
    @patch("requests.get")
    def test_returns_running_list(self, mock_get):
        """Returns the 'running' list from /running endpoint."""
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"running": [{"name": "chat", "model_id": "Qwen3"}]},
        )
        running = hm._get_running("http://swap:8080")
        assert running == [{"name": "chat", "model_id": "Qwen3"}]

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
        assert hm._try_health_check("http://swap:8080", "chat") is True

    @patch("requests.post")
    def test_health_check_500(self, mock_post):
        """500 response → False."""
        mock_post.return_value = MagicMock(status_code=500)
        assert hm._try_health_check("http://swap:8080", "chat") is False

    @patch("requests.post", side_effect=ConnectionError)
    def test_health_check_network_error(self, mock_post):
        """Network error → False."""
        assert hm._try_health_check("http://swap:8080", "chat") is False


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
