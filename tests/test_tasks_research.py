"""The research step: the shared loop, a read-only tool set, one step result."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.tasks import research, runner


class _App:
    def __init__(self):
        self.logger = None
        self.config = {}


def _context(cancel_check=lambda: False):
    return SimpleNamespace(
        app=_App(),
        step={"id": "s1", "tool": "research", "goal": "find the rate", "checkpoint_criteria": "a number"},
        context_digest="",
        cancel_check=cancel_check,
        lang="en",
        response_style="neutral",
        task_request="find the rate",
        plan_text="",
        user_answers="",
        user_id="u1",
        doc_ids=None,
    )


CATALOGUE = [
    {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}
    for name in ("web_search", "read_page", "rag_search", "history_search", "get_current_time", "image_gen")
]


def _call(name="web_search", arguments="{}", call_id="c1"):
    return {"id": call_id, "function": {"name": name, "arguments": arguments}}


def _run(context, replies, tool_calls=()):
    """Drive run_research with a scripted model; returns (result, calls, executed)."""
    calls: list[dict] = []
    executed: list[str] = []
    script = list(replies)
    tc_script = list(tool_calls)

    def fake_reasoning(app, prompt, cancel_check, lang="ru", temperature=None, status_callback=None, tools=None,
                       messages=None, tool_choice=None):
        calls.append({"tools": tools, "messages": [dict(message) for message in (messages or [])],
                      "tool_choice": tool_choice})
        return script.pop(0)

    def fake_execute(name, arguments, tool_context):
        executed.append(name)
        return "42"

    with (
        patch("app.tasks.runner.build_step_prompt", return_value="PROMPT"),
        patch("app.tools.get_tool_definitions", return_value=CATALOGUE),
        patch("app.tasks.runner._call_reasoning", side_effect=fake_reasoning),
        patch("app.tasks.runner.last_tool_calls", side_effect=lambda: tc_script.pop(0) if tc_script else []),
        patch("app.tools.execute_tool", side_effect=fake_execute),
    ):
        result = research.run_research(context)
    return result, calls, executed


@pytest.mark.unit
def test_a_plain_answer_comes_back_unchanged():
    result, calls, executed = _run(_context(), [("the rate is 1.08", {"prompt_tokens": 10})])
    assert result == "the rate is 1.08"
    assert executed == []
    assert len(calls) == 1


@pytest.mark.unit
def test_only_read_only_tools_are_advertised():
    _, calls, _ = _run(_context(), [("answer", {})])
    advertised = {item["function"]["name"] for item in calls[0]["tools"]}
    assert advertised == set(research.RESEARCH_TOOLS)
    assert "image_gen" not in advertised


@pytest.mark.unit
def test_a_tool_round_is_executed_and_its_result_reaches_the_transcript():
    result, calls, executed = _run(
        _context(),
        [("", {"prompt_tokens": 5}), ("the rate is 1.08", {})],
        tool_calls=[[_call()], []],
    )
    assert result == "the rate is 1.08"
    assert executed == ["web_search"]
    transcript = calls[1]["messages"]
    assert any(message["role"] == "tool" and message["content"] == "42" for message in transcript)
    assert any(message["role"] == "assistant" and message.get("tool_calls") for message in transcript)


@pytest.mark.unit
def test_the_last_round_withdraws_the_tools():
    # Four rounds of tool calls: the first three execute, the last is refused
    # because the tools are withdrawn there — the same rule chat follows.
    _, calls, executed = _run(
        _context(),
        [("", {}), ("", {}), ("", {}), ("", {})],
        tool_calls=[[_call()], [_call()], [_call()], [_call()]],
    )
    assert calls[0]["tool_choice"] is None
    assert calls[-1]["tool_choice"] == "none"
    assert len(executed) == research.RESEARCH_MAX_ROUNDS - 1


@pytest.mark.unit
def test_an_unparsable_call_is_refused_without_running_the_tool():
    _, _, executed = _run(
        _context(),
        [("", {}), ("", {}), ("", {}), ("", {})],
        tool_calls=[[_call(arguments="{not json")], [], [], []],
    )
    assert executed == []


@pytest.mark.unit
def test_cancellation_raises_step_aborted():
    with pytest.raises(runner.StepAborted):
        _run(_context(cancel_check=lambda: True), [("never", {})])


@pytest.mark.unit
def test_the_last_tool_result_answers_when_the_model_writes_nothing():
    result, _, executed = _run(
        _context(),
        [("", {}), ("", {}), ("", {}), ("", {})],
        tool_calls=[[_call()], [], [], []],
    )
    assert executed == ["web_search"]
    assert result == "42"
