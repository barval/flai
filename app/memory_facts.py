# app/memory_facts.py
"""Parsing and gating for long-term memory facts.

The rule-based extractor in ``app/slm_rules.py`` only fires on explicit
first-person statements ("я работаю", "I prefer") and misses everything else:
measured on 30 real messages it produced zero facts, while the import path
stored raw questions as "facts". Extraction now runs on the resident model and
its answer is parsed here — strictly, because a wrong fact is worse than none.
"""

from __future__ import annotations

import json
import re
from typing import Any

MAX_FACT_CHARS = 200
MIN_CONFIDENCE = 0.5
VALID_KINDS = ("profile", "preference", "project", "instruction")

# A cheap cost filter, not a router: these markers only decide whether the
# exchange is worth one extra model call, and a missed fact is cheaper than an
# extraction on every message about the weather.
_PERSONAL_MARKERS = re.compile(
    r"\b(я|мне|меня|мной|мой|моя|моё|мои|моих|у меня|мы|нас|наш|наша|наше|наши|"
    r"i|me|my|mine|we|our|ours|myself)\b",
    re.IGNORECASE,
)
_EXPLICIT_REMEMBER = re.compile(r"(запомни|запомните|remember)", re.IGNORECASE)


def looks_personal(text: str) -> bool:
    """True when the message may carry a durable fact about the user."""
    body = (text or "").strip()
    if len(body) < 12:
        return False
    return bool(_PERSONAL_MARKERS.search(body) or _EXPLICIT_REMEMBER.search(body))


_CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)
_LATIN = re.compile(r"[a-z]", re.IGNORECASE)


def detect_language(text: str, fallback: str = "ru") -> str:
    """Language of the message itself, not of the interface.

    The extraction used the account's UI language, so a Russian message on an
    English account went through the English prompt — and the model answered
    ``[]`` for "I collect vinyl and listen to jazz" while the same prompt in
    Russian extracted two facts. Facts are stored in the language they were
    spoken in, which also keeps lexical recall matching.
    """
    body = text or ""
    cyrillic = len(_CYRILLIC.findall(body))
    latin = len(_LATIN.findall(body))
    if cyrillic == 0 and latin == 0:
        return fallback
    return "ru" if cyrillic >= latin else "en"


def auto_extract_enabled(mode: str, platform: str) -> bool:
    """Whether automatic extraction may run on this host.

    ``auto`` means "only where a model is already resident and fast": on a
    CPU-only host the same call costs ~30 s of the model that chat is using, so
    the explicit "remember this" path stays available but the per-message
    extraction does not run unless the admin forces it with ``SLM_AUTO_EXTRACT=on``.
    """
    value = (mode or "auto").strip().lower()
    if value in ("off", "0", "false", "no"):
        return False
    if value in ("on", "1", "true", "yes"):
        return True
    return (platform or "").strip().lower() != "cpu"


def _clean(text: str) -> str:
    body = " ".join(str(text).split())
    if len(body) > MAX_FACT_CHARS:
        body = body[: MAX_FACT_CHARS - 3].rstrip() + "..."
    return body


def _candidate_objects(raw: str) -> list[Any]:
    """Pull the first JSON array out of a model answer, fences and prose aside."""
    body = (raw or "").strip()
    if not body:
        return []
    fenced = re.search(r"```(?:json)?\s*(.+?)```", body, re.DOTALL)
    if fenced:
        body = fenced.group(1).strip()
    start = body.find("[")
    end = body.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        parsed = json.loads(body[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def parse_memory_facts(raw: str) -> list[dict[str, Any]]:
    """Validate what the model produced into storable facts.

    Drops questions (the model likes to echo the user's question as a "fact"),
    low-confidence guesses, entries without a kind and duplicates. Returns at
    most five facts, in the order the model ranked them.
    """
    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _candidate_objects(raw):
        if not isinstance(item, dict):
            continue
        text = _clean(item.get("fact") or item.get("text") or "")
        if len(text) < 8 or text.endswith("?"):
            continue
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            continue
        if confidence < MIN_CONFIDENCE:
            continue
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in VALID_KINDS:
            kind = "profile" if kind in ("fact", "personal") else "preference"
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        facts.append({"fact": text, "kind": kind, "confidence": round(min(confidence, 1.0), 2)})
        if len(facts) == 5:
            break
    return facts


def merge_into_profile(facts: list[dict[str, Any]], previous: str = "") -> str:
    """Text block for the always-injected user profile.

    Profile facts are short and stable; the caller stores the result as one
    ``kind=profile`` fact so the block never grows without a rewrite.
    """
    lines: list[str] = []
    if previous.strip():
        lines.append(previous.strip())
    for item in facts:
        if item.get("kind") != "profile":
            continue
        text = _clean(item.get("fact", ""))
        if text and text not in lines:
            lines.append(text)
    return "\n".join(lines[-12:])
