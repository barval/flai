# tests/test_rlm_streaming.py
"""RLM model turns must use the streaming path.

A non-streaming call applies the model's configured timeout (120 s on the CPU
seed) to the WHOLE generation, so a long deep-analysis turn died with
"Timeout (120 s) when calling the model" in the middle of the step. Streaming
resets the read timeout on every chunk, exactly like a chat request.
"""

import pytest

from modules.rlm import _chat_turn


class _FakeLlamacpp:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    def chat_stream(self, messages, **kwargs):
        self.calls.append(kwargs)
        yield from self.chunks

    def chat(self, *args, **kwargs):  # pragma: no cover - must never be used
        raise AssertionError("RLM must not use the non-streaming chat() call")


def test_text_chunks_are_collected():
    fake = _FakeLlamacpp(["Разбей ", "документ ", "на сцены."])

    result = _chat_turn(fake, [{"role": "user", "content": "q"}], lang="ru")

    assert result == {"content": "Разбей документ на сцены.", "tool_calls": []}
    assert fake.calls and fake.calls[0]["model_type"] == "reasoning"


def test_tool_calls_are_collected_from_the_stream():
    fake = _FakeLlamacpp([{"type": "tool_calls", "tool_calls": [{"id": "1", "function": {"name": "final"}}]}])

    result = _chat_turn(fake, [{"role": "user", "content": "q"}], lang="ru", tools=[{"function": {"name": "final"}}])

    assert result["tool_calls"][0]["function"]["name"] == "final"
    assert result["content"] == ""


def test_backend_error_string_is_returned_as_is():
    fake = _FakeLlamacpp(["Таймаут (120с) при вызове модели."])

    result = _chat_turn(fake, [{"role": "user", "content": "q"}], lang="ru")

    assert isinstance(result, str)
    assert "Таймаут" in result


def test_chat_stream_is_the_only_model_call():
    fake = _FakeLlamacpp(["ok"])

    _chat_turn(fake, [{"role": "user", "content": "q"}], lang="en")

    assert len(fake.calls) == 1
    with pytest.raises(AssertionError):
        fake.chat([])
