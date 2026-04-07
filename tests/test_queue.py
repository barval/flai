# tests/test_queue.py
"""Tests for Redis request queue."""
import pytest
from unittest.mock import Mock, patch, MagicMock
import json
import hmac
import hashlib


@pytest.mark.unit
class TestRedisRequestQueue:
    """Test cases for RedisRequestQueue class."""

    @pytest.fixture
    def mock_app(self):
        """Create mock Flask app."""
        app = Mock()
        app.config = {
            'REDIS_URL': 'redis://localhost:6379/0',
            'SECRET_KEY': 'test-secret-key'
        }
        app.logger = Mock()
        return app

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        redis = Mock()
        redis.blpop.return_value = None
        redis.llen.return_value = 0
        redis.scard.return_value = 0
        return redis

    def test_serialize_creates_hmac_signature(self, mock_app, mock_redis):
        """Test that _serialize creates proper HMAC signature."""
        from app.queue import RedisRequestQueue
        
        with patch('app.queue.redis.from_url', return_value=mock_redis):
            queue = RedisRequestQueue(mock_app)
            
            data = {'test': 'data', 'user_id': 'test_user'}
            result = queue._serialize(data)
            
            # Result should be JSON
            wrapper = json.loads(result)
            assert 'data' in wrapper
            assert 'sig' in wrapper
            
            # Verify signature
            expected_sig = hmac.new(
                mock_app.config['SECRET_KEY'].encode('utf-8'),
                wrapper['data'].encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            assert wrapper['sig'] == expected_sig

    def test_deserialize_verifies_signature(self, mock_app, mock_redis):
        """Test that _deserialize verifies HMAC signature."""
        from app.queue import RedisRequestQueue
        
        with patch('app.queue.redis.from_url', return_value=mock_redis):
            queue = RedisRequestQueue(mock_app)
            
            # Create valid serialized data
            original_data = {'test': 'data'}
            serialized = queue._serialize(original_data)
            
            # Deserialize should work
            result = queue._deserialize(serialized)
            assert result == original_data

    def test_deserialize_rejects_tampered_data(self, mock_app, mock_redis):
        """Test that _deserialize rejects tampered data."""
        from app.queue import RedisRequestQueue
        
        with patch('app.queue.redis.from_url', return_value=mock_redis):
            queue = RedisRequestQueue(mock_app)
            
            # Create valid serialized data
            serialized = queue._serialize({'test': 'data'})
            wrapper = json.loads(serialized)
            
            # Tamper with data
            wrapper['data'] = json.dumps({'tampered': 'data'})
            tampered_serialized = json.dumps(wrapper)
            
            # Deserialize should return None for tampered data
            result = queue._deserialize(tampered_serialized)
            assert result is None

    def test_get_user_queue_counts_empty(self, mock_app, mock_redis):
        """Test get_user_queue_counts with empty queue."""
        from app.queue import RedisRequestQueue
        
        mock_redis.llen.return_value = 0
        
        with patch('app.queue.redis.from_url', return_value=mock_redis):
            queue = RedisRequestQueue(mock_app)
            user_count, total_count = queue.get_user_queue_counts('test_user')
            
            assert user_count == 0
            assert total_count == 0

    def test_add_request_creates_task(self, mock_app, mock_redis):
        """Test that add_request creates and queues a task."""
        from app.queue import RedisRequestQueue

        with patch('app.queue.redis.from_url', return_value=mock_redis):
            queue = RedisRequestQueue(mock_app)

            # Mock _get_session_title to avoid DB calls
            queue._get_session_title = Mock(return_value='Test Session')

            request_id, position = queue.add_request(
                user_id='test_user',
                session_id='test-session-id',
                request_data={'type': 'text', 'text': 'Hello'},
                user_class=2,
                lang='ru'
            )

            # Should have called rpush
            assert mock_redis.rpush.called
            # Should have added to user set
            assert mock_redis.sadd.called
            # Should return request_id and position
            assert request_id is not None
            assert position['position'] >= 1
