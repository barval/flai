# tests/test_model_config.py
"""Tests for model configuration module."""

import datetime
from unittest.mock import MagicMock, patch

import pytest

from app import model_config

_NOW = datetime.datetime(2026, 5, 21, 12, 0, 0)


class TestModelConfig:
    """Test cases for model_config module."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Clear cache before each test."""
        model_config._MODEL_CONFIG_CACHE.clear()
        yield
        model_config._MODEL_CONFIG_CACHE.clear()

    def test_get_model_config_returns_cached(self):
        """Should return cached data when updated_at matches DB."""
        cached_data = {"model_name": "test-model", "temperature": 0.7, "updated_at": _NOW}
        model_config._MODEL_CONFIG_CACHE["chat"] = {"data": cached_data, "_updated_at": _NOW}

        with patch("app.model_config.get_db") as mock_get_db:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = {"updated_at": _NOW}
            mock_conn.cursor.return_value = mock_cursor
            mock_get_db.return_value.__enter__.return_value = mock_conn

            result = model_config.get_model_config("chat")
            assert result == cached_data
            # Only the lightweight updated_at check was executed
            mock_cursor.execute.assert_called_once_with(
                "SELECT updated_at FROM model_configs WHERE module = %s", ("chat",)
            )

    def test_get_model_config_returns_none_for_missing(self):
        """Should return None if module not found in cache or DB."""
        with patch("app.model_config.get_db") as mock_get_db:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = None
            mock_conn.cursor.return_value = mock_cursor
            mock_get_db.return_value.__enter__.return_value = mock_conn

            result = model_config.get_model_config("chat")
            assert result is None

    def test_get_model_config_queries_database(self):
        """Should query database if not in cache."""
        db_row = {"module": "chat", "model_name": "qwen", "temperature": 0.7, "updated_at": _NOW}
        with patch("app.model_config.get_db") as mock_get_db:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = db_row
            mock_conn.cursor.return_value = mock_cursor
            mock_get_db.return_value.__enter__.return_value = mock_conn

            result = model_config.get_model_config("chat")

            assert result == db_row
            # exec SELECT * because cache was empty
            mock_cursor.execute.assert_called_once()

    def test_get_model_config_caches_result(self):
        """Should cache database result and reuse on subsequent calls."""
        db_row = {"module": "chat", "model_name": "qwen", "updated_at": _NOW}

        with patch("app.model_config.get_db") as mock_get_db:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = db_row
            mock_conn.cursor.return_value = mock_cursor
            mock_get_db.return_value.__enter__.return_value = mock_conn

            model_config.get_model_config("chat")
            model_config.get_model_config("chat")

            # First call: SELECT * (cache miss)
            # Second call: SELECT updated_at (cache hit, matches)
            assert mock_cursor.execute.call_count == 2

    def test_get_model_config_reloads_when_updated_at_changes(self):
        """Should re-read from DB when updated_at differs from cache."""
        old_row = {"model_name": "old-model", "updated_at": _NOW}
        new_row = {"model_name": "new-model", "updated_at": _NOW + datetime.timedelta(seconds=10)}

        with patch("app.model_config.get_db") as mock_get_db:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.side_effect = [
                {"updated_at": _NOW + datetime.timedelta(seconds=10)},  # freshness check → mismatch
                new_row,  # full re-read
            ]
            mock_conn.cursor.return_value = mock_cursor
            mock_get_db.return_value.__enter__.return_value = mock_conn

            model_config._MODEL_CONFIG_CACHE["chat"] = {"data": old_row, "_updated_at": _NOW}

            result = model_config.get_model_config("chat")
            assert result["model_name"] == "new-model"

    def test_get_model_config_deleted_row(self):
        """Should return None and clear cache if row deleted from DB."""
        with patch("app.model_config.get_db") as mock_get_db:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = None  # row deleted
            mock_conn.cursor.return_value = mock_cursor
            mock_get_db.return_value.__enter__.return_value = mock_conn

            model_config._MODEL_CONFIG_CACHE["chat"] = {"data": {"model": "old"}, "_updated_at": _NOW}

            result = model_config.get_model_config("chat")
            assert result is None
            assert "chat" not in model_config._MODEL_CONFIG_CACHE

    def test_invalidate_model_config_cache_specific(self):
        """Should invalidate specific module cache."""
        model_config._MODEL_CONFIG_CACHE["chat"] = {"data": {"model": "a"}, "_updated_at": _NOW}
        model_config._MODEL_CONFIG_CACHE["embedding"] = {"data": {"model": "b"}, "_updated_at": _NOW}

        model_config.invalidate_model_config_cache("chat")

        assert "chat" not in model_config._MODEL_CONFIG_CACHE
        assert "embedding" in model_config._MODEL_CONFIG_CACHE

    def test_invalidate_model_config_cache_all(self):
        """Should invalidate all cache if no module specified."""
        model_config._MODEL_CONFIG_CACHE["chat"] = {"data": {"model": "a"}, "_updated_at": _NOW}
        model_config._MODEL_CONFIG_CACHE["embedding"] = {"data": {"model": "b"}, "_updated_at": _NOW}

        model_config.invalidate_model_config_cache()

        assert len(model_config._MODEL_CONFIG_CACHE) == 0

    def test_get_model_config_fallback_on_db_error(self):
        """Should return cached data on DB error."""
        model_config._MODEL_CONFIG_CACHE["chat"] = {"data": {"model_name": "fallback"}, "_updated_at": _NOW}

        with patch("app.model_config.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__.side_effect = Exception("DB error")

            result = model_config.get_model_config("chat")
            assert result == {"model_name": "fallback"}

    def test_get_model_config_returns_none_on_db_error_no_cache(self):
        """Should return None on DB error when no cache."""
        with patch("app.model_config.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__.side_effect = Exception("DB error")

            result = model_config.get_model_config("chat")
            assert result is None

    def test_reload_all_model_configs(self):
        """Should reload all configs from DB."""
        rows = [
            {"module": "chat", "model_name": "chat-model", "updated_at": _NOW},
            {"module": "reasoning", "model_name": "reason-model", "updated_at": _NOW},
        ]

        with patch("app.model_config.get_db") as mock_get_db:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchall.return_value = rows
            mock_conn.cursor.return_value = mock_cursor
            mock_get_db.return_value.__enter__.return_value = mock_conn

            result = model_config.reload_all_model_configs()

            assert len(result) == 2
            assert "chat" in model_config._MODEL_CONFIG_CACHE
            assert "reasoning" in model_config._MODEL_CONFIG_CACHE


class TestModelConfigNoUpdatedAt:
    """Tests for cache entries without updated_at (legacy data)."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        model_config._MODEL_CONFIG_CACHE.clear()
        yield
        model_config._MODEL_CONFIG_CACHE.clear()

    def test_cache_without_updated_at_triggers_full_read(self):
        """Entry without _updated_at should re-read from DB each time."""
        with patch("app.model_config.get_db") as mock_get_db:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.side_effect = [
                {"updated_at": _NOW},  # freshness check
                {"module": "chat", "model_name": "m1", "updated_at": _NOW},  # full read
            ]
            mock_conn.cursor.return_value = mock_cursor
            mock_get_db.return_value.__enter__.return_value = mock_conn

            model_config._MODEL_CONFIG_CACHE["chat"] = {"data": {"model": "old"}}

            result = model_config.get_model_config("chat")
            assert result["model_name"] == "m1"
            # Should have executed SELECT * (not just updated_at)
            args = mock_cursor.execute.call_args
            assert args is not None
            assert args[0][0].strip().upper().startswith("SELECT *")
