# tests/test_slm_merge_watcher.py
"""Tests for the idle-time SLM merge watcher started by create_app()."""

import threading
from unittest.mock import MagicMock

import app
from app import _start_slm_merge_watcher, stop_slm_merge_watcher
from tests.conftest import stop_app_background_threads


def _watcher_threads() -> list[str]:
    return [t.name for t in threading.enumerate() if t.name in ("flai-watchdog", "slm-merge-watcher")]


class TestStopSlmMergeWatcher:
    """Every create_app() starts a merge watcher, so a test suite that builds
    hundreds of apps would accumulate hundreds of unstoppable minute-sleep
    threads holding an app context each. They must be stoppable."""

    def test_stop_terminates_a_running_thread(self):
        fake_app = MagicMock()
        _start_slm_merge_watcher(fake_app)
        thread = fake_app._slm_watcher_thread
        assert isinstance(thread, threading.Thread)
        assert thread.is_alive()

        assert stop_slm_merge_watcher(fake_app, timeout=5) is True
        assert not thread.is_alive()

    def test_stop_without_start_is_a_noop(self):
        assert stop_slm_merge_watcher(MagicMock(), timeout=0) is False

    def test_restart_after_stop_starts_a_live_thread(self):
        fake_app = MagicMock()
        _start_slm_merge_watcher(fake_app)
        stop_slm_merge_watcher(fake_app, timeout=5)
        _start_slm_merge_watcher(fake_app)
        assert fake_app._slm_watcher_thread.is_alive()
        stop_slm_merge_watcher(fake_app, timeout=5)

    def test_watcher_thread_is_daemon(self):
        fake_app = MagicMock()
        _start_slm_merge_watcher(fake_app)
        try:
            assert fake_app._slm_watcher_thread.daemon is True
        finally:
            stop_slm_merge_watcher(fake_app, timeout=5)
        assert app is not None


class TestConftestTeardownHelper:
    """The helper the test_app teardown uses must leave no watcher alive."""

    def test_stops_the_watchers_of_a_real_app(self, test_app):
        # The fixture really starts them, otherwise this proves nothing.
        assert _watcher_threads()

        stop_app_background_threads(test_app)

        assert _watcher_threads() == []

    def test_tolerates_an_app_without_watchers(self):
        stop_app_background_threads(MagicMock())
