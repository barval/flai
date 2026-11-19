"""Which progress stage a tool announces, and that the UI can render it.

A document request spends its time in the model composing the tool call: the SVG
schema took 25 s of argument generation before the tool itself ran for 0.6 s.
The name arrives in the first stream delta, so the stage is announced there.
"""

import json
import re
from pathlib import Path

import pytest

from app.queue import TOOL_STAGES, tool_stage

pytestmark = pytest.mark.unit


def test_a_document_is_announced_as_a_document():
    assert tool_stage("write_document", {"format": "md"}) == "writing_document"


def test_a_drawing_is_announced_as_a_drawing():
    assert tool_stage("write_document", {"format": "svg"}) == "drawing_svg"


def test_the_call_is_announced_before_its_arguments_are_known():
    # The stream carries the name first; the arguments arrive many seconds later.
    assert tool_stage("write_document") == "writing_document"


def test_editing_and_restoring_have_their_own_stages():
    assert tool_stage("edit_document") == "editing_document"
    assert tool_stage("restore_document_version") == "restoring_document"


def test_a_tool_without_a_stage_announces_nothing():
    assert tool_stage("activate_skill") is None


def test_every_stage_has_a_label_and_a_translation():
    js = Path("app/static/js/events.js").read_text(encoding="utf-8")
    html = Path("app/templates/chat.html").read_text(encoding="utf-8")
    mapped = set(re.findall(r"^\s+[a-z_]+: '(stage_[a-z_]+)',", js, re.M))
    for stage in sorted(set(TOOL_STAGES.values()) | {"drawing_svg"}):
        key = f"stage_{stage}"
        assert key in mapped, f"{stage} has no STAGE_LABEL_KEYS entry"
        assert f"'{key}'" in html, f"{stage} has no translation in chat.html"


class _FakeStream:
    """Just enough of a requests response to drive the SSE parser."""

    status_code = 200
    text = ""

    def __init__(self, lines):
        self._lines = lines

    def iter_lines(self):
        return iter(self._lines)

    def close(self):
        return None


def _sse(delta: dict) -> bytes:
    return ("data: " + json.dumps({"choices": [{"delta": delta}]})).encode()


@pytest.mark.unit
def test_the_stream_announces_the_call_before_any_argument(monkeypatch):
    """The name arrives first; the SVG arguments took 25 s more."""
    import app.llamacpp_client as client

    lines = [
        _sse({"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "write_document", "arguments": ""}}]}),
        _sse({"tool_calls": [{"index": 0, "function": {"arguments": '{"format": "svg"}'}}]}),
        b"data: [DONE]",
    ]
    monkeypatch.setattr(client.requests, "post", lambda *a, **kw: _FakeStream(lines))

    seen: list[str] = []
    backend = client.DirectLlamaBackend()
    chunks = list(
        backend.chat_stream(
            [{"role": "user", "content": "draw it"}],
            "test-model",
            {"context_length": 4096, "temperature": 0.7, "top_p": 0.9, "repeat_penalty": 1.1},
            10,
            "ru",
            tools=[{"type": "function", "function": {"name": "write_document", "parameters": {}}}],
            tool_call_started=seen.append,
        )
    )

    assert seen == ["write_document"]
    assert any(isinstance(chunk, dict) and chunk.get("type") == "tool_calls" for chunk in chunks)
