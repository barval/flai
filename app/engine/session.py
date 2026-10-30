"""What the loop needs from whoever is holding the turn.

A chat turn lives in memory for the duration of one queue request; a background
task lives in PostgreSQL and survives a restart. The loop must not know which it
is holding, so it only ever calls these five things.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


class Session(Protocol):
    """The interface the engine loop is written against."""

    messages: list[dict[str, Any]]

    def append_assistant(self, content: str, tool_calls: list[dict[str, Any]]) -> None: ...

    def append_tool(self, tool_call_id: str, content: str) -> None: ...

    def is_cancelled(self) -> bool: ...

    def report_stage(self, stage: str) -> None: ...

    def publish(self, event: str, payload: dict[str, Any]) -> None: ...


@dataclass
class ChatSession:
    """A chat turn: a list the caller already owns, plus the queue's callbacks.

    ``messages`` is not copied. The caller builds the system and user messages,
    the session appends the assistant/tool exchanges, and the caller saves from
    the same list afterwards.
    """

    messages: list[dict[str, Any]]
    is_cancelled: Callable[[], bool]
    publish: Callable[[str, dict[str, Any]], None]
    report_stage: Callable[[str], None]
    on_token: Callable[[str], None] = field(default=lambda _text: None)

    def append_assistant(self, content: str, tool_calls: list[dict[str, Any]]) -> None:
        message: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            message["tool_calls"] = tool_calls
        self.messages.append(message)

    def append_tool(self, tool_call_id: str, content: str) -> None:
        self.messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": content})

    def report_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> None:
        self.publish("tool_call", {"tool_name": tool_name, "arguments": arguments})

    def report_tool_result(self, tool_name: str, preview: str) -> None:
        self.publish("tool_result", {"tool_name": tool_name, "result_preview": preview})
