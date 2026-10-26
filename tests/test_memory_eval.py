# tests/test_memory_eval.py
"""Memory measurement: the CPU gate for auto-extraction and the eval metrics."""

import pytest

from app.memory_eval import evaluate_memory
from app.memory_facts import auto_extract_enabled

pytestmark = pytest.mark.unit


def test_auto_extraction_stays_off_on_a_cpu_host():
    """One extraction is ~100 tokens at CPU speed — half a minute of the model."""
    assert auto_extract_enabled("auto", "cpu") is False


def test_auto_extraction_runs_on_a_gpu_host():
    assert auto_extract_enabled("auto", "nvidia") is True


def test_auto_extraction_can_be_forced_on_and_off():
    assert auto_extract_enabled("on", "cpu") is True
    assert auto_extract_enabled("off", "nvidia") is False


def test_eval_counts_hit_at_k_and_mrr():
    records = [
        {"question": "где я живу?", "expected": ["твер"]},
        {"question": "что я не ем?", "expected": ["гриб"]},
    ]

    def recall(question, k):
        if "живу" in question:
            return ["Работает аналитиком", "Живёт в Твери"], 0.004
        return ["Любит кофе", "Работает в Сбере"], 0.004

    result = evaluate_memory(records, recall, k=5)

    assert result["questions"] == 2
    assert result["hit"] == 0.5
    assert result["mrr"] == pytest.approx(0.25)  # the hit is at rank 2
    assert result["avg_facts"] == 2.0


def test_eval_reports_zero_for_an_empty_dataset():
    result = evaluate_memory([], lambda q, k: ([], 0.0), k=5)

    assert result["questions"] == 0
    assert result["hit"] == 0.0


def test_eval_matches_case_insensitively():
    records = [{"question": "where do I live?", "expected": ["tver"]}]

    result = evaluate_memory(records, lambda q, k: (["User lives in Tver"], 0.0), k=3)

    assert result["hit"] == 1.0
