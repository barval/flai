"""The request kernel.

One call to the multimodal model decides what a message needs: which subsystem
answers it, which context sources to load, and which tools to advertise. The
decision is a single JSON object, validated against a closed vocabulary so that
a hallucinated value degrades to the old router instead of reaching the queue.

The kernel is the first stage of the engine described in
``docs/superpowers/specs/2026-10-30-agentic-engine-design.md``. Everything in
this module is pure: no app context, no model call, no IO.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# The closed vocabulary. It mirrors the router's marker actions on purpose: the
# adapter maps a decision onto the dict shape that every downstream caller
# already understands, so nothing past ``process_message`` had to change.
KERNEL_INTENTS: tuple[str, ...] = (
    "none",
    "image",
    "video",
    "camera",
    "rag",
    "search",
    "history",
    "reasoning",
    "reasoning_web",
    "remember",
    "crawl",
)

INTENT_ACTIONS: dict[str, str] = {intent: intent for intent in KERNEL_INTENTS}

# A small model degrades quickly as the tool list grows; five to eight is the
# measured cliff, and an empty list is a legitimate answer meaning "no tool".
MAX_KERNEL_TOOLS = 6

# Only the memory flag is asked for: documents, history and web are already
# encoded by the intent, so a second opinion about them would be redundant — and
# every field the model is asked for is a field it can get wrong.
SOURCE_KEYS: tuple[str, ...] = ("memory",)

# A skill is a set of instructions, not a tool: the model names at most two and
# the runtime loads their bodies before the answer starts. Measured on
# Qwen3VL-8B: asked to call `activate_skill` itself, the model answered with a
# plain web search instead; naming the skill in the same decision where it
# already picks the intent is what makes it fire.
MAX_KERNEL_SKILLS = 2


@dataclass(frozen=True)
class SourceFlags:
    """Which context sources the answer stage should load.

    Grows one member per source that has a *consumer*. ``memory`` is the first:
    it drives ``skip_slm`` on the chat branch, so a request that needs nothing
    personal does not pay for a long-term-memory recall.

    Tri-state on purpose. ``None`` (the field was not answered) keeps the
    previous behaviour — silence must never remove context the user has come to
    expect — and only an explicit ``False`` skips the recall.
    """

    memory: bool | None = None


@dataclass(frozen=True)
class KernelDecision:
    """One validated kernel answer.

    ``tools`` carries three states on purpose: ``None`` means the model gave no
    tool list at all (no opinion — advertise everything), ``()`` means it gave an
    empty list (this message needs no tool — advertise nothing), and a non-empty
    tuple is the selection.

    ``sources`` is recorded but not consumed yet; the answer stages still load
    their context the way they always did. Wiring it is part of the engine work.
    """

    intent: str
    query: str
    sources: SourceFlags
    tools: tuple[str, ...] | None
    confidence: float
    skills: tuple[str, ...] = ()


def _first_json_object(text: str) -> str | None:
    """The first balanced JSON object in ``text`` (string-aware), or None.

    Copied from ``app.tasks.executor`` on purpose: importing the executor from
    the router path would pull the queue and the whole task subsystem into a
    hot code path for thirty lines of brace scanning.
    """
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        start = text.find("{", start + 1)
    return None


def _coerce_tools(raw: Any) -> tuple[str, ...] | None:
    """De-duplicate, drop non-strings and cap the list, preserving order.

    A missing or non-list field returns ``None`` — "no opinion" — which is a
    different statement from an empty list.
    """
    if not isinstance(raw, list):
        return None
    seen: list[str] = []
    for item in raw:
        if isinstance(item, str) and item and item not in seen:
            seen.append(item)
        if len(seen) >= MAX_KERNEL_TOOLS:
            break
    return tuple(seen)


def _coerce_sources(raw: Any) -> SourceFlags:
    if not isinstance(raw, dict):
        return SourceFlags()
    # ``isinstance(..., bool)`` rather than ``bool(...)``: a JSON-mode model that
    # emits "false" as a string must not read as true, and an absent field must
    # stay absent rather than become "no".
    return SourceFlags(**{key: value if isinstance(value := raw.get(key), bool) else None for key in SOURCE_KEYS})


def _coerce_confidence(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, value))


def _coerce_skills(raw: Any, known: set[str]) -> tuple[str, ...]:
    """Skill names the installation actually has, capped and de-duplicated.

    A name outside the catalog is dropped rather than loaded: the model cannot
    reach instructions that were never installed, and a typo must not cost the
    answer.
    """
    if isinstance(raw, str):
        # A model that answers with one name instead of a list still named a
        # skill; refusing it would only cost the instructions.
        raw = [raw]
    if not isinstance(raw, list):
        return ()
    chosen: list[str] = []
    for item in raw:
        if isinstance(item, str) and item in known and item not in chosen:
            chosen.append(item)
        if len(chosen) >= MAX_KERNEL_SKILLS:
            break
    return tuple(chosen)


def parse_kernel_decision(text: str | None, known_skills: set[str] | None = None) -> KernelDecision | None:
    """Parse a kernel reply, or return None when it cannot be trusted.

    None is the signal to fall back to the marker router. It is returned for
    every malformed shape rather than raising, because a small model's failure
    mode is a truncated or fenced object, not an exception.
    """
    if not text:
        return None
    payload = _first_json_object(text)
    if payload is None:
        return None
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    intent = data.get("intent")
    if not isinstance(intent, str) or intent not in INTENT_ACTIONS:
        return None

    query = data.get("query")
    return KernelDecision(
        intent=intent,
        query=query if isinstance(query, str) else "",
        sources=_coerce_sources(data.get("sources")),
        tools=_coerce_tools(data.get("tools")),
        confidence=_coerce_confidence(data.get("confidence")),
        skills=_coerce_skills(data.get("skills"), known_skills or set()),
    )


def decision_to_route(decision: KernelDecision, original_query: str, cam: Any = None) -> dict[str, Any]:
    """Convert a decision into the dict shape every downstream caller expects.

    The quirks are inherited from ``BaseModule._parse_router_response`` and are
    pinned by ``tests/test_kernel_route.py``: an image or video request keeps
    the user's original words (models echo history after the marker), a camera
    request resolves to a room code, and a downstream query longer than 1.5x the
    original is replaced by the original.
    """
    action = INTENT_ACTIONS[decision.intent]
    processed = decision.query.strip()

    if action in ("image", "video"):
        processed = original_query
    elif action == "camera":
        if not processed:
            processed = original_query
        if cam is not None and hasattr(cam, "room_names") and processed not in cam.room_names:
            extracted = cam.get_room_code(processed)
            if extracted:
                processed = extracted
    elif original_query and (not processed or len(processed) > len(original_query) * 1.5):
        processed = original_query

    if action == "none":
        processed = original_query

    if decision.skills:
        # A skill is a procedure for the chat loop, where the tools it names are
        # available: a request that matches one is answered by that loop even if
        # the intent was search or rag. The skill bodies are loaded before the
        # first round, so the model reads them as the instructions they are.
        action = "none"
        processed = original_query

    return {
        "action": action,
        "query": processed,
        "needs_reasoning": action in ("reasoning", "reasoning_web"),
        # None means "no opinion" (advertise every tool); () means "no tool
        # needed" (advertise none). The two must not collapse into one value.
        "tools": decision.tools,
        "memory": decision.sources.memory,
        "skills": decision.skills,
    }


# What the backend is actually asked for. llama-server (build b11889 as shipped in
# llama-swap v267) accepts only "text" or "json_object" and rejects a full schema
# with HTTP 400, so JSON mode plus the enum check in this module is the strongest
# guarantee the runtime gives us. The schema below stays as documentation of the
# shape and as the contract the parser enforces.
KERNEL_RESPONSE_FORMAT: dict[str, str] = {"type": "json_object"}

# Documentation of the reply shape, NOT something sent to the server: llama-server
# rejects a schema outright (see KERNEL_RESPONSE_FORMAT above). Tests assert on it
# so the documented contract and the parser cannot drift apart.
KERNEL_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(KERNEL_INTENTS)},
        "query": {"type": "string"},
        "sources": {
            "type": "object",
            "properties": {key: {"type": "boolean"} for key in SOURCE_KEYS},
        },
        "tools": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_KERNEL_TOOLS},
        "skills": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_KERNEL_SKILLS},
        "confidence": {"type": "number"},
    },
    "required": ["intent", "query"],
}

CATALOG_DESCRIPTION_CHARS = 60


def build_kernel_catalog(tool_definitions: list[dict[str, Any]]) -> str:
    """One line per tool: its name and the first line of its description.

    Full JSON Schemas are the largest fixed cost in a request (~2 000 tokens for
    eight tools); the catalog is ~15 tokens per tool and is enough to *choose*.
    The chosen tools get their schemas later, on the path that actually calls
    them.
    """
    lines: list[str] = []
    for item in tool_definitions:
        function = item.get("function") or {}
        name = function.get("name")
        if not name:
            continue
        description = (function.get("description") or "").strip().split("\n")[0]
        if len(description) > CATALOG_DESCRIPTION_CHARS:
            description = description[: CATALOG_DESCRIPTION_CHARS - 1].rstrip() + "…"
        lines.append(f"- {name}: {description}".rstrip())
    return "\n".join(lines)


def build_kernel_prompt(
    message: str, catalog: str, current_time: str, router_context: str, rooms: str = "", skills: str = ""
) -> str:
    """Render the kernel prompt.

    The order matters for the llama.cpp prefix cache: the instructions and the
    catalog are static, the time, the recent context and the message come last,
    so the model re-prefills only the tail of the prompt on every request.

    The template is English regardless of the user's language: the kernel
    returns a machine value (an enum, a phrase to search with), not user-facing
    prose, and the English twin is measurably smaller (spec section 7).
    """
    from app.utils import format_prompt

    prompt = format_prompt(
        "kernel.template",
        {
            "catalog": catalog,
            "skills": skills,
            "current_time": current_time,
            "rooms": rooms,
            "router_context": router_context or "",
            "user_query": message,
        },
        lang="en",
    )
    return prompt or ""


def memory_is_skipped(use_memory: bool | None) -> bool:
    """Whether the long-term-memory recall may be skipped for this request.

    Only an explicit ``False`` from the kernel skips it. ``None`` — the marker
    path, or a kernel reply without the field — keeps the previous behaviour,
    because silence must never remove context the user has come to expect.
    """
    return use_memory is False
