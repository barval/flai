"""Measure the request kernel against a fixed dataset.

The harness answers one question: is the kernel at least as good as the marker
router it replaces? It runs the same cases through both and reports intent
accuracy, exact tool match, the number of falsely selected tools and a
per-language split — a Russian message under an English-only prompt is exactly
the regression this is meant to catch.

The metric functions are pure and unit-tested; only :func:`run_evaluation`
talks to a model, so the harness itself is never part of CI.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_TIME = "2026-01-01 12:00"

# Only the chat branch consumes a tool selection: every other intent hands the
# request to a subsystem with its own tools. Scoring tool choice for those cases
# would count a harmless field as an error.
TOOL_SCORING_INTENTS: tuple[str, ...] = ("none",)


@dataclass(frozen=True)
class EvalCase:
    """One labelled request."""

    message: str
    lang: str
    expected_intent: str
    expected_tools: tuple[str, ...] = ()
    note: str = ""


def load_dataset(path: Path) -> list[EvalCase]:
    """Read a JSONL dataset of ``{message, lang, expected_intent, expected_tools, note}``."""
    cases: list[EvalCase] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            cases.append(
                EvalCase(
                    message=record["message"],
                    lang=record.get("lang", "ru"),
                    expected_intent=record["expected_intent"],
                    expected_tools=tuple(record.get("expected_tools") or ()),
                    note=record.get("note", ""),
                )
            )
    return cases


def score(predictions: list[tuple[str, tuple[str, ...]]], cases: list[EvalCase]) -> dict[str, Any]:
    """Compare predictions against labels.

    ``false_tools`` counts tools that were selected but were not expected — the
    error that matters most, because a wrongly advertised tool is what makes a
    small model call something it should not.
    """
    total = len(cases)
    if total == 0:
        return {
            "cases": 0,
            "intent_accuracy": 0.0,
            "tool_cases": 0,
            "tool_exact": 0.0,
            "false_tools": 0,
            "confusions": {},
            "by_lang": {},
        }

    intent_hits = 0
    tool_cases = 0
    tool_hits = 0
    false_tools = 0
    confusions: dict[tuple[str, str], int] = {}
    by_lang: dict[str, dict[str, Any]] = {}

    for (intent, tools), case in zip(predictions, cases, strict=False):
        expected = set(case.expected_tools)
        if intent == case.expected_intent:
            intent_hits += 1
        else:
            key = (case.expected_intent, intent)
            confusions[key] = confusions.get(key, 0) + 1
        if case.expected_intent in TOOL_SCORING_INTENTS:
            tool_cases += 1
            if set(tools) == expected:
                tool_hits += 1
            false_tools += len(set(tools) - expected)

        bucket = by_lang.setdefault(case.lang, {"cases": 0, "intent_hits": 0, "tool_hits": 0, "tool_cases": 0})
        bucket["cases"] += 1
        bucket["intent_hits"] += int(intent == case.expected_intent)
        if case.expected_intent in TOOL_SCORING_INTENTS:
            bucket["tool_cases"] += 1
            bucket["tool_hits"] += int(set(tools) == expected)

    return {
        "cases": total,
        "intent_accuracy": round(intent_hits / total, 4),
        "tool_cases": tool_cases,
        "tool_exact": round(tool_hits / tool_cases, 4) if tool_cases else 0.0,
        "false_tools": false_tools,
        "confusions": confusions,
        "by_lang": {
            lang: {
                "cases": bucket["cases"],
                "intent_accuracy": round(bucket["intent_hits"] / bucket["cases"], 4),
                "tool_exact": round(bucket["tool_hits"] / bucket["tool_cases"], 4)
                if bucket["tool_cases"]
                else 0.0,
            }
            for lang, bucket in by_lang.items()
        },
    }


def run_evaluation(app: Any, cases: list[EvalCase], mode: str) -> list[tuple[str, tuple[str, ...]]]:
    """Run the dataset through the marker router (``router``) or the kernel (``kernel``).

    Both modes go through ``BaseModule.process_message`` so the comparison is
    between two decisions of the same function, not between two reimplementations.
    """
    base = app.modules["base"]
    previous = base.KERNEL_ENABLED
    base.KERNEL_ENABLED = mode == "kernel"
    predictions: list[tuple[str, tuple[str, ...]]] = []
    try:
        with app.app_context():
            for case in cases:
                result = base.process_message(case.message, DEFAULT_TIME, lang=case.lang, recent_context="")
                predictions.append((result.get("action", "none"), tuple(result.get("tools") or ())))
    finally:
        base.KERNEL_ENABLED = previous
    return predictions


def serialise(report: dict[str, Any]) -> dict[str, Any]:
    """JSON-safe copy: the confusion keys are tuples, which JSON cannot hold."""
    payload = dict(report)
    payload["confusions"] = {f"{expected}→{got}": count for (expected, got), count in report["confusions"].items()}
    return payload
