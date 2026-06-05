# tests/test_queue.py
"""Tests for Redis request queue."""

import hashlib
import hmac
import json
from unittest.mock import Mock, patch

import pytest


@pytest.mark.unit
class TestRedisRequestQueue:
    """Test cases for RedisRequestQueue class."""

    @pytest.fixture(autouse=True)
    def _no_workers(self):
        """Prevent worker threads from starting during tests."""
        with patch("app.queue.RedisRequestQueue.start_worker"):
            yield

    @pytest.fixture
    def mock_app(self):
        """Create mock Flask app."""
        app = Mock()
        app.config = {"REDIS_URL": "redis://localhost:6379/0", "SECRET_KEY": "test-secret-key"}
        app.logger = Mock()
        return app

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        redis = Mock()
        redis.blpop.return_value = None
        redis.llen.return_value = 0
        redis.hlen.return_value = 0
        redis.scard.return_value = 0
        redis.hincrby.return_value = 1
        pipe = Mock()
        pipe.hincrby.return_value = 1
        pipe.execute.return_value = [1, 1]
        redis.pipeline.return_value = pipe
        return redis

    def test_serialize_creates_hmac_signature(self, mock_app, mock_redis):
        """Test that _serialize creates proper HMAC signature."""
        from app.queue import RedisRequestQueue

        with patch("app.queue.redis.from_url", return_value=mock_redis):
            queue = RedisRequestQueue(mock_app)

            data = {"test": "data", "user_id": "test_user"}
            result = queue._serialize(data)

            # Result should be JSON
            wrapper = json.loads(result)
            assert "data" in wrapper
            assert "sig" in wrapper

            # Verify signature
            expected_sig = hmac.new(
                mock_app.config["SECRET_KEY"].encode("utf-8"), wrapper["data"].encode("utf-8"), hashlib.sha256
            ).hexdigest()
            assert wrapper["sig"] == expected_sig

    def test_deserialize_verifies_signature(self, mock_app, mock_redis):
        """Test that _deserialize verifies HMAC signature."""
        from app.queue import RedisRequestQueue

        with patch("app.queue.redis.from_url", return_value=mock_redis):
            queue = RedisRequestQueue(mock_app)

            # Create valid serialized data
            original_data = {"test": "data"}
            serialized = queue._serialize(original_data)

            # Deserialize should work
            result = queue._deserialize(serialized)
            assert result == original_data

    def test_deserialize_rejects_tampered_data(self, mock_app, mock_redis):
        """Test that _deserialize rejects tampered data."""
        from app.queue import RedisRequestQueue

        with patch("app.queue.redis.from_url", return_value=mock_redis):
            queue = RedisRequestQueue(mock_app)

            # Create valid serialized data
            serialized = queue._serialize({"test": "data"})
            wrapper = json.loads(serialized)

            # Tamper with data
            wrapper["data"] = json.dumps({"tampered": "data"})
            tampered_serialized = json.dumps(wrapper)

            # Deserialize should return None for tampered data
            result = queue._deserialize(tampered_serialized)
            assert result is None

    def test_get_user_queue_counts_empty(self, mock_app, mock_redis):
        """Test get_user_queue_counts with empty queue."""
        from app.queue import RedisRequestQueue

        mock_redis.llen.return_value = 0

        with patch("app.queue.redis.from_url", return_value=mock_redis):
            queue = RedisRequestQueue(mock_app)
            user_count, total_count = queue.get_user_queue_counts("test_user")

            assert user_count == 0
            assert total_count == 0

    def test_add_request_creates_task(self, mock_app, mock_redis):
        """Test that add_request creates and queues a task."""
        from app.queue import RedisRequestQueue

        with patch("app.queue.redis.from_url", return_value=mock_redis):
            queue = RedisRequestQueue(mock_app)

            request_id, position = queue.add_request(
                user_id="test_user",
                session_id="test-session-id",
                request_data={"type": "text", "text": "Hello"},
                user_class=2,
                lang="ru",
            )

            # RPUSH + HINCRBY should be called atomically via pipeline
            pipe = mock_redis.pipeline.return_value
            assert pipe.rpush.called
            assert pipe.hincrby.called
            assert pipe.execute.called

    def test_recover_stale_tasks(self, mock_app, mock_redis):
        """Test recovery of stale tasks from processing hash."""

        # Simulate a stuck task in processing
        task = {"id": "task-1", "type": "text", "text": "hello", "timestamp": 1234567890}
        task_json = json.dumps(task)
        # _serialize adds HMAC signature: data.sig
        from app.queue import RedisRequestQueue as RQ  # noqa: N814

        # We need to mock the deserialization to return valid data
        mock_redis.hgetall.return_value = {b"task-1": task_json.encode()}

        with patch("app.queue.redis.from_url", return_value=mock_redis):
            queue = RQ(mock_app)
            # _deserialize will try to verify HMAC, which will fail with raw JSON
            # So we patch it to return the task directly
            with patch.object(queue, "_deserialize", return_value=task):
                queue._recover_stale_tasks()

            # Should have re-queued the task
            assert mock_redis.rpush.called
            # Should have removed from processing
            assert mock_redis.hdel.called

    def test_recover_stale_tasks_empty(self, mock_app, mock_redis):
        """Test recovery when no stale tasks exist."""
        from app.queue import RedisRequestQueue

        mock_redis.hgetall.return_value = {}

        with patch("app.queue.redis.from_url", return_value=mock_redis):
            queue = RedisRequestQueue(mock_app)
            queue._recover_stale_tasks()

            # Should not have called rpush
            assert not mock_redis.rpush.called
