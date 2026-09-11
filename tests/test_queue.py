# tests/test_queue.py
"""Tests for Redis request queue."""

import hashlib
import hmac
import json
import time
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

    def test_processing_ttl_covers_task_timeouts(self, mock_app, mock_redis):
        """processing_ttl must cover the longest configured task timeout (CPU builds).

        Otherwise long image/video tasks outlive the slow_processing_requests hash
        TTL and the lightning bolt disappears from the sidebar mid-task.
        """
        from app.queue import RedisRequestQueue

        mock_app.config.update(
            {
                "QUEUE_MAX_WAIT_TIME": 300,
                "SD_CLI_TIMEOUT": 3600,
                "LLM_TIMEOUT": 600,
                "LTX_VIDEO_TIMEOUT": 600,
                "SD_CPP_TIMEOUT": 1800,
            }
        )

        with patch("app.queue.redis.from_url", return_value=mock_redis):
            queue = RedisRequestQueue(mock_app)
            queue._process_request = Mock(return_value={"session_id": "s1"})  # type: ignore[method-assign]
            queue._publish_result_event = Mock()  # type: ignore[method-assign]
            queue._publish_result_event = Mock()  # type: ignore[method-assign]
            queue._ready_for_task = Mock(return_value=True)  # type: ignore[method-assign]
            queue._cleanup_vram_after_task = Mock()  # type: ignore[method-assign]

            task = {"id": "t1", "user_id": "u1", "session_id": "s1", "type": "text", "timestamp": time.time()}
            queue._process_single_task(task, "slow_processing_requests")

        # First expire call is on slow_processing_requests (processing TTL).
        ttl_calls = [c.args[1] for c in mock_redis.expire.call_args_list]
        assert len(ttl_calls) >= 1
        assert (
            ttl_calls[0]
            >= max(
                mock_app.config["SD_CLI_TIMEOUT"],
                mock_app.config["LLM_TIMEOUT"],
                mock_app.config["LTX_VIDEO_TIMEOUT"],
                mock_app.config["SD_CPP_TIMEOUT"],
            )
            + 120
        )
