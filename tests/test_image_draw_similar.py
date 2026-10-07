# tests/test_image_draw_similar.py
"""Tests for the «draw something similar» route.

A chat message with attached images is classified by the multimodal model,
which can now emit the [-IMAGE-] marker. The image-chat handlers must route
that marker to _process_image_gen_task with the attached images as reference
examples (described + real aspect ratio appended to the SD prompt).
"""

import base64
from unittest.mock import Mock, patch

import pytest

# Minimal valid 1x1 PNG (same bytes as tests/test_image_edit.py)
_PNG_B64 = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05"
    b"\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
).decode("utf-8")


@pytest.mark.unit
class TestImageDrawSimilarRouting:
    """The [-IMAGE-] marker must be routed to image generation with images."""

    @pytest.fixture(autouse=True)
    def _no_workers(self):
        with patch("app.queue.RedisRequestQueue.start_worker"):
            yield

    def _make_queue(self, multimodal):
        from app.queue import RedisRequestQueue

        app = Mock()
        app.config = {"REDIS_URL": "redis://localhost:6379/0", "SECRET_KEY": "test-secret-key"}
        app.logger = Mock()
        base = Mock()
        base._ = lambda message, lang="en": message
        app.modules = {"base": base, "multimodal": multimodal}
        redis = Mock()
        redis.blpop.return_value = None
        redis.llen.return_value = 0
        redis.scard.return_value = 0
        pipe = Mock()
        pipe.execute.return_value = []
        redis.pipeline.return_value = pipe
        with patch("app.queue.redis.from_url", return_value=redis):
            queue = RedisRequestQueue(app)
        queue._publish_stream_event = Mock()
        queue._publish_stream_token = Mock()
        queue._save_and_respond = Mock(return_value={})
        queue._build_error_response = Mock()
        queue._is_task_cancelled = Mock(return_value=False)
        queue._get_model_name = Mock(return_value="test-model")
        return queue

    def test_non_streaming_image_chat_routes_image_marker_to_image_gen(self):
        """A reply starting with [-IMAGE-] is re-routed to SD generation."""
        multimodal = Mock()
        multimodal.available = True
        multimodal.validate_image.return_value = (True, None)
        multimodal.process_images_with_text = Mock(return_value=("[-IMAGE-] Нарисуй что-то похожее", None))

        queue = self._make_queue(multimodal)
        queue._process_image_gen_task = Mock(return_value={"status": "completed"})

        result = queue._process_image_chat_task(
            ["a", "b"],
            "image/png",
            "photo.png",
            "Нарисуй что-то похожее",
            "s1",
            "2026-10-07 12:00:00",
            "ru",
            "u1",
        )

        queue._process_image_gen_task.assert_called_once_with(
            "Нарисуй что-то похожее", "s1", "u1", "ru", "neutral", images=["a", "b"]
        )
        queue._save_and_respond.assert_not_called()
        assert result == {"status": "completed"}

    def test_streaming_image_chat_routes_image_marker_to_image_gen(self):
        """Streaming buffers early tokens and routes [-IMAGE-] to SD generation."""
        multimodal = Mock()
        multimodal.available = True
        multimodal.validate_image.return_value = (True, None)
        multimodal.process_images_with_text_stream = Mock(return_value=iter(["[-IMAGE-] ", "Нарисуй", " похожее"]))

        queue = self._make_queue(multimodal)
        queue._process_image_gen_task = Mock(return_value={"status": "completed"})
        task = {"id": "t1", "user_id": "u1", "session_id": "s1", "user_class": 2}

        result = queue._process_image_chat_task_stream(
            task,
            ["a", "b"],
            "image/png",
            "photo.png",
            "Нарисуй что-то похожее",
            "s1",
            "2026-10-07 12:00:00",
            "ru",
            "u1",
        )

        queue._process_image_gen_task.assert_called_once_with(
            "Нарисуй похожее", "s1", "u1", "ru", "neutral", task=task, images=["a", "b"]
        )
        queue._publish_stream_token.assert_not_called()
        assert result == {"status": "completed"}

    def test_empty_image_generation_query_saves_error(self):
        """A bare [-IMAGE-] marker without a query is an error, not a crash."""
        multimodal = Mock()
        multimodal.available = True
        multimodal.validate_image.return_value = (True, None)
        multimodal.process_image_with_text = Mock(return_value=("[-IMAGE-]    ", None))

        queue = self._make_queue(multimodal)
        queue._process_image_gen_task = Mock(return_value={"status": "completed"})

        queue._process_image_chat_task(
            "img",
            "image/png",
            "photo.png",
            "Нарисуй что-то похожее",
            "s1",
            "2026-10-07 12:00:00",
            "ru",
            "u1",
        )

        queue._process_image_gen_task.assert_not_called()
        queue._save_and_respond.assert_called_once()
        args = queue._save_and_respond.call_args[0]
        assert "Image generation request was empty" in args[1]
        assert queue._save_and_respond.call_args[1]["is_error"] is True


@pytest.mark.unit
class TestReferenceExampleAspect:
    """Reference descriptions embed the real aspect ratio of each example."""

    @pytest.fixture(autouse=True)
    def _no_workers(self):
        with patch("app.queue.RedisRequestQueue.start_worker"):
            yield

    def _make_queue(self, multimodal):
        from app.queue import RedisRequestQueue

        app = Mock()
        app.config = {"REDIS_URL": "redis://localhost:6379/0", "SECRET_KEY": "test-secret-key"}
        app.logger = Mock()
        base = Mock()
        base._ = lambda message, lang="en": message
        app.modules = {"base": base, "multimodal": multimodal}
        redis = Mock()
        redis.blpop.return_value = None
        redis.llen.return_value = 0
        redis.scard.return_value = 0
        pipe = Mock()
        pipe.execute.return_value = []
        redis.pipeline.return_value = pipe
        with patch("app.queue.redis.from_url", return_value=redis):
            queue = RedisRequestQueue(app)
        return queue

    def test_collect_reference_examples_appends_real_aspect(self):
        """A valid reference image gets its real dimensions appended."""
        multimodal = Mock()
        multimodal.available = True
        multimodal.describe_images_for_context = Mock(return_value=[(_PNG_B64, "Красное яблоко", None)])

        queue = self._make_queue(multimodal)
        sections = queue._collect_reference_examples([_PNG_B64], "ru")

        multimodal.describe_images_for_context.assert_called_once_with([_PNG_B64], "ru")
        assert sections == ["1. Красное яблоко\nAspect ratio: 1×1 (square)"]

    def test_collect_reference_examples_skips_unreadable_image_size(self):
        """An undecodable image keeps the description but no aspect line."""
        multimodal = Mock()
        multimodal.available = True
        multimodal.describe_images_for_context = Mock(return_value=[("not-base64!", "Зелёное яблоко", None)])

        queue = self._make_queue(multimodal)
        sections = queue._collect_reference_examples(["not-base64!"], "ru")

        assert sections == ["1. Зелёное яблоко"]

    def test_collect_reference_examples_skips_failed_descriptions(self):
        """Failed descriptions are collected as errors, not appended."""
        multimodal = Mock()
        multimodal.available = True
        multimodal.describe_images_for_context = Mock(return_value=[("img", None, "loc:vision failed")])

        queue = self._make_queue(multimodal)
        sections = queue._collect_reference_examples(["img"], "ru")

        assert sections == []


@pytest.mark.unit
class TestGetImageDimensions:
    def test_valid_png(self):
        from app.utils import get_image_dimensions

        assert get_image_dimensions(_PNG_B64) == (1, 1)

    def test_data_url_prefix(self):
        from app.utils import get_image_dimensions

        assert get_image_dimensions(f"data:image/png;base64,{_PNG_B64}") == (1, 1)

    def test_invalid_data_returns_none(self):
        from app.utils import get_image_dimensions

        assert get_image_dimensions("not-base64!") is None
        assert get_image_dimensions("") is None
