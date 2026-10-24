# app/rag_eval.py
"""Offline quality measurement for document search (RAG).

The harness answers one question: did a change make retrieval better? It runs a
fixed question set through the real search path and reports hit@k, recall@k,
MRR, nDCG@k and the latency each query costs. A JSON report keeps the settings
snapshot, so two runs are comparable.

A dataset is JSONL, one question per line::

    {"question": "What is the penalty for late payment?", "relevant": ["<doc_id>", "contract.pdf"]}

``relevant`` matches the retrieved chunk's ``doc_id`` or ``filename``.
"""

from __future__ import annotations

import json
import math
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SearchFn = Callable[[str, int], list[dict]]


@dataclass
class EvalItem:
    question: str
    relevant: list[str] = field(default_factory=list)
    reference: str | None = None


@dataclass
class EvalResult:
    question: str
    hit: float
    recall: float
    reciprocal_rank: float
    ndcg: float
    latency_ms: float
    retrieved: list[str]
    context_chars: int


def load_dataset(path: str | Path) -> list[EvalItem]:
    """Read a JSONL dataset; malformed lines are reported, not fatal."""
    items: list[EvalItem] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"{path}:{number}: {exc}") from exc
        question = str(row.get("question") or "").strip()
        relevant = [str(value) for value in (row.get("relevant") or []) if value]
        if not question or not relevant:
            raise ValueError(f"{path}:{number}: 'question' and 'relevant' are required")
        items.append(EvalItem(question=question, relevant=relevant, reference=row.get("reference")))
    return items


def _normalize(text: str) -> str:
    return " ".join(str(text or "").split()).lower()


def _is_relevant(chunk: dict, relevant: set[str], reference: str | None = None) -> bool:
    """Chunk-level when the dataset carries the source text, document-level otherwise.

    A document-level ground truth saturates on a small corpus (any chunk of the
    right file counts), so a question generated from a passage also stores that
    passage: the retrieved chunk must then actually contain it.
    """
    if reference:
        # A different chunk of the same document is NOT a hit at chunk level.
        needle = _normalize(reference)[:120]
        return bool(needle) and needle in _normalize(chunk.get("text") or "")
    return str(chunk.get("doc_id") or "") in relevant or str(chunk.get("filename") or "") in relevant


def _dcg(gains: list[float]) -> float:
    return sum(gain / (1 if index == 0 else math.log2(index + 1)) for index, gain in enumerate(gains))


def evaluate_item(item: EvalItem, search_fn: SearchFn, top_k: int) -> EvalResult:
    started = time.perf_counter()
    chunks = search_fn(item.question, top_k) or []
    latency_ms = (time.perf_counter() - started) * 1000.0

    relevant = set(item.relevant)
    retrieved = [str(chunk.get("doc_id") or chunk.get("filename") or "") for chunk in chunks]
    matches = [_is_relevant(chunk, relevant, item.reference) for chunk in chunks]

    hit = 1.0 if any(matches) else 0.0
    found = {retrieved[index] for index, matched in enumerate(matches) if matched}
    recall = len(found) / len(relevant) if relevant else 0.0
    first = next((index for index, matched in enumerate(matches) if matched), None)
    reciprocal_rank = 0.0 if first is None else 1.0 / (first + 1)

    # Relevance is judged per DOCUMENT: several chunks of the same file are one
    # hit, otherwise a long document could push nDCG above 1.
    gains: list[float] = []
    seen_docs: set[str] = set()
    for index in range(len(chunks)):
        doc = retrieved[index]
        if doc in seen_docs:
            continue
        seen_docs.add(doc)
        gains.append(1.0 if matches[index] else 0.0)
    dcg = _dcg(gains)
    ideal = _dcg([1.0] * min(len(relevant), top_k))
    ndcg = dcg / ideal if ideal else 0.0

    return EvalResult(
        question=item.question,
        hit=hit,
        recall=recall,
        reciprocal_rank=reciprocal_rank,
        ndcg=ndcg,
        latency_ms=latency_ms,
        retrieved=retrieved,
        context_chars=sum(len(str(chunk.get("text") or "")) for chunk in chunks),
    )


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def evaluate(
    items: list[EvalItem],
    search_fn: SearchFn,
    top_k: int = 10,
    label: str = "",
    config: dict[str, Any] | None = None,
) -> dict:
    """Run the dataset and return a report dict (also written by the CLI)."""
    results = [evaluate_item(item, search_fn, top_k) for item in items]
    if not results:
        raise ValueError("The dataset is empty")

    latencies = [result.latency_ms for result in results]
    return {
        "label": label,
        "top_k": top_k,
        "questions": len(results),
        "config": config or {},
        "metrics": {
            "hit_at_k": statistics.fmean(result.hit for result in results),
            "recall_at_k": statistics.fmean(result.recall for result in results),
            "mrr": statistics.fmean(result.reciprocal_rank for result in results),
            "ndcg_at_k": statistics.fmean(result.ndcg for result in results),
            "latency_ms_p50": _percentile(latencies, 0.5),
            "latency_ms_p95": _percentile(latencies, 0.95),
            "context_chars_avg": statistics.fmean(result.context_chars for result in results),
        },
        "results": [
            {
                "question": result.question,
                "hit": result.hit,
                "recall": round(result.recall, 4),
                "reciprocal_rank": round(result.reciprocal_rank, 4),
                "ndcg": round(result.ndcg, 4),
                "latency_ms": round(result.latency_ms, 1),
                "context_chars": result.context_chars,
                "retrieved": result.retrieved,
            }
            for result in results
        ],
    }


def format_report(report: dict) -> str:
    """Human-readable summary for the CLI."""
    metrics = report["metrics"]
    lines = [
        f"label:     {report.get('label') or '-'}",
        f"questions: {report['questions']}   top_k: {report['top_k']}",
        f"hit@{report['top_k']}:    {metrics['hit_at_k']:.3f}",
        f"recall@{report['top_k']}: {metrics['recall_at_k']:.3f}",
        f"MRR:       {metrics['mrr']:.3f}",
        f"nDCG@{report['top_k']}:   {metrics['ndcg_at_k']:.3f}",
        f"latency:   p50 {metrics['latency_ms_p50']:.0f} ms, p95 {metrics['latency_ms_p95']:.0f} ms",
        f"context:   {metrics['context_chars_avg']:.0f} chars per query",
    ]
    return "\n".join(lines)


def save_report(report: dict, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
