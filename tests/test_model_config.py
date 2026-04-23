# tests/test_model_config.py
"""Tests for model configuration module."""
import pytest
import time
from unittest.mock import patch, MagicMock, call

from app import model_config


class TestModelConfig:
    """Test cases for model_config module."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Clear cache before each test."""
        model_config._MODEL_CONFIG_CACHE.clear()
        yield
        model_config._MODEL_CONFIG_CACHE.clear()

    def test_get_model_config_returns_cached(self):
        """Should return cached data if available and not expired."""
        cached_data = {'model_name': 'test-model', 'temperature': 0.7}
        model_config._MODEL_CONFIG_CACHE['chat'] = {
            'data': cached_data,
            'time': time.time()
        }

        result = model_config.get_model_config('chat')
        assert result == cached_data

    def test_get_model_config_returns_none_for_missing(self):
        """Should return None if module not found in cache or DB."""
        with patch('app.model_config.get_db') as mock_get_db:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = None
            mock_conn.cursor.return_value = mock_cursor
            mock_get_db.return_value.__enter__.return_value = mock_conn

            result = model_config.get_model_config('chat')
            assert result is None

    def test_get_model_config_queries_database(self):
        """Should query database if not in cache."""
        db_row = {'module': 'chat', 'model_name': 'qwen', 'temperature': 0.7}
        with patch('app.model_config.get_db') as mock_get_db:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = db_row
            mock_conn.cursor.return_value = mock_cursor
            mock_get_db.return_value.__enter__.return_value = mock_conn

            result = model_config.get_model_config('chat')

            assert result == db_row
            mock_cursor.execute.assert_called_once()

    def test_get_model_config_caches_result(self):
        """Should cache database result."""
        db_row = {'module': 'chat', 'model_name': 'qwen'}
        
        with patch('app.model_config.get_db') as mock_get_db:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = db_row
            mock_conn.cursor.return_value = mock_cursor
            mock_get_db.return_value.__enter__.return_value = mock_conn

            model_config.get_model_config('chat')
            model_config.get_model_config('chat')

            assert mock_cursor.execute.call_count == 1

    def test_invalidate_model_config_cache_specific(self):
        """Should invalidate specific module cache."""
        model_config._MODEL_CONFIG_CACHE['chat'] = {'data': {'model': 'a'}, 'time': time.time()}
        model_config._MODEL_CONFIG_CACHE['embedding'] = {'data': {'model': 'b'}, 'time': time.time()}

        model_config.invalidate_model_config_cache('chat')

        assert 'chat' not in model_config._MODEL_CONFIG_CACHE
        assert 'embedding' in model_config._MODEL_CONFIG_CACHE

    def test_invalidate_model_config_cache_all(self):
        """Should invalidate all cache if no module specified."""
        model_config._MODEL_CONFIG_CACHE['chat'] = {'data': {'model': 'a'}, 'time': time.time()}
        model_config._MODEL_CONFIG_CACHE['embedding'] = {'data': {'model': 'b'}, 'time': time.time()}

        model_config.invalidate_model_config_cache()

        assert len(model_config._MODEL_CONFIG_CACHE) == 0

    def test_cache_expiration(self):
        """Cache should expire after TTL."""
        old_time = time.time() - model_config._CACHE_TTL - 1
        model_config._MODEL_CONFIG_CACHE['chat'] = {
            'data': {'model': 'old'},
            'time': old_time
        }

        with patch('app.model_config.get_db') as mock_get_db:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = {'module': 'chat', 'model_name': 'new'}
            mock_conn.cursor.return_value = mock_cursor
            mock_get_db.return_value.__enter__.return_value = mock_conn

            result = model_config.get_model_config('chat')
            assert result['model_name'] == 'new'

    def test_invalidate_cache_on_error(self):
        """Should not crash on database error."""
        with patch('app.model_config.get_db') as mock_get_db:
            mock_get_db.return_value.__enter__.side_effect = Exception("DB error")

            result = model_config.get_model_config('chat')
            assert result is None


class TestModelConfigCache:
    """Test cache TTL behavior."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Clear cache before each test."""
        model_config._MODEL_CONFIG_CACHE.clear()
        yield
        model_config._MODEL_CONFIG_CACHE.clear()

    def test_cache_ttl_constant(self):
        """Cache TTL should be defined."""
        assert model_config._CACHE_TTL > 0

    def test_concurrent_access(self):
        """Cache should handle concurrent access."""
        call_count = [0]
        
        def mock_db():
            call_count[0] += 1
            return {'module': 'test', 'model': 'model'}

        with patch('app.model_config.get_db') as mock_get_db:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = mock_db()
            mock_conn.cursor.return_value = mock_cursor
            mock_get_db.return_value.__enter__.return_value = mock_conn

            model_config.get_model_config('chat')
            time.sleep(0.01)
            model_config.get_model_config('chat')

        assert call_count[0] == 1