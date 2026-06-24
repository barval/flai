# tests/test_dry_load.py
"""Tests for the background dry-load + auto-rollback task."""
from unittest.mock import MagicMock, patch


class TestDryLoad:
    """Test schedule_dry_load worker behavior."""

    def test_schedule_dry_load_spawns_thread(self):
        """schedule_dry_load starts a daemon thread, doesn't block."""
        from app.tasks import dry_load

        with patch.object(dry_load.threading, "Thread") as mock_thread_cls:
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread
            dry_load.schedule_dry_load(MagicMock(), "chat", "Qwen3-4B.gguf")
            mock_thread_cls.assert_called_once()
            mock_thread.start.assert_called_once()

    def test_schedule_dry_load_skips_empty_model(self):
        """Empty model name → no thread spawned."""
        from app.tasks import dry_load

        with patch.object(dry_load.threading, "Thread") as mock_thread_cls:
            dry_load.schedule_dry_load(MagicMock(), "chat", "")
            mock_thread_cls.assert_not_called()

    def test_fallback_models_dict_has_all_modules(self):
        """FALLBACK_MODELS has fallback for every module type."""
        from app.tasks.dry_load import get_fallback_models

        models = get_fallback_models()
        assert "chat" in models
        assert "reasoning" in models
        assert "multimodal" in models
        assert "embedding" in models
        for module, fallback in models.items():
            assert fallback, f"Empty fallback for {module}"


class TestTriggerLoad:
    """Test the _trigger_load function."""

    @patch("requests.post")
    def test_trigger_load_success(self, mock_post):
        """200 response → return True."""
        from app.tasks.dry_load import _trigger_load

        mock_post.return_value = MagicMock(status_code=200)
        result = _trigger_load("http://swap:8080", "chat")
        assert result is True
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "chat/completions" in call_args.args[0]
        assert call_args.kwargs["json"]["model"] == "chat"

    @patch("requests.post")
    def test_trigger_load_http_error(self, mock_post):
        """500 response → return False."""
        from app.tasks.dry_load import _trigger_load

        mock_post.return_value = MagicMock(status_code=500)
        result = _trigger_load("http://swap:8080", "chat")
        assert result is False

    @patch("requests.post", side_effect=ConnectionError("boom"))
    def test_trigger_load_network_error(self, mock_post):
        """Network exception → return False."""
        from app.tasks.dry_load import _trigger_load

        result = _trigger_load("http://swap:8080", "chat")
        assert result is False


class TestCheckRunning:
    """Test _check_running verification."""

    @patch("requests.get")
    def test_check_running_found(self, mock_get):
        """Model is in /running list → True."""
        from app.tasks.dry_load import _check_running

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"running": [{"name": "chat", "model_id": "Qwen3"}]},
        )
        assert _check_running("http://swap:8080", "chat") is True

    @patch("requests.get")
    def test_check_running_not_found(self, mock_get):
        """Model not in /running list → False."""
        from app.tasks.dry_load import _check_running

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"running": [{"name": "reasoning"}]},
        )
        assert _check_running("http://swap:8080", "chat") is False

    @patch("requests.get")
    def test_check_running_http_error(self, mock_get):
        """HTTP error → False (don't fail loudly)."""
        from app.tasks.dry_load import _check_running

        mock_get.return_value = MagicMock(status_code=503)
        assert _check_running("http://swap:8080", "chat") is False

    @patch("requests.get", side_effect=ConnectionError)
    def test_check_running_network_error(self, mock_get):
        """Network exception → False."""
        from app.tasks.dry_load import _check_running

        assert _check_running("http://swap:8080", "chat") is False
