"""The engine's tool loop, with no Flask and no queue in it.

The loop was extracted from ``RedisRequestQueue._process_chat_with_tools``. It
knows four things and nothing else: how to ask the model for a round, how to run
the tools that round asked for, whether the turn was cancelled, and what to
report when the last round still returns a tool call. Every one of those is a
callable the caller supplies, so the same loop can drive a chat turn (in-memory
transcript, streamed tokens) and, in a later phase, a background task (a plan
stored in PostgreSQL).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.tools import tool_choice_for_iteration


@dataclass(frozen=True)
class Turn:
    """One model round.

    ``text`` is whatever content came with the round — a final answer, or the
    sentence the model wrote before asking for a tool. ``error`` is set when the
    round failed at the transport level; the loop then stops and hands the text
    to the caller's existing error path.
    """

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


@dataclass(frozen=True)
class LoopResult:
    """What the caller saves.

    ``last_tool_result`` is the fallback answer when the model never produced
    prose: a raw tool result is better than nothing, but it is only used when
    ``text`` is empty.
    """

    text: str
    last_tool_result: str
    error: str = ""
    cancelled: bool = False
    rounds: int = 0


def run_tool_loop(
    *,
    max_iterations: int,
    generate: Callable[[str | None], Turn],
    execute: Callable[[list[dict[str, Any]], str], str],
    is_cancelled: Callable[[], bool],
    on_final_round: Callable[[], None],
    on_cancelled: Callable[[], None] | None = None,
) -> LoopResult:
    """Generate, run the asked-for tools, repeat until an answer.

    ``generate`` receives the ``tool_choice`` for its round: ``None`` normally
    and ``"none"`` on the last one, where the tools are withdrawn so the model
    must answer with what it already has. ``execute`` returns the last tool
    result, which becomes the fallback answer if no round produces prose.
    """
    last_tool_result = ""
    text = ""
    rounds = 0

    for iteration in range(max_iterations):
        if is_cancelled():
            return LoopResult(text=text, last_tool_result=last_tool_result, cancelled=True, rounds=rounds)

        round_tool_choice = tool_choice_for_iteration(iteration, max_iterations)
        turn = generate(round_tool_choice)
        rounds = iteration + 1

        if turn.error:
            return LoopResult(text="", last_tool_result=last_tool_result, error=turn.error, rounds=rounds)

        if is_cancelled():
            # The text is already on the user's screen for a streamed turn, so
            # the caller saves it; only the tools are abandoned.
            if on_cancelled is not None:
                on_cancelled()
            return LoopResult(text=turn.text, last_tool_result=last_tool_result, cancelled=True, rounds=rounds)

        if turn.tool_calls:
            if round_tool_choice == "none":
                # A server that ignores tool_choice can still send one. Running it
                # would burn a call the loop has no room for, and keeping the
                # result would end the turn by showing raw tool output.
                on_final_round()
                return LoopResult(text=turn.text, last_tool_result="", rounds=rounds)
            last_tool_result = execute(turn.tool_calls, turn.text)
            continue

        text = turn.text
        return LoopResult(text=text, last_tool_result=last_tool_result, rounds=rounds)

    # Rounds exhausted without an answer; the caller decides between the last
    # tool result and an empty-response error.
    return LoopResult(text=text, last_tool_result=last_tool_result, rounds=rounds)
