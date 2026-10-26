# app/memory_eval.py
"""Measure what long-term memory actually retrieves.

RAG has had `flask rag-eval` with hit@k/MRR/nDCG since v12.5; memory had no
measurement at all, so "improved memory" was an opinion. Recall is scored the
same way RAG is: a question carries the substrings that make an injected fact
relevant, and the harness reports hit@k, MRR and the token cost of the injected
set. Pure functions — the recall call is injected — so it runs in tests without
a model, a store or a GPU.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from typing import Any

RecallFn = Callable[[str, int], tuple[list[str], float]]


def _first_relevant(facts: list[str], expected: list[str]) -> int | None:
    """1-based rank of the first fact carrying any expected marker."""
    haystack = [fact.casefold() for fact in facts]
    needles = [item.casefold() for item in expected if item]
    for index, text in enumerate(haystack, start=1):
        if any(needle in text for needle in needles):
            return index
    return None


def evaluate_memory(records: list[dict[str, Any]], recall_fn: RecallFn, k: int = 5) -> dict[str, Any]:
    """Score a question set against a recall callable.

    Returns ``hit`` (share of questions with a relevant fact in the top k),
    ``mrr`` (mean reciprocal rank of the first relevant fact), ``avg_facts``
    (how many facts get injected per question — the context cost) and latency.
    """
    questions = len(records)
    if not questions:
        return {"questions": 0, "hit": 0.0, "mrr": 0.0, "avg_facts": 0.0, "p50_ms": 0.0}

    hits = 0
    reciprocal = 0.0
    injected = 0
    latencies: list[float] = []

    for record in records:
        facts, elapsed = recall_fn(str(record["question"]), k)
        injected += len(facts)
        latencies.append(elapsed)
        rank = _first_relevant(facts, list(record.get("expected") or []))
        if rank is not None:
            hits += 1
            reciprocal += 1.0 / rank

    return {
        "questions": questions,
        "hit": round(hits / questions, 3),
        "mrr": round(reciprocal / questions, 3),
        "avg_facts": round(injected / questions, 2),
        "p50_ms": round(statistics.median(latencies) * 1000, 1) if latencies else 0.0,
    }
