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
import re
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
    references: list[str] = field(default_factory=list)
    answer: str | None = None

    @property
    def passages(self) -> list[str]:
        """Ground-truth passages; an answer may span several chunks."""
        if self.references:
            return [str(value) for value in self.references if value]
        return [self.reference] if self.reference else []


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
        references = [str(value) for value in (row.get("references") or []) if value]
        items.append(
            EvalItem(
                question=question,
                relevant=relevant,
                reference=row.get("reference"),
                references=references,
                answer=str(row["answer"]) if row.get("answer") is not None else None,
            )
        )
    return items


def _normalize(text: str) -> str:
    return " ".join(str(text or "").split()).lower()


def _passage_match(passage: str, text: str, threshold: float = 0.75) -> bool:
    """True when the chunk carries the passage (word overlap, not exact bytes)."""
    needle = _normalize(passage)[:200]
    if not needle:
        return False
    if needle in text:
        return True
    wanted = set(needle.split())
    if not wanted:
        return False
    return len(wanted & set(text.split())) / len(wanted) >= threshold


def _is_relevant(chunk: dict, relevant: set[str], passages: list[str] | None = None) -> bool:
    """Chunk-level when the dataset carries the source text, document-level otherwise.

    A document-level ground truth saturates on a small corpus (any chunk of the
    right file counts), so a question generated from a passage also stores that
    passage: the retrieved chunk must then actually contain it.
    """
    if passages:
        # A different chunk of the same document is NOT a hit at chunk level;
        # any one of the ground-truth passages counts. Matching is tolerant to
        # chunk boundaries: a passage split by a new chunking strategy still
        # counts when most of its words are in the chunk.
        text = _normalize(chunk.get("text") or "")
        return any(_passage_match(passage, text) for passage in passages if passage)
    return str(chunk.get("doc_id") or "") in relevant or str(chunk.get("filename") or "") in relevant


def _dcg(gains: list[float]) -> float:
    """Standard DCG: position 1 weighs 1, position 2 weighs 1/log2(3), and so on."""
    return sum(gain / math.log2(position + 1) for position, gain in enumerate(gains, start=1))


def evaluate_item(item: EvalItem, search_fn: SearchFn, top_k: int) -> EvalResult:
    started = time.perf_counter()
    chunks = search_fn(item.question, top_k) or []
    latency_ms = (time.perf_counter() - started) * 1000.0

    relevant = set(item.relevant)
    retrieved = [str(chunk.get("doc_id") or chunk.get("filename") or "") for chunk in chunks]
    matches = [_is_relevant(chunk, relevant, item.passages) for chunk in chunks]

    hit = 1.0 if any(matches) else 0.0
    found = {retrieved[index] for index, matched in enumerate(matches) if matched}
    recall = len(found) / len(relevant) if relevant else 0.0
    first = next((index for index, matched in enumerate(matches) if matched), None)
    reciprocal_rank = 0.0 if first is None else 1.0 / (first + 1)

    # Document-level ground truth counts a file once (several chunks of the same
    # document must not push nDCG above 1); chunk-level ground truth ranks the
    # matching chunks themselves.
    if item.passages:
        gains = [1.0 if matched else 0.0 for matched in matches]
    else:
        gains = []
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


def _numbers(text: str) -> list[str]:
    return re.findall(r"\d+", str(text or ""))


def answer_matches(expected: str, actual: str) -> bool:
    """Numeric answers compare by number, free-form ones by normalized text.

    Counting questions are the reason this exists: a model that retrieves the
    right chunks can still answer "3" where the truth is "2", and hit@k would
    never notice. An expected zero also accepts an explicit "no data" wording.
    """
    expected = str(expected or "").strip()
    actual = str(actual or "").strip()
    if not expected:
        return False
    expected_numbers = _numbers(expected)
    actual_numbers = _numbers(actual)
    if expected_numbers:
        if expected_numbers[0] in actual_numbers:
            return True
        if expected_numbers[0] == "0":
            return not actual_numbers and bool(
                re.search(r"нет|не работал|не найдено|no data|none", _normalize(actual))
            )
        return False
    return _normalize(expected) in _normalize(actual)


def evaluate_answers(
    items: list[EvalItem],
    answer_fn: Callable[[str, str], str],
    search_fn: SearchFn,
    top_k: int = 10,
    label: str = "",
    config: dict[str, Any] | None = None,
) -> dict:
    """End-to-end check: retrieve, answer, compare with the expected answer.

    ``answer_fn(question, context)`` is the model call; the context is built
    from the retrieved chunks exactly like a chat request.
    """
    judged = [item for item in items if item.answer]
    if not judged:
        raise ValueError("No dataset item carries an 'answer' field")

    rows = []
    for item in judged:
        chunks = search_fn(item.question, top_k) or []
        context = "\n\n".join(
            f"[{chunk.get('filename') or '?'}] {chunk.get('text') or ''}" for chunk in chunks
        )
        actual = answer_fn(item.question, context)
        rows.append(
            {
                "question": item.question,
                "expected": item.answer,
                "actual": actual,
                "match": answer_matches(str(item.answer), actual),
                "chunks": len(chunks),
            }
        )

    return {
        "label": label,
        "mode": "answers",
        "top_k": top_k,
        "questions": len(rows),
        "config": config or {},
        "metrics": {
            "answer_accuracy": sum(1 for row in rows if row["match"]) / len(rows),
        },
        "results": rows,
    }


def format_report(report: dict) -> str:
    """Human-readable summary for the CLI."""
    metrics = report["metrics"]
    if report.get("mode") == "answers":
        lines = [
            f"label:     {report.get('label') or '-'}",
            f"questions: {report['questions']}   top_k: {report['top_k']}",
            f"answer accuracy: {metrics['answer_accuracy']:.3f}",
        ]
        misses = [row for row in report["results"] if not row["match"]]
        for row in misses:
            lines.append(f"  MISS  {row['question']}  expected={row['expected']!r}  got={row['actual'][:80]!r}")
        return "\n".join(lines)
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
