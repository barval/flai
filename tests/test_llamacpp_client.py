# tests/test_llamacpp_client.py
"""Tests for LlamaCppClient error handling.

Verifies that:
- `_format_user_error` always returns strings starting with "⚠️ "
- llama.cpp error messages are extracted from the JSON body (e.g. "Failed to load image")
- Direct chat/chat_stream with HTTP 400 yields "⚠️ Failed to load image or audio file"
  (not a generic "HTTP error 400")
"""
import json
from unittest.mock import MagicMock, patch

from app.llamacpp_client import (
    _extract_error_message,
    _format_user_error,
)


class TestExtractErrorMessage:
    """Pure-function tests for _extract_error_message."""

    def test_extracts_message_from_nested_error(self):
        resp = MagicMock()
        resp.json.return_value = {
            "error": {"code": 400, "message": "Failed to load image or audio file"}
        }
        resp.text = json.dumps(resp.json.return_value)
        assert _extract_error_message(resp) == "Failed to load image or audio file"

    def test_extracts_message_from_flat_error(self):
        resp = MagicMock()
        resp.json.return_value = {"error": "something went wrong"}
        resp.text = json.dumps(resp.json.return_value)
        assert _extract_error_message(resp) == "something went wrong"

    def test_falls_back_to_raw_text_on_invalid_json(self):
        resp = MagicMock()
        resp.json.side_effect = ValueError("not json")
        resp.text = "<html>500 Internal Server Error</html>"
        assert _extract_error_message(resp) == "<html>500 Internal Server Error</html>"

    def test_falls_back_when_error_field_missing(self):
        resp = MagicMock()
        resp.json.return_value = {"choices": []}
        resp.text = json.dumps(resp.json.return_value)
        assert _extract_error_message(resp) == json.dumps({"choices": []})

    def test_handles_empty_text(self):
        """Empty body returns empty string; the caller (e.g. _format_user_error)
        is responsible for falling back to a generic 'HTTP error' message."""
        resp = MagicMock()
        resp.json.side_effect = Exception("no body")
        resp.text = ""
        assert _extract_error_message(resp) == ""


class TestFormatUserError:
    """Pure-function tests for _format_user_error."""

    def test_starts_with_warning_prefix(self):
        resp = MagicMock()
        resp.json.return_value = {"error": {"message": "Failed to load image"}}
        resp.text = json.dumps(resp.json.return_value)
        out = _format_user_error(resp, "en")
        assert out.startswith("⚠️ ")
        assert "Failed to load image" in out

    def test_does_not_double_prefix(self):
        """If the extracted message already starts with ⚠️, don't add it again."""
        resp = MagicMock()
        resp.json.return_value = {"error": {"message": "⚠️ already prefixed"}}
        resp.text = json.dumps(resp.json.return_value)
        out = _format_user_error(resp, "en")
        # Should be exactly one "⚠️ " prefix
        assert out.count("⚠️") == 1
        assert out == "⚠️ already prefixed"

    def test_falls_back_to_translated_generic(self):
        """When extraction yields nothing, use translated generic fallback."""
        resp = MagicMock()
        resp.status_code = 502
        resp.json.side_effect = Exception("no body")
        resp.text = ""
        out = _format_user_error(resp, "ru")
        assert out.startswith("⚠️ ")
        # Russian generic fallback contains "HTTP" and the status code
        assert "502" in out


class TestChatStreamYieldsWarningPrefix:
    """Integration: LlamaSwapBackend.chat_stream yields ⚠️-prefixed error on HTTP 400."""

    def test_chat_stream_http_400_yields_warning_prefix(self):
        with patch("app.llamacpp_client.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 400
            mock_response.json.return_value = {
                "error": {
                    "code": 400,
                    "message": "Failed to load image or audio file",
                    "type": "invalid_request_error",
                }
            }
            mock_response.text = json.dumps(mock_response.json.return_value)
            mock_post.return_value = mock_response

            from app.llamacpp_client import LlamaSwapBackend
            backend = LlamaSwapBackend()
            cb = MagicMock()
            cb.can_execute.return_value = True
            with patch.object(backend, "_get_circuit_breaker", return_value=cb):
                gen = backend.chat_stream(
                    [{"role": "user", "content": "test"}],
                    model="multimodal",
                    config={"temperature": 0.7, "top_p": 0.9, "repeat_penalty": 1.1},
                    timeout=120,
                    lang="en",
                    model_type="multimodal",
                )
                tokens = list(gen)

        assert len(tokens) == 1
        assert tokens[0].startswith("⚠️")
        assert "Failed to load image" in tokens[0]

    def test_chat_stream_502_yields_warning_prefix(self):
        with patch("app.llamacpp_client.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 502
            mock_response.json.return_value = {"error": "Bad Gateway"}
            mock_response.text = json.dumps(mock_response.json.return_value)
            mock_post.return_value = mock_response

            from app.llamacpp_client import LlamaSwapBackend
            backend = LlamaSwapBackend()
            cb = MagicMock()
            cb.can_execute.return_value = True
            with patch.object(backend, "_get_circuit_breaker", return_value=cb):
                gen = backend.chat_stream(
                    [{"role": "user", "content": "test"}],
                    model="multimodal",
                    config={"temperature": 0.7, "top_p": 0.9, "repeat_penalty": 1.1},
                    timeout=120,
                    lang="en",
                    model_type="multimodal",
                )
                tokens = list(gen)

        assert len(tokens) == 1
        assert tokens[0].startswith("⚠️")
        assert "Bad Gateway" in tokens[0]

    def test_chat_stream_image_load_400_retries_once(self):
        """HTTP 400 with 'Failed to load image' triggers a 3s retry, then yields ⚠️."""
        with patch("app.llamacpp_client.requests.post") as mock_post, \
             patch("app.llamacpp_client.time.sleep") as mock_sleep:
            mock_response = MagicMock()
            mock_response.status_code = 400
            mock_response.json.return_value = {
                "error": {"message": "Failed to load image or audio file"}
            }
            mock_response.text = json.dumps(mock_response.json.return_value)
            mock_post.return_value = mock_response

            from app.llamacpp_client import LlamaSwapBackend
            backend = LlamaSwapBackend()
            cb = MagicMock()
            cb.can_execute.return_value = True
            with patch.object(backend, "_get_circuit_breaker", return_value=cb):
                gen = backend.chat_stream(
                    [{"role": "user", "content": "test"}],
                    model="multimodal",
                    config={"temperature": 0.7, "top_p": 0.9, "repeat_penalty": 1.1},
                    timeout=120,
                    lang="en",
                    model_type="multimodal",
                )
                tokens = list(gen)

        # 1st attempt fails, 1s sleep, 2nd attempt fails, then yields ⚠️
        assert mock_post.call_count == 2
        assert mock_sleep.called
        assert mock_sleep.call_args[0][0] == 1
        assert len(tokens) == 1
        assert tokens[0].startswith("⚠️")

    def test_chat_stream_non_image_400_does_not_retry(self):
        """HTTP 400 WITHOUT 'Failed to load image' does NOT trigger a retry."""
        with patch("app.llamacpp_client.requests.post") as mock_post, \
             patch("app.llamacpp_client.time.sleep") as mock_sleep:
            mock_response = MagicMock()
            mock_response.status_code = 400
            mock_response.json.return_value = {"error": {"message": "Invalid request body"}}
            mock_response.text = json.dumps(mock_response.json.return_value)
            mock_post.return_value = mock_response

            from app.llamacpp_client import LlamaSwapBackend
            backend = LlamaSwapBackend()
            cb = MagicMock()
            cb.can_execute.return_value = True
            with patch.object(backend, "_get_circuit_breaker", return_value=cb):
                gen = backend.chat_stream(
                    [{"role": "user", "content": "test"}],
                    model="multimodal",
                    config={"temperature": 0.7, "top_p": 0.9, "repeat_penalty": 1.1},
                    timeout=120,
                    lang="en",
                    model_type="multimodal",
                )
                tokens = list(gen)

        # Only 1 attempt, no retry
        assert mock_post.call_count == 1
        assert not mock_sleep.called
        assert len(tokens) == 1
        assert tokens[0].startswith("⚠️")
        assert "Invalid request body" in tokens[0]
