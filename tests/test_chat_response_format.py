"""``response_format`` must reach the request payload, and must not appear when unused."""

from __future__ import annotations

import pytest

from app.llamacpp_client import DirectLlamaBackend


class _FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"choices": [{"message": {"content": '{"intent": "none"}'}}]}


def _capture(monkeypatch) -> dict:
    captured: dict = {}

    def fake_post(url, json=None, timeout=None, **kwargs):
        captured.update(json or {})
        return _FakeResponse()

    monkeypatch.setattr("app.llamacpp_client.requests.post", fake_post)
    monkeypatch.setattr("app.llamacpp_client._record_prompt_tokens", lambda *args, **kwargs: None)
    return captured


@pytest.mark.unit
def test_response_format_reaches_the_request_payload(monkeypatch):
    captured = _capture(monkeypatch)
    DirectLlamaBackend().chat(
        [{"role": "user", "content": "hi"}],
        model="m",
        config={"context_length": 4096},
        timeout=5,
        lang="en",
        response_format={"type": "json_object"},
    )
    assert captured["response_format"] == {"type": "json_object"}


@pytest.mark.unit
def test_absent_response_format_is_not_sent(monkeypatch):
    captured = _capture(monkeypatch)
    DirectLlamaBackend().chat(
        [{"role": "user", "content": "hi"}],
        model="m",
        config={"context_length": 4096},
        timeout=5,
        lang="en",
    )
    assert "response_format" not in captured


@pytest.mark.unit
def test_the_swap_backend_also_sends_it(monkeypatch):
    # llama-swap is the default deployment backend; the two payload builders are
    # separate code, so one test cannot stand in for the other.
    from app.llamacpp_client import LlamaSwapBackend

    captured = _capture(monkeypatch)
    LlamaSwapBackend().chat(
        [{"role": "user", "content": "hi"}],
        model="m",
        config={"context_length": 4096},
        timeout=5,
        lang="en",
        response_format={"type": "json_object"},
    )
    assert captured["response_format"] == {"type": "json_object"}
