# tests/test_rag_eval.py
"""RAG quality harness: dataset parsing, metrics and report shape."""

import json
from pathlib import Path

import pytest

from app.rag_eval import EvalItem, evaluate, evaluate_item, format_report, load_dataset, save_report

pytestmark = pytest.mark.unit


def _chunk(doc_id: str, text: str = "x" * 100) -> dict:
    return {"doc_id": doc_id, "filename": f"{doc_id}.pdf", "text": text}


def test_load_dataset_parses_jsonl(tmp_path: Path):
    path = tmp_path / "set.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"question": "Q1?", "relevant": ["d1"]}),
                "# a comment line",
                json.dumps({"question": "Q2?", "relevant": ["d2", "d3"], "reference": "text"}),
            ]
        ),
        encoding="utf-8",
    )

    items = load_dataset(path)

    assert [item.question for item in items] == ["Q1?", "Q2?"]
    assert items[1].relevant == ["d2", "d3"]
    assert items[1].reference == "text"


def test_load_dataset_rejects_a_line_without_a_question(tmp_path: Path):
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps({"relevant": ["d1"]}), encoding="utf-8")

    with pytest.raises(ValueError):
        load_dataset(path)


def test_perfect_hit_scores_one():
    result = evaluate_item(EvalItem("q", ["d1"]), lambda _q, _k: [_chunk("d1")], top_k=5)

    assert (result.hit, result.recall, result.reciprocal_rank, result.ndcg) == (1.0, 1.0, 1.0, 1.0)


def test_second_rank_halves_the_reciprocal_rank():
    result = evaluate_item(
        EvalItem("q", ["d1", "d2"]),
        lambda _q, _k: [_chunk("other"), _chunk("d1")],
        top_k=5,
    )

    assert result.hit == 1.0
    assert result.recall == 0.5
    assert result.reciprocal_rank == 0.5


def test_a_miss_scores_zero():
    result = evaluate_item(EvalItem("q", ["d1"]), lambda _q, _k: [_chunk("other")], top_k=5)

    assert (result.hit, result.recall, result.reciprocal_rank, result.ndcg) == (0.0, 0.0, 0.0, 0.0)


def test_filename_also_counts_as_relevant():
    chunk = {"doc_id": "unknown", "filename": "contract.pdf", "text": "t"}

    result = evaluate_item(EvalItem("q", ["contract.pdf"]), lambda _q, _k: [chunk], top_k=5)

    assert result.hit == 1.0


def test_report_carries_metrics_config_and_per_question_rows():
    items = [EvalItem("q1", ["d1"]), EvalItem("q2", ["d2"])]
    chunks = {"q1": [_chunk("d1")], "q2": [_chunk("other")]}

    report = evaluate(items, lambda q, _k: chunks[q], top_k=3, label="baseline", config={"RAG_TOP_K": 3})

    assert report["label"] == "baseline"
    assert report["questions"] == 2
    assert report["config"] == {"RAG_TOP_K": 3}
    assert report["metrics"]["hit_at_k"] == 0.5
    assert report["metrics"]["recall_at_k"] == 0.5
    assert report["metrics"]["latency_ms_p95"] >= 0
    assert len(report["results"]) == 2
    assert report["results"][0]["retrieved"] == ["d1"]


def test_empty_dataset_is_rejected():
    with pytest.raises(ValueError):
        evaluate([], lambda _q, _k: [], top_k=3)


def test_format_report_mentions_the_key_numbers():
    report = evaluate([EvalItem("q", ["d1"])], lambda _q, _k: [_chunk("d1")], top_k=4)

    text = format_report(report)

    assert "hit@4" in text and "recall@4" in text and "MRR" in text


def test_save_report_writes_json(tmp_path: Path):
    report = evaluate([EvalItem("q", ["d1"])], lambda _q, _k: [_chunk("d1")], top_k=3)

    target = save_report(report, tmp_path / "out" / "report.json")

    assert json.loads(target.read_text(encoding="utf-8"))["metrics"]["hit_at_k"] == 1.0


def test_ndcg_counts_a_document_once():
    """Several chunks of the same relevant file must not push nDCG above 1."""
    chunks = [_chunk("d1") for _ in range(5)]

    result = evaluate_item(EvalItem("q", ["d1"]), lambda _q, _k: chunks, top_k=10)

    assert result.hit == 1.0
    assert result.recall == 1.0
    assert result.ndcg == 1.0


def test_reference_makes_the_check_chunk_level():
    """The right document with the wrong passage is a miss when a reference exists."""
    right = {"doc_id": "d1", "filename": "d1.pdf", "text": "The penalty is 5% per day of delay."}
    wrong = {"doc_id": "d1", "filename": "d1.pdf", "text": "Section 1. General provisions."}
    item = EvalItem("q", ["d1"], reference="The penalty is 5% per day")

    hit = evaluate_item(item, lambda _q, _k: [right], top_k=5)
    miss = evaluate_item(item, lambda _q, _k: [wrong], top_k=5)

    assert hit.hit == 1.0
    assert miss.hit == 0.0
