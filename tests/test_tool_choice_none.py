"""``tool_choice`` must reach both request payloads, streamed and not."""

from __future__ import annotations

import pytest

from app.llamacpp_client import DirectLlamaBackend, LlamaSwapBackend


class _ChatResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}]}


class _StreamResponse:
    status_code = 200
    text = ""

    def iter_lines(self, decode_unicode=False):
        return iter(())

    def close(self):
        return None


def _capture(monkeypatch, response):
    captured: dict = {}

    def fake_post(url, json=None, timeout=None, stream=False, **kwargs):
        captured.update(json or {})
        return response

    monkeypatch.setattr("app.llamacpp_client.requests.post", fake_post)
    monkeypatch.setattr("app.llamacpp_client._record_prompt_tokens", lambda *args, **kwargs: None)
    return captured


@pytest.mark.unit
def test_the_direct_backend_sends_it_on_a_chat_call(monkeypatch):
    captured = _capture(monkeypatch, _ChatResponse())
    DirectLlamaBackend().chat(
        [{"role": "user", "content": "hi"}],
        model="m",
        config={"context_length": 4096},
        timeout=5,
        lang="en",
        tool_choice="none",
    )
    assert captured["tool_choice"] == "none"


@pytest.mark.unit
def test_absence_is_not_sent_as_null(monkeypatch):
    captured = _capture(monkeypatch, _ChatResponse())
    DirectLlamaBackend().chat(
        [{"role": "user", "content": "hi"}], model="m", config={"context_length": 4096}, timeout=5, lang="en"
    )
    assert "tool_choice" not in captured


@pytest.mark.unit
def test_the_streaming_path_sends_it_too(monkeypatch):
    # The chat tool loop streams, so this is the path that matters.
    captured = _capture(monkeypatch, _StreamResponse())
    backend = DirectLlamaBackend()
    list(
        backend.chat_stream(
            [{"role": "user", "content": "hi"}],
            model="m",
            config={"context_length": 4096},
            timeout=5,
            lang="en",
            tool_choice="none",
        )
    )
    assert captured["tool_choice"] == "none"


@pytest.mark.unit
def test_the_swap_backend_sends_it(monkeypatch):
    captured = _capture(monkeypatch, _ChatResponse())
    LlamaSwapBackend().chat(
        [{"role": "user", "content": "hi"}],
        model="m",
        config={"context_length": 4096},
        timeout=5,
        lang="en",
        tool_choice="none",
    )
    assert captured["tool_choice"] == "none"


@pytest.mark.unit
def test_the_swap_backend_streaming_path_sends_it(monkeypatch):
    imported = _capture(monkeypatch, _StreamResponse())
    backend = LlamaSwapBackend()
    list(
        backend.chat_stream(
            [{"role": "user", "content": "hi"}],
            model="m",
            config={"context_length": 4096},
            timeout=5,
            lang="en",
            tool_choice="none",
        )
    )
    assert imported["tool_choice"] == "none"
