"""Tests verifying that error responses do not include
response_time / response_style / completion_tokens in the result dict
sent to the client — these would otherwise cause the UI to render
⏱️ / 🚀 / 🤖 decorations on error message headers.
"""
from unittest.mock import MagicMock, patch

import pytest


def _build_success_dict_stub(**overrides):
    """Return a minimal _build_success_response result for tests."""
    base = {
        "response": "⚠️ something",
        "session_id": "s1",
        "model_used": "system",
        "assistant_timestamp": "ts",
        "response_time": 0.1,
        "is_error": True,
        "message_id": 42,
    }
    base.update(overrides)
    return base


class TestSaveAndRespondOmitsHeaderFieldsOnError:
    """_save_and_respond must strip ⏱️/🚀/🤖 markers from the result
    dict when is_error=True (success replies keep them)."""

    @pytest.fixture
    def q(self, test_app):
        from app.queue import RedisRequestQueue
        with patch("redis.from_url", return_value=MagicMock()), \
             patch.object(RedisRequestQueue, "__init__", lambda self, app: None):
            q = RedisRequestQueue(test_app)
            q.app = test_app
            q.logger = test_app.logger
            return q

    def test_error_result_omits_response_time(self, q):
        with patch("app.queue.save_message", return_value=42), \
             patch.object(q, "_build_success_response",
                          return_value=_build_success_dict_stub()):
            result = q._save_and_respond(
                "s1", "⚠️ something", "system", 0.1,
                is_error=True, response_style="neutral",
            )
        assert "response_time" not in result, (
            "is_error=True must strip response_time to prevent ⏱️ in header"
        )

    def test_error_result_omits_response_style(self, q):
        with patch("app.queue.save_message", return_value=42), \
             patch.object(q, "_build_success_response",
                          return_value=_build_success_dict_stub()):
            result = q._save_and_respond(
                "s1", "⚠️ something", "system", 0.1,
                is_error=True, response_style="neutral",
            )
        assert "response_style" not in result, (
            "is_error=True must strip response_style to prevent 🤖 in header"
        )

    def test_error_result_omits_completion_tokens(self, q):
        with patch("app.queue.save_message", return_value=42), \
             patch.object(q, "_build_success_response",
                          return_value=_build_success_dict_stub()):
            result = q._save_and_respond(
                "s1", "⚠️ something", "system", 0.1,
                is_error=True, response_style="neutral",
            )
        assert "completion_tokens" not in result, (
            "is_error=True must strip completion_tokens to prevent 🚀 in header"
        )

    def test_success_result_keeps_header_fields(self, q):
        """Sanity check: successful replies still include all header fields."""
        success_stub = {
            "response": "Hello!",
            "session_id": "s1",
            "model_used": "Qwen3-4B",
            "assistant_timestamp": "ts",
            "response_time": 0.5,
            "is_error": False,
            "message_id": 42,
        }
        with patch("app.queue.save_message", return_value=42), \
             patch.object(q, "_build_success_response", return_value=success_stub):
            result = q._save_and_respond(
                "s1", "Hello!", "Qwen3-4B", 0.5,
                is_error=False, response_style="neutral",
            )
        assert "response_time" in result
        assert "response_style" in result
        assert "completion_tokens" in result


class TestBuildErrorResponseOmitsResponseTime:
    """_build_error_response must not return response_time in the dict
    sent to the client (it stays in DB via save_message for analytics)."""

    @pytest.fixture
    def q(self, test_app):
        from app.queue import RedisRequestQueue
        with patch("redis.from_url", return_value=MagicMock()), \
             patch.object(RedisRequestQueue, "__init__", lambda self, app: None):
            q = RedisRequestQueue(test_app)
            q.app = test_app
            q.logger = test_app.logger
            return q

    def test_error_response_has_no_response_time(self, q):
        # _build_error_response imports save_message locally with `from .db import ...`,
        # so we patch the source module's attribute.
        with patch("app.db.save_message", return_value=99):
            result = q._build_error_response("s1", "Image format WEBP not supported", 0.1, "ru")

        assert "response_time" not in result, (
            "_build_error_response must not include response_time in the client-facing "
            "dict (avoids ⏱️ in the error message header)"
        )
        assert result["error"] == "Image format WEBP not supported"
        assert result["is_error"] is True
        assert result["message_id"] == 99
