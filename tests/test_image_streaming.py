# tests/test_image_streaming.py
"""Tests for _process_image_chat_task_stream error routing.

Verifies that when the multimodal model yields an LLM error string
(e.g. "⚠️ Failed to load image or audio file"), the queue routes
the response through _build_error_response (with "⚠️ " prefix),
NOT through _save_and_respond (which would save the raw text).
"""
import base64
from unittest.mock import MagicMock, patch

import pytest


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("utf-8")


class TestImageOnlyStreamErrorRouting:
    """Image-only branch of _process_image_chat_task_stream (no user text)."""

    @pytest.fixture
    def mock_multimodal_ok(self):
        """Multimodal module that streams a normal response (no error)."""
        m = MagicMock()
        m.available = True
        m.validate_image.return_value = (True, None)

        def fake_stream(*args, **kwargs):
            yield "A "
            yield "red "
            yield "car."

        m.process_image_with_text_stream = fake_stream
        return m

    @pytest.fixture
    def mock_multimodal_error(self):
        """Multimodal module that streams an LLM error token (like '⚠️ Failed to load image')."""
        m = MagicMock()
        m.available = True
        m.validate_image.return_value = (True, None)

        def fake_stream(*args, **kwargs):
            yield "⚠️ Failed to load image or audio file"

        m.process_image_with_text_stream = fake_stream
        return m

    def test_image_only_stream_publishes_normal_tokens(
        self, test_app, mock_multimodal_ok
    ):
        """Normal tokens are published to client and saved via _save_and_respond."""
        from app.queue import RedisRequestQueue

        test_app.modules["multimodal"] = mock_multimodal_ok
        with patch("redis.from_url", return_value=MagicMock()), \
             patch.object(RedisRequestQueue, "__init__", lambda self, app: None):
            q = RedisRequestQueue(test_app)
            q.app = test_app
            q.logger = test_app.logger

            with patch.object(q, "_publish_stream_token") as mock_pub, \
                 patch.object(q, "_save_and_respond") as mock_save, \
                 patch.object(q, "_build_error_response") as mock_err, \
                 patch.object(q, "_get_model_name", return_value="Qwen3VL-8B"), \
                 patch.object(q, "_unload_llamacpp_models", return_value=True), \
                 patch.object(q, "_unload_video_pipeline", return_value=True), \
                 patch.object(q, "_wait_for_vram", return_value=True), \
                 patch.object(q, "_is_task_cancelled", return_value=False):
                mock_save.return_value = {"session_id": "s1"}
                task = {"id": "t1", "data": {}}
                q._process_image_chat_task_stream(
                    task, _b64(b"fake-jpg"), "image/jpeg", "t.jpg", "",
                    "s1", "2026-06-04 12:00:00", "en", "u1", "neutral",
                )

        assert mock_pub.call_count == 3
        assert mock_save.called
        assert not mock_err.called

    def test_image_only_stream_routes_error_to_build_error_response(
        self, test_app, mock_multimodal_error
    ):
        """When multimodal yields an LLM error, queue routes to _build_error_response
        (which adds the '⚠️ ' prefix) instead of _save_and_respond."""
        from app.queue import RedisRequestQueue

        test_app.modules["multimodal"] = mock_multimodal_error
        with patch("redis.from_url", return_value=MagicMock()), \
             patch.object(RedisRequestQueue, "__init__", lambda self, app: None):
            q = RedisRequestQueue(test_app)
            q.app = test_app
            q.logger = test_app.logger

            with patch.object(q, "_publish_stream_token") as mock_pub, \
                 patch.object(q, "_save_and_respond") as mock_save, \
                 patch.object(q, "_build_error_response") as mock_err, \
                 patch.object(q, "_get_model_name", return_value="Qwen3VL-8B"), \
                 patch.object(q, "_unload_llamacpp_models", return_value=True), \
                 patch.object(q, "_unload_video_pipeline", return_value=True), \
                 patch.object(q, "_wait_for_vram", return_value=True), \
                 patch.object(q, "_is_task_cancelled", return_value=False):
                mock_err.return_value = {
                    "error": "⚠️ Failed to load image or audio file",
                    "is_error": True,
                    "session_id": "s1",
                }
                task = {"id": "t1", "data": {}}
                q._process_image_chat_task_stream(
                    task, _b64(b"fake-jpg"), "image/jpeg", "t.jpg", "",
                    "s1", "2026-06-04 12:00:00", "en", "u1", "neutral",
                )

        # Error must be routed through _build_error_response
        assert mock_err.called
        # NOT through _save_and_respond (which would store the raw error)
        assert not mock_save.called
        # The error token must NOT be published as a normal stream token
        # (the client will receive it via _build_error_response as a final event)
        assert not mock_pub.called


class TestImageTextStreamErrorRouting:
    """Image+text branch of _process_image_chat_task_stream (with user text)."""

    @pytest.fixture
    def mock_multimodal_error_late(self):
        """Multimodal streams many normal tokens, then yields an LLM error at the end."""
        m = MagicMock()
        m.available = True
        m.validate_image.return_value = (True, None)

        def fake_stream(*args, **kwargs):
            yield "some "
            yield "long "
            yield "description "
            yield "here. "
            yield "⚠️ HTTP error 500"

        m.process_image_with_text_stream = fake_stream
        return m

    def test_image_text_stream_routes_late_error_to_build_error_response(
        self, test_app, mock_multimodal_error_late
    ):
        """When a late token in the stream is an LLM error, the final response
        must be routed through _build_error_response."""
        from app.queue import RedisRequestQueue

        test_app.modules["multimodal"] = mock_multimodal_error_late
        with patch("redis.from_url", return_value=MagicMock()), \
             patch.object(RedisRequestQueue, "__init__", lambda self, app: None):
            q = RedisRequestQueue(test_app)
            q.app = test_app
            q.logger = test_app.logger

            with patch.object(q, "_publish_stream_token") as mock_pub, \
                 patch.object(q, "_save_and_respond") as mock_save, \
                 patch.object(q, "_build_error_response") as mock_err, \
                 patch.object(q, "_get_model_name", return_value="Qwen3VL-8B"), \
                 patch.object(q, "_unload_llamacpp_models", return_value=True), \
                 patch.object(q, "_unload_video_pipeline", return_value=True), \
                 patch.object(q, "_wait_for_vram", return_value=True), \
                 patch.object(q, "_is_task_cancelled", return_value=False):
                mock_err.return_value = {
                    "error": "⚠️ HTTP error 500",
                    "is_error": True,
                    "session_id": "s1",
                }
                task = {"id": "t1", "data": {}}
                # Use a long message_text to skip the image-only branch
                q._process_image_chat_task_stream(
                    task, _b64(b"fake-jpg"), "image/jpeg", "t.jpg",
                    "describe this image",  # message_text is non-empty
                    "s1", "2026-06-04 12:00:00", "en", "u1", "neutral",
                )

        # Final response must go through _build_error_response
        assert mock_err.called
        # Some normal tokens were published before the late error was detected
        assert mock_pub.call_count >= 1
        # NOT through _save_and_respond
        assert not mock_save.called


class TestImageStreamErrorModelName:
    """All error paths in _process_image_chat_task_stream must save with
    model_name='system' (not 'unknown') for consistent header display."""

    @pytest.fixture
    def q(self, test_app):
        from app.queue import RedisRequestQueue
        with patch("redis.from_url", return_value=MagicMock()), \
             patch.object(RedisRequestQueue, "__init__", lambda self, app: None):
            q = RedisRequestQueue(test_app)
            q.app = test_app
            q.logger = test_app.logger
            return q

    @staticmethod
    def _wire(q, *, save_return=None):
        """Patch all the helpers called by the early error paths."""
        return [
            patch.object(q, "_save_and_respond", return_value=save_return or {}),
            patch.object(q, "_unload_llamacpp_models", return_value=True),
            patch.object(q, "_unload_video_pipeline", return_value=True),
            patch.object(q, "_wait_for_vram", return_value=True),
        ]

    def test_multimodal_unavailable_uses_system(self, q):
        """When the multimodal module is missing/unavailable, the error reply
        must be saved with model_name='system'."""
        m = MagicMock()
        m.available = False
        q.app.modules["multimodal"] = m

        patches = self._wire(q)
        with patches[0] as mock_save, patches[1], patches[2], patches[3]:
            task = {"id": "t1", "data": {}}
            q._process_image_chat_task_stream(
                task, _b64(b"fake-jpg"), "image/jpeg", "t.jpg", "",
                "s1", "2026-06-04 12:00:00", "en", "u1", "neutral",
            )

        mock_save.assert_called_once()
        assert mock_save.call_args.args[2] == "system", (
            f"Expected model_name='system', got {mock_save.call_args.args[2]!r}"
        )
        assert mock_save.call_args.kwargs.get("is_error") is True

    def test_image_validation_failure_uses_system(self, q):
        """When validate_image rejects the image (e.g. WEBP/HEIC/AVIF), the
        error reply must be saved with model_name='system'."""
        m = MagicMock()
        m.available = True
        m.validate_image.return_value = (False, "Image format WEBP is not supported")
        q.app.modules["multimodal"] = m

        patches = self._wire(q)
        with patches[0] as mock_save, patches[1], patches[2], patches[3]:
            task = {"id": "t1", "data": {}}
            q._process_image_chat_task_stream(
                task, _b64(b"fake-webp"), "image/webp", "t.webp", "",
                "s1", "2026-06-04 12:00:00", "en", "u1", "neutral",
            )

        mock_save.assert_called_once()
        assert mock_save.call_args.args[2] == "system", (
            f"Expected model_name='system', got {mock_save.call_args.args[2]!r}"
        )
        assert mock_save.call_args.kwargs.get("is_error") is True

    def test_gpu_memory_unavailable_uses_system(self, q):
        """When VRAM wait times out, the error reply must be saved with
        model_name='system'."""
        m = MagicMock()
        m.available = True
        m.validate_image.return_value = (True, None)
        q.app.modules["multimodal"] = m

        with patch.object(q, "_save_and_respond") as mock_save, \
             patch.object(q, "_unload_llamacpp_models", return_value=True), \
             patch.object(q, "_unload_video_pipeline", return_value=True), \
             patch.object(q, "_wait_for_vram", return_value=False):
            task = {"id": "t1", "data": {}}
            q._process_image_chat_task_stream(
                task, _b64(b"fake-jpg"), "image/jpeg", "t.jpg", "",
                "s1", "2026-06-04 12:00:00", "en", "u1", "neutral",
            )

        mock_save.assert_called_once()
        assert mock_save.call_args.args[2] == "system", (
            f"Expected model_name='system', got {mock_save.call_args.args[2]!r}"
        )
        assert mock_save.call_args.kwargs.get("is_error") is True
