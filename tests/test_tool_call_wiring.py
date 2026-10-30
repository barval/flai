"""The loop's call site, not just the helpers it uses.

``_run_tool_calls`` is exercised on a stub that provides only what it touches —
constructing a real queue pulls in redis, Qdrant and worker threads for no gain
in coverage.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from app.queue import RedisRequestQueue
from app.tools import ToolCallLoopGuard


class _Stub:
    """Just enough object for the unbound method under test."""

    def __init__(self, app):
        self.app = app
        self.logger = logging.getLogger("test")

    def _publish_stream_event(self, *args, **kwargs):
        return None


def _call(tool: str, arguments: str, call_id: str = "c1") -> dict:
    return {"id": call_id, "function": {"name": tool, "arguments": arguments}}


def _run(app, tool_calls, loop_guard=None):
    messages: list[dict] = []
    stub = _Stub(app)
    with (
        patch("app.queue.execute_tool", return_value="tool said something") as executed,
        patch("flask_babel.gettext", lambda text: text),
    ):
        RedisRequestQueue._run_tool_calls(
            stub, {"id": "task1"}, messages, tool_calls, "user", "en", loop_guard=loop_guard
        )
    return messages, executed


@pytest.mark.unit
def test_broken_arguments_reject_the_call_without_running_the_tool(app):
    messages, executed = _run(app, [_call("calculator", "{broken")])
    assert executed.call_count == 0
    tool_messages = [message for message in messages if message["role"] == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "c1"
    assert "not valid JSON" in tool_messages[0]["content"]


@pytest.mark.unit
def test_a_healthy_call_still_runs(app):
    messages, executed = _run(app, [_call("calculator", '{"expression": "2+2"}')])
    assert executed.call_count == 1
    tool_messages = [message for message in messages if message["role"] == "tool"]
    assert tool_messages[0]["content"] == "tool said something"


@pytest.mark.unit
def test_the_third_identical_call_is_refused_at_the_call_site(app):
    guard = ToolCallLoopGuard()
    call = _call("calculator", '{"expression": "2+2"}')
    executed_total = 0
    for _ in range(3):
        _, executed = _run(app, [call], loop_guard=guard)
        executed_total += executed.call_count
    assert executed_total == 2, "the third identical call must not reach the tool"


@pytest.mark.unit
def test_the_refusal_message_matches_the_call_it_answers(app):
    guard = ToolCallLoopGuard()
    call = _call("calculator", '{"expression": "2+2"}', call_id="abc")
    for _ in range(2):
        _run(app, [call], loop_guard=guard)
    messages, _ = _run(app, [call], loop_guard=guard)
    refusals = [message for message in messages if message["role"] == "tool"]
    assert refusals[0]["tool_call_id"] == "abc"
    assert "refused" in refusals[0]["content"]


@pytest.mark.unit
def test_arguments_are_repaired_before_the_tool_sees_them(app):
    messages, executed = _run(app, [_call("calculator", '{"expression": "2+2"}')])
    assert executed.call_count == 1
    assert executed.call_args.args[1] == {"expression": "2+2"}
