"""Metric maths for the kernel evaluation harness (no model calls)."""

from __future__ import annotations

import pytest

from app.kernel_eval import EvalCase, load_dataset, score


@pytest.mark.unit
def test_identical_predictions_score_one():
    cases = [EvalCase("q", "ru", "none", (), ""), EvalCase("q2", "en", "none", ("web_search",), "")]
    report = score([("none", ()), ("none", ("web_search",))], cases)
    assert report["intent_accuracy"] == 1.0
    assert report["tool_exact"] == 1.0
    assert report["false_tools"] == 0


@pytest.mark.unit
def test_a_wrong_tool_is_counted_even_when_the_intent_matches():
    cases = [EvalCase("q", "ru", "none", (), "")]
    report = score([("none", ("image_generate",))], cases)
    assert report["intent_accuracy"] == 1.0
    assert report["false_tools"] == 1


@pytest.mark.unit
def test_confusions_are_recorded_as_ordered_pairs():
    cases = [EvalCase("q", "ru", "rag", (), "")]
    report = score([("history", ())], cases)
    assert report["confusions"] == {("rag", "history"): 1}


@pytest.mark.unit
def test_the_report_splits_by_language():
    cases = [EvalCase("a", "ru", "rag", (), ""), EvalCase("b", "en", "search", (), "")]
    report = score([("rag", ()), ("rag", ())], cases)
    assert report["by_lang"]["ru"]["intent_accuracy"] == 1.0
    assert report["by_lang"]["en"]["intent_accuracy"] == 0.0


@pytest.mark.unit
def test_tools_are_only_scored_for_the_chat_branch():
    # A rag request is handed to the rag subsystem, which has its own tools, so a
    # selection there is not an error.
    cases = [EvalCase("q", "ru", "rag", (), "")]
    report = score([("rag", ("rag_search",))], cases)
    assert report["tool_cases"] == 0
    assert report["false_tools"] == 0


@pytest.mark.unit
def test_an_empty_dataset_does_not_divide_by_zero():
    report = score([], [])
    assert report["intent_accuracy"] == 0.0
    assert report["cases"] == 0


@pytest.mark.unit
def test_the_seed_dataset_loads_and_is_well_formed(tmp_path):
    from pathlib import Path

    cases = load_dataset(Path("tests/data/kernel_eval.jsonl"))
    assert len(cases) >= 24
    assert {case.lang for case in cases} == {"ru", "en"}
    assert all(case.message for case in cases)
