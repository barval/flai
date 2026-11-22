"""Tool calls the model printed as text, and a filter that keeps them out of chat.

Small models sometimes answer with the call as prose instead of a structured
``tool_call`` (observed with Qwen3.6 on a tool-restricted round):

    <tool_call> <function=write_document> <parameter=name> scene.svg ...

The RLM actor has parsed this shape since v12.5. Chat reuses the same parser and,
on top of it, stops forwarding the text at the first marker: measured, the model
narrated, printed a whole SVG inside an escaped ``<parameter>`` and the user
watched XML for minutes while the model kept generating.
"""

from __future__ import annotations

import contextlib
import html
import json
import re
from typing import Any

_LEAKED_FUNCTION_START_RE = re.compile(r"<function=(?P<name>\w+)\s*>", re.IGNORECASE)

_LEAKED_PARAM_RE = re.compile(r"<parameter=(?P<key>\w+)\s*>(?P<value>[\s\S]*?)</parameter>")

# Inline arguments: final(answer="..."), python(code="..."). Only quoted
# assignments are treated as a leak so normal prose mentioning final(answer)
# stays untouched.
_LEAKED_ASSIGN_RE = re.compile(r'(?P<key>\w+)\s*=\s*"(?P<value>(?:[^"\\]|\\.)*)"')

_LEAKED_WRAPPER_RE = re.compile(r"</?tool_call>", re.IGNORECASE)

_LEAKED_TAIL_RE = re.compile(r"<(?:tool_call|function=\w+)[\s\S]*$", re.IGNORECASE)

# A marker that opens a printed call. The name must follow ``<function=``: a bare
# ``<tool_call>`` in prose about the syntax is not a call, and truncating an
# explanation would be worse than the leak. The observed output always carries
# the function block inside the wrapper.
_LEAKED_MARKER_RE = re.compile(r"<function=\w|<\|tool_call\|>", re.IGNORECASE)


def _leaked_function_blocks(text: str) -> list[tuple[int, int, str, str]]:
    """(start, end, name, body) for every printed <function=…> block.

    ``end`` covers a closing ``</function>``/``</tool_call>`` when present and
    the rest of the text otherwise — the unclosed tail is a real Qwen3.6
    output shape, so it must not survive into the answer.
    """
    starts = list(_LEAKED_FUNCTION_START_RE.finditer(text))
    blocks: list[tuple[int, int, str, str]] = []
    for i, match in enumerate(starts):
        body_start = match.end()
        limit = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        segment = text[body_start:limit]
        close = segment.find("</function>")
        if close != -1:
            body = segment[:close]
            end = body_start + close + len("</function>")
        else:
            tool_close = segment.find("</tool_call>")
            if tool_close != -1:
                body = segment[:tool_close]
                end = body_start + tool_close + len("</tool_call>")
            else:
                body = segment
                end = len(text)
        blocks.append((match.start(), end, match.group("name"), body))
    return blocks


def _leaked_args(body: str) -> dict[str, str]:
    """Parameters from a leaked block: XML <parameter> or key="value" form.

    Values are unescaped: a model that prints its call as text also escapes the
    content, so an SVG arrives as ``&lt;svg … &quot;`` and would otherwise be
    stored as literal entities.
    """
    args: dict[str, str] = {}
    for pm in _LEAKED_PARAM_RE.finditer(body):
        args[pm.group("key")] = html.unescape(pm.group("value"))
    if not args:
        for am in _LEAKED_ASSIGN_RE.finditer(body):
            value = am.group("value")
            with contextlib.suppress(TypeError, ValueError):
                value = json.loads(f'"{value}"')
            args[am.group("key")] = html.unescape(value)
    return args


def parse_leaked_tool_calls(text: str) -> tuple[list[dict[str, Any]], str]:
    """Extract tool calls the model printed as text (Claude-style XML blocks).

    Returns (tool_calls, remaining_text) where tool_calls follow the
    OpenAI structure (id, function.name, JSON-serialized arguments dict)
    and remaining_text is the input with the leaked blocks removed.
    Calls with no <parameter> blocks (e.g. final(answer) inline form)
    are parsed as {"answer": ...} from the block body.
    """
    calls: list[dict[str, Any]] = []
    blocks = _leaked_function_blocks(text)
    for idx, (_start, _end, name, body) in enumerate(blocks):
        args = _leaked_args(body)
        if not args:
            body_text = body.strip()
            if body_text:
                args = {"answer": body_text} if name == "final" else {"code": body_text}
        if args:
            calls.append(
                {
                    "id": f"leaked_{idx}",
                    "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                }
            )
    remaining = text
    for start, end, _name, _body in reversed(blocks):
        remaining = remaining[:start] + remaining[end:]
    # Orphan closing tags / wrapper remnants and a still-unparseable tail must
    # never reach the user as an «answer».
    remaining = re.sub(r"</tool_call>\s*\"?", "", remaining)
    remaining = re.sub(r"</?parameter>|</?function>", "", remaining)
    remaining = _LEAKED_WRAPPER_RE.sub("", remaining)
    remaining = _LEAKED_TAIL_RE.sub("", remaining)
    return calls, remaining


class LeakedCallFilter:
    """Streaming filter: forward prose, stop at the first printed call.

    The marker can be split across chunks, so the tail of the text is held back
    until it cannot be the beginning of one. :attr:`leaked` is the signal to stop
    the generation: the model is dumping the call (often a whole SVG) as text.
    """

    HOLD = 24

    def __init__(self) -> None:
        self._pending = ""
        self.leaked = False

    def feed(self, chunk: str) -> str:
        """The text safe to show now; may lag by up to ``HOLD`` characters."""
        if self.leaked:
            return ""
        self._pending += chunk
        match = _LEAKED_MARKER_RE.search(self._pending)
        if match:
            self.leaked = True
            # The wrapper is part of the call, not prose: `<tool_call>` may sit just
            # before the `<function=` marker the filter caught.
            head = re.sub(r"</?tool_call>\s*$", "", self._pending[: match.start()], flags=re.IGNORECASE)
            self._pending = ""
            return head
        if len(self._pending) <= self.HOLD:
            return ""
        emit = self._pending[: -self.HOLD]
        self._pending = self._pending[-self.HOLD :]
        return emit

    def flush(self) -> str:
        """Whatever is still held back when the stream ended without a call."""
        if self.leaked:
            return ""
        text, self._pending = self._pending, ""
        return text
