"""The tool loop is a pure function: no Flask, no queue, no model."""

from __future__ import annotations

import pytest

from app.engine.loop import LoopResult, Turn, run_tool_loop


def _call(name: str = "calculator", call_id: str = "c1") -> dict:
    return {"id": call_id, "function": {"name": name, "arguments": "{}"}}


def _scripted(turns: list[Turn]):
    """A generate callable that returns the turns in order and records the tool_choice it got."""
    seen: list[str | None] = []

    def generate(tool_choice):
        seen.append(tool_choice)
        return turns[len(seen) - 1]

    return generate, seen


@pytest.mark.unit
def test_a_tool_round_is_executed_and_the_next_round_answers():
    generate, seen = _scripted([Turn(tool_calls=[_call()]), Turn(text="the answer")])
    executed: list[tuple[list[dict], str]] = []

    result = run_tool_loop(
        max_iterations=5,
        generate=generate,
        execute=lambda calls, content: executed.append((calls, content)) or "42",
        is_cancelled=lambda: False,
        on_final_round=lambda: pytest.fail("no tool call was refused"),
    )

    assert result == LoopResult(text="the answer", last_tool_result="42", rounds=2)
    assert len(executed) == 1
    assert seen == [None, None]


@pytest.mark.unit
def test_the_assistant_text_travels_with_its_tool_calls():
    generate, _ = _scripted([Turn(text="let me check", tool_calls=[_call()]), Turn(text="done")])
    executed: list[str] = []

    run_tool_loop(
        max_iterations=5,
        generate=generate,
        execute=lambda calls, content: executed.append(content) or "42",
        is_cancelled=lambda: False,
        on_final_round=lambda: None,
    )

    assert executed == ["let me check"]


@pytest.mark.unit
def test_the_last_round_withdraws_the_tools():
    generate, seen = _scripted([Turn(tool_calls=[_call()]) for _ in range(2)])
    executed: list[list[dict]] = []

    run_tool_loop(
        max_iterations=2,
        generate=generate,
        execute=lambda calls, content: executed.append(calls) or "42",
        is_cancelled=lambda: False,
        on_final_round=lambda: None,
    )

    assert seen[-1] == "none"


@pytest.mark.unit
def test_a_tool_call_on_the_last_round_is_refused_and_its_result_dropped():
    # A server that ignores tool_choice can still send one. Running it would only
    # burn a call the loop has no room for, and keeping its result would end the
    # turn by showing raw tool output as the answer.
    generate, _ = _scripted([Turn(tool_calls=[_call()]), Turn(tool_calls=[_call()])])
    executed: list[list[dict]] = []
    refused: list[bool] = []

    result = run_tool_loop(
        max_iterations=2,
        generate=generate,
        execute=lambda calls, content: executed.append(calls) or "42",
        is_cancelled=lambda: False,
        on_final_round=lambda: refused.append(True),
    )

    assert result.text == ""
    assert result.last_tool_result == ""
    assert len(executed) == 1
    assert refused == [True]


@pytest.mark.unit
def test_an_error_turn_ends_the_loop_without_running_tools():
    generate, _ = _scripted([Turn(error="HTTP 502")])
    executed: list[list[dict]] = []

    result = run_tool_loop(
        max_iterations=5,
        generate=generate,
        execute=lambda calls, content: executed.append(calls) or "42",
        is_cancelled=lambda: False,
        on_final_round=lambda: None,
    )

    assert result.error == "HTTP 502"
    assert executed == []


@pytest.mark.unit
def test_cancellation_before_a_round_stops_the_loop():
    generate, seen = _scripted([Turn(text="never")])

    result = run_tool_loop(
        max_iterations=5,
        generate=generate,
        execute=lambda calls, content: "42",
        is_cancelled=lambda: True,
        on_final_round=lambda: None,
    )

    assert result.cancelled is True
    assert result.text == ""
    assert seen == []


@pytest.mark.unit
def test_cancellation_after_a_round_keeps_the_text_already_generated():
    # The streaming path publishes tokens as they arrive; that text is on the
    # user's screen and must survive in the saved message.
    cancelled = {"now": False}

    def generate(tool_choice):
        cancelled["now"] = True
        return Turn(text="half an ans")

    notified: list[bool] = []

    result = run_tool_loop(
        max_iterations=5,
        generate=generate,
        execute=lambda calls, content: "42",
        is_cancelled=lambda: cancelled["now"],
        on_final_round=lambda: None,
        on_cancelled=lambda: notified.append(True),
    )

    assert result.cancelled is True
    assert result.text == "half an ans"
    assert notified == [True]


@pytest.mark.unit
def test_one_iteration_still_produces_an_answer():
    generate, seen = _scripted([Turn(text="answer")])

    result = run_tool_loop(
        max_iterations=1,
        generate=generate,
        execute=lambda calls, content: pytest.fail("no tools on a single-round loop"),
        is_cancelled=lambda: False,
        on_final_round=lambda: None,
    )

    assert result.text == "answer"
    assert seen == ["none"]
