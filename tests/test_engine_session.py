"""A chat turn's transcript and its event sink, behind the session interface."""

from __future__ import annotations

import pytest

from app.engine.session import ChatSession


def _session():
    events: list[tuple[str, dict]] = []
    stages: list[str] = []
    tokens: list[str] = []
    messages: list[dict] = []
    session = ChatSession(
        messages=messages,
        is_cancelled=lambda: False,
        publish=lambda event, payload: events.append((event, payload)),
        report_stage=stages.append,
        on_token=tokens.append,
    )
    return session, messages, events, stages, tokens


@pytest.mark.unit
def test_the_session_appends_to_the_caller_transcript():
    session, messages, *_ = _session()
    session.append_assistant("checking", [{"id": "c1"}])
    session.append_tool("c1", "42")
    # The same list the caller holds must be mutated: the caller saves from it.
    assert messages == [
        {"role": "assistant", "content": "checking", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "42"},
    ]
    assert session.messages is messages


@pytest.mark.unit
def test_an_assistant_round_without_tool_calls_is_plain_content():
    session, messages, *_ = _session()
    session.append_assistant("hello", [])
    assert messages == [{"role": "assistant", "content": "hello"}]


@pytest.mark.unit
def test_cancellation_and_events_delegate():
    session, _, events, stages, tokens = _session()
    assert session.is_cancelled() is False
    session.report_stage("searching_web")
    session.publish("tool_call", {"tool_name": "calculator"})
    session.on_token("tok")
    assert stages == ["searching_web"]
    assert events == [("tool_call", {"tool_name": "calculator"})]
    assert tokens == ["tok"]
