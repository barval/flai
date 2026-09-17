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

    def test_strip_generic_reasoning_strips_leaked_cot(self, mock_app, mock_redis):
        """Queue safety-net strip removes plain-text CoT leaked into content."""
        from app.queue import RedisRequestQueue

        with patch("app.queue.redis.from_url", return_value=mock_redis):
            queue = RedisRequestQueue(mock_app)
            leaked = (
                "Let me review the user request carefully. "
                "The user asked to fix the white screen. "
                "Final Answer: here is the corrected HTML file that the user "
                "sees. It must replace the empty white screen the user "
                'reported. <div class="app"><div class="header">FLAI</div>'
                '<div class="content">the app now renders correctly</div>'
                "</div> and there is enough text here."
            )
            result = queue._strip_generic_reasoning(leaked)
            assert "Let me review" not in result
            assert "The user asked" not in result
            assert "here is the corrected HTML" in result

    def test_strip_generic_reasoning_leaves_clean_answer(self, mock_app, mock_redis):
        """Clean answer without reasoning markers passes through unchanged."""
        from app.queue import RedisRequestQueue

        with patch("app.queue.redis.from_url", return_value=mock_redis):
            queue = RedisRequestQueue(mock_app)
            clean = "Готово, вот исправленный код: <div>ok</div>"
            assert queue._strip_generic_reasoning(clean) == clean


@pytest.mark.unit
class TestWorkerStartGuard:
    """CLI processes must not start queue workers; start_worker is idempotent.

    Regression: `docker exec flai-web flask admin-password ...` loaded the app,
    started fast/slow worker threads (daemon=False), and then hung forever:
    the CLI process silently became a second queue consumer alongside gunicorn,
    stealing tasks (its logs went to the lost docker-exec stdout) and serving
    them with a stale app state — searches returned ⚠️ "0 results" with no
    trace in docker logs.
    """

    @pytest.fixture(autouse=True)
    def _no_workers(self):
        """Prevent real worker threads from starting during tests."""
        with patch("app.queue.RedisRequestQueue.start_worker"):
            yield

    @pytest.fixture
    def mock_app(self):
        app = Mock()
        app.config = {"REDIS_URL": "redis://localhost:6379/0", "SECRET_KEY": "test-secret-key"}
        app.logger = Mock()
        return app

    @pytest.fixture
    def mock_redis(self):
        redis = Mock()
        redis.hlen.return_value = 0
        return redis

    def test_start_workers_false_does_not_start_worker(self, mock_app, mock_redis):
        """RedisRequestQueue(app, start_workers=False) must not call start_worker()."""
        from app.queue import RedisRequestQueue

        with patch("app.queue.redis.from_url", return_value=mock_redis):
            queue = RedisRequestQueue(mock_app, start_workers=False)
        mock_app.logger.info.assert_any_call("RedisRequestQueue: workers NOT started (CLI/non-server process)")
        assert not hasattr(queue, "_workers_started")

    def test_start_worker_is_idempotent(self, mock_app, mock_redis):
        """A second start_worker() call must not spawn duplicate workers."""
        from app.queue import RedisRequestQueue

        with patch("app.queue.redis.from_url", return_value=mock_redis):
            queue = RedisRequestQueue(mock_app, start_workers=False)
            # Simulate the first start_worker() run (the autouse fixture patched
            # start_worker on the class, so call the internals directly).
            queue._shutdown_event = __import__("threading").Event()
            queue._workers_started = True
            with patch("app.queue.threading.Thread") as thread_cls:
                thread_cls.return_value = Mock()
                # Second call must be a no-op thanks to the _workers_started guard.
                RedisRequestQueue.start_worker(queue)
                assert thread_cls.call_count == 0  # no new threads on retry

    def test_is_cli_process_detects_flask(self):
        """_is_cli_process() is True when argv[0] ends with 'flask'."""
        from app import _is_cli_process

        with patch("app.sys.argv", ["/usr/local/bin/flask", "admin-password", "x"]):
            assert _is_cli_process() is True
        with patch("app.sys.argv", ["/usr/local/bin/gunicorn", "-c", "gunicorn_config.py", "wsgi:app"]):
            assert _is_cli_process() is False
        with patch("app.sys.argv", ["wsgi.py"]):
            assert _is_cli_process() is False


@pytest.mark.unit
class TestMultimodalStageProgress:
    """Multimodal-stream paths must publish task_progress stages.

    Image+text chat and simple chat answered directly by the multimodal model
    previously showed no emoji status / seconds counter while the model was
    working (especially slow on CPU). The stage is emitted up front and the
    frontend auto-removes it on the first stream_token.
    """

    @pytest.fixture(autouse=True)
    def _no_workers(self):
        """Prevent worker threads from starting during tests."""
        with patch("app.queue.RedisRequestQueue.start_worker"):
            yield

    @pytest.fixture
    def mock_app(self):
        app = Mock()
        app.config = {"REDIS_URL": "redis://localhost:6379/0", "SECRET_KEY": "test-secret-key"}
        app.logger = Mock()
        return app

    @pytest.fixture
    def mock_redis(self):
        redis = Mock()
        redis.blpop.return_value = None
        pipe = Mock()
        pipe.execute.return_value = []
        redis.pipeline.return_value = pipe
        return redis

    def _make_queue(self, mock_app, mock_redis, base=None, multimodal=None):
        """RedisRequestQueue stub with SSE/result methods mocked away."""
        from app.queue import RedisRequestQueue

        mock_app.modules = {"base": base or Mock(), "multimodal": multimodal or Mock()}
        with patch("app.queue.redis.from_url", return_value=mock_redis):
            queue = RedisRequestQueue(mock_app)
        queue._publish_stream_event = Mock()
        queue._publish_stream_token = Mock()
        queue._save_and_respond = Mock(return_value={})
        queue._build_error_response = Mock()
        queue._is_task_cancelled = Mock(return_value=False)
        queue._get_model_name = Mock(return_value="test-model")
        return queue

    def test_image_chat_stream_publishes_analyzing_image(self, mock_app, mock_redis):
        """Image+text chat publishes 'analyzing_image' until the first token."""
        multimodal = Mock()
        multimodal.available = True
        multimodal.validate_image.return_value = (True, None)
        multimodal.process_image_with_text_stream = Mock(return_value=iter(["Analyzed image"]))

        queue = self._make_queue(mock_app, mock_redis, multimodal=multimodal)
        task = {"id": "t1", "user_id": "u1", "session_id": "s1"}

        queue._process_image_chat_task_stream(
            task,
            "data:image/png;base64,AAAA",
            "image/png",
            "photo.png",
            "What is in this photo?",
            "s1",
            "2026-09-17 12:00:00",
            "ru",
            "u1",
        )

        queue._publish_stream_event.assert_any_call(task, "task_progress", {"stage": "analyzing_image"})

    def test_image_chat_stream_publishes_stage_without_text(self, mock_app, mock_redis):
        """Image-only chat (no text) also publishes 'analyzing_image'."""
        multimodal = Mock()
        multimodal.available = True
        multimodal.validate_image.return_value = (True, None)
        multimodal.process_image_with_text_stream = Mock(return_value=iter(["Analyzed image"]))

        queue = self._make_queue(mock_app, mock_redis, multimodal=multimodal)
        task = {"id": "t1", "user_id": "u1", "session_id": "s1"}

        queue._process_image_chat_task_stream(
            task,
            "data:image/png;base64,AAAA",
            "image/png",
            "photo.png",
            "",
            "s1",
            "2026-09-17 12:00:00",
            "ru",
            "u1",
        )

        queue._publish_stream_event.assert_any_call(task, "task_progress", {"stage": "analyzing_image"})

    def test_chat_with_tools_stream_publishes_thinking(self, mock_app, mock_redis):
        """Streaming simple chat publishes 'reasoning_thinking' before generation."""
        base = Mock()
        base.llamacpp = Mock()
        base.llamacpp.chat_stream = Mock(return_value=iter(["Hello"]))
        base._get_context_for_model.return_value = ""

        queue = self._make_queue(mock_app, mock_redis, base=base)
        task = {"id": "t1", "user_id": "u1", "session_id": "s1"}

        with (
            patch("app.queue.format_prompt", return_value="system"),
            patch("app.queue.get_tool_definitions", return_value=None),
            patch("app.queue.MAX_TOOL_ITERATIONS", 1),
            patch("modules.base.get_style_instruction", return_value=""),
            patch("modules.base.response_language_name", return_value="Russian"),
        ):
            queue._process_chat_with_tools(
                task, "hello", "2026-09-17 12:00:00", "s1", "u1", "ru", "neutral", stream=True
            )

        queue._publish_stream_event.assert_any_call(task, "task_progress", {"stage": "reasoning_thinking"})

    def test_chat_with_tools_non_streaming_skips_thinking(self, mock_app, mock_redis):
        """Non-streaming chat must NOT publish a progress stage (no removal event)."""
        base = Mock()
        base.llamacpp = Mock()
        base.llamacpp.chat.return_value = "Hello"
        base._get_context_for_model.return_value = ""

        queue = self._make_queue(mock_app, mock_redis, base=base)
        task = {"id": "t1", "user_id": "u1", "session_id": "s1"}

        with (
            patch("app.queue.format_prompt", return_value="system"),
            patch("app.queue.get_tool_definitions", return_value=None),
            patch("app.queue.MAX_TOOL_ITERATIONS", 1),
            patch("modules.base.get_style_instruction", return_value=""),
            patch("modules.base.response_language_name", return_value="Russian"),
        ):
            queue._process_chat_with_tools(
                task, "hello", "2026-09-17 12:00:00", "s1", "u1", "ru", "neutral", stream=False
            )

        queue._publish_stream_event.assert_not_called()
