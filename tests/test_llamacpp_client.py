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


class TestReasoningStreamFilter:
    """Incremental plain-text reasoning strip for reasoning-model streams."""

    def _stream(self, text, step=50, hold=500):
        from app.llamacpp_client import _ReasoningStreamFilter

        f = _ReasoningStreamFilter(hold=hold)
        parts = []
        for i in range(0, len(text), step):
            out = f.feed(text[i : i + step])
            if out:
                parts.append(out)
        tail = f.flush()
        if tail:
            parts.append(tail)
        return "".join(parts)

    def test_clean_answer_passes_through_unchanged(self):
        """Healthy deepseek-split path: content has no markers → exact copy."""
        answer = "Просто исправь страницу, вот готовый код: <div>ok</div>"
        assert self._stream(answer) == answer

    def test_leaked_cot_is_stripped(self):
        """Plain-text CoT with markers before the answer is stripped."""
        cot = (
            "Let me review the user request carefully. "
            "The user asked us to fix the white screen issue. "
            "We must output only corrected HTML file content. "
            "Final Answer: <div>fixed</div> this is the answer to show"
        )
        result = self._stream(cot)
        assert "Let me review" not in result
        assert "The user asked" not in result
        assert "<div>fixed</div>" in result

    def test_long_reasoning_between_markers_held_back(self):
        """Reasoning chunks between markers are not streamed out early."""
        reasoning = "I should check the requirements. " * 3
        answer = "The answer to display starts here and continues for a while."
        full = reasoning + answer
        result = self._stream(full)
        assert "The answer to display" in result
        assert reasoning not in result

    def test_no_retraction_after_emit(self):
        """Emitted prefix never shortens — no flicker."""
        from app.llamacpp_client import _ReasoningStreamFilter

        f = _ReasoningStreamFilter(hold=50)
        emitted = ""
        text = (
            "Let me think about this. The user asked something. "
            + "x" * 300
            + "This is the real answer now. "
            + "x" * 200
        )
        for i in range(0, len(text), 30):
            out = f.feed(text[i : i + 30])
            if out:
                emitted += out
            # Once text is emitted, the current candidate (stripped buffer)
            # must always START with what we already emitted.
            from app.llamacpp_client import _strip_generic_reasoning

            candidate = _strip_generic_reasoning(f._buf)
            assert candidate.startswith(emitted)  # no retraction
        f.flush()


class TestExtractErrorMessage:
    """Pure-function tests for _extract_error_message."""

    def test_extracts_message_from_nested_error(self):
        resp = MagicMock()
        resp.json.return_value = {"error": {"code": 400, "message": "Failed to load image or audio file"}}
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
        with (
            patch("app.llamacpp_client.requests.post") as mock_post,
            patch("app.llamacpp_client.time.sleep") as mock_sleep,
        ):
            mock_response = MagicMock()
            mock_response.status_code = 400
            mock_response.json.return_value = {"error": {"message": "Failed to load image or audio file"}}
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
        assert any(call[0][0] == 1 for call in mock_sleep.call_args_list)
        assert len(tokens) == 1
        assert tokens[0].startswith("⚠️")

    def test_chat_stream_non_image_400_does_not_retry(self):
        """HTTP 400 WITHOUT 'Failed to load image' does NOT trigger a retry."""
        with (
            patch("app.llamacpp_client.requests.post") as mock_post,
            patch("app.llamacpp_client.time.sleep") as mock_sleep,
        ):
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


class TestAdaptiveHold:
    """Slow streams (CPU) must not buffer the whole answer behind the hold-back.

    Regression: on a CPU box (~1.6 tok/s) typical reasoning answers (600-1400
    chars) never exceed the 1500-char loop-guard hold during generation, so
    the entire response arrived as one block after generation finished.
    The hold must shrink adaptively while generation is still running.
    """

    def test_loop_guard_shrinks_hold_when_stream_is_slow(self):
        """Feeding text slowly (many small feeds) must release a prefix."""
        from app.llamacpp_client import _LoopGuard

        g = _LoopGuard()
        # 300 feeds of 2 chars = 600 chars total, well under the 1500 hold.
        # The adaptive shrink must open the window long before the end.
        released = 0
        for _ in range(0, 600, 2):
            out = g.feed("ab")
            released += len(out)
        assert released > 0, "nothing streamed for a 600-char slow answer"

    def test_loop_guard_keeps_hold_for_fast_streams(self):
        """A few big chunks (fast GPU stream) keep the full hold-back."""
        from app.llamacpp_client import _LoopGuard

        g = _LoopGuard()
        # 1400 chars in 2 large chunks: still under hold, must hold everything.
        out = g.feed("x" * 700) + g.feed("x" * 700)
        assert out == ""

    def test_loop_guard_still_detects_loop(self):
        """Loop detection keeps working after the hold has shrunk."""
        from app.llamacpp_client import _LoopGuard

        g = _LoopGuard()
        block = "Повторяющийся блок текста для проверки петли. " * 20  # > 3x window
        emitted = g.feed(block[:200])
        for i in range(200, len(block), 10):
            emitted += g.feed(block[i : i + 10])
            if g.loop_detected:
                break
        assert g.loop_detected
        # The emitted prefix must be shorter than the full block (loop tail
        # retracted), proving detection still fires with a shrunk hold.
        assert len(emitted) < len(block)

    def test_loop_guard_hold_floor(self):
        """The shrunk hold never goes below the floor (300 chars)."""
        from app.llamacpp_client import _LoopGuard

        g = _LoopGuard()
        # Many tiny feeds: after adaptive shrink (floor=300), a 250-char
        # answer must still be held entirely; and at floor the limit is
        # len(buf) - 300, so 250 chars emit nothing.
        released = 0
        for _ in range(125):
            released += len(g.feed("ab"))
        assert released == 0, "250 chars must stay behind the floor hold"

    def test_reasoning_filter_shrinks_hold_when_stream_is_slow(self):
        """_ReasoningStreamFilter with markers must stream on slow feeds too."""
        from app.llamacpp_client import _ReasoningStreamFilter

        f = _ReasoningStreamFilter()
        marker = "The user asked something. "
        # Slow stream: marker + 900 chars of answer in 2-char feeds.
        text = marker + "y" * 900
        released = 0
        for i in range(0, len(text), 2):
            released += len(f.feed(text[i : i + 2]))
        released += len(f.flush())
        assert released >= 900, "answer must stream out on a slow feed"
        assert "y" * 100 in text  # sanity


class TestChatStreamReasoningTailFlush:
    """The loop-guard hold-back tail must be flushed on a clean reasoning stream.

    Regression: LlamaSwapBackend used to flush _ReasoningStreamFilter but not
    _LoopGuard, silently dropping the last ~1500 chars (or the whole answer when
    it was shorter than the hold) on every clean reasoning response.
    """

    def _sse_lines(self, chunks):
        for chunk in chunks:
            yield f"data: {json.dumps(chunk)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    def _run_chat_stream(self, chunks):
        from app.llamacpp_client import LlamaSwapBackend

        with patch("app.llamacpp_client.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.iter_lines.return_value = self._sse_lines(chunks)
            mock_post.return_value = mock_response

            backend = LlamaSwapBackend()
            cb = MagicMock()
            cb.can_execute.return_value = True
            with patch.object(backend, "_get_circuit_breaker", return_value=cb):
                gen = backend.chat_stream(
                    [{"role": "user", "content": "test"}],
                    model="reasoning",
                    config={
                        "temperature": 0.7,
                        "top_p": 0.9,
                        "repeat_penalty": 1.6,
                        "context_length": 4096,
                    },
                    timeout=120,
                    lang="en",
                    model_type="reasoning",
                )
                return [t for t in gen if isinstance(t, str)]

    def test_short_answer_is_not_dropped(self):
        """A clean answer shorter than the loop hold must arrive in full."""
        answer = "Выводы из предоставленных данных: краткий ответ модели"
        tokens = self._run_chat_stream([{"choices": [{"delta": {"content": answer, "reasoning_content": None}}]}])
        joined = "".join(tokens)
        assert answer in joined

    def test_long_answer_tail_is_kept(self):
        """A long answer must not lose its trailing hold-back tail."""
        answer = " ".join(
            f"Новость №{i}: компания {chr(65 + i % 26)} объявила о выпуске {i}-го по счёту отчёта. " for i in range(60)
        )
        tokens = self._run_chat_stream([{"choices": [{"delta": {"content": answer, "reasoning_content": None}}]}])
        joined = "".join(tokens)
        assert joined == answer
        assert answer[-50:] in joined
