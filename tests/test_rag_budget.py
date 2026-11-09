"""Retrieval breadth follows the window the deployment auto-fitted, not a constant."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.rag_budget import WHOLE_DOCUMENT_MAX_TOKENS, retrieval_budget, tokens_per_fragment
from modules.rag import apply_coverage


def _budget(app, window: int, corpus_documents: int | None = None, lang: str = "ru", **config):
    app.config.update({"RAG_TOP_K": 0, "RAG_CONTEXT_PERCENT": 0, "TEMPLATE_OVERHEAD_TOKENS": 800, **config})
    with patch("app.model_config.get_model_config", return_value={"context_length": window}):
        return retrieval_budget(app, lang, corpus_documents=corpus_documents)


@pytest.mark.unit
def test_a_bigger_window_holds_more_fragments(app):
    small, medium, large = _budget(app, 8192), _budget(app, 16384), _budget(app, 24576)
    assert small.fragments < medium.fragments < large.fragments
    # The reference 16 GB card held 20 fragments (10 files) before; the window
    # alone now gives it more than that.
    assert large.fragments > 20


@pytest.mark.unit
def test_a_cpu_window_covers_a_small_corpus_entirely(app):
    # 8 192 tokens used to grant documents 30 % of the window whatever they were.
    budget = _budget(app, 8192, corpus_documents=4)
    assert budget.coverage_files == 4
    assert budget.fragments >= 4


@pytest.mark.unit
def test_a_cpu_window_gives_a_large_corpus_more_than_the_old_fixed_share(app):
    # The old 30 % of an 8 192-token window held ~9 fragments; the corpus is the
    # task, so it gets the room the reply and the prompt do not need.
    budget = _budget(app, 8192, corpus_documents=100)
    assert budget.fragments > 9
    assert budget.tokens > int(8192 * 0.30)


@pytest.mark.unit
def test_a_small_corpus_is_covered_entirely(app):
    # 26 small documents with room for 28 fragments: every one is represented,
    # the rest of the room deepens the relevance half.
    budget = _budget(app, 24576, corpus_documents=26)
    assert budget.coverage_files == 26
    assert budget.top_k >= 1
    assert budget.top_k + budget.coverage_files == budget.fragments


@pytest.mark.unit
def test_a_small_corpus_can_hold_one_document_whole(app):
    # The noise-law case: three visible documents, one relevance chunk, and the
    # answer split between two chunks of the same document. The budget must have
    # room for the whole small document, otherwise depth stays at one chunk.
    budget = _budget(app, 24576, corpus_documents=3)
    assert budget.tokens >= WHOLE_DOCUMENT_MAX_TOKENS


@pytest.mark.unit
def test_a_large_corpus_splits_the_room_by_policy(app):
    budget = _budget(app, 24576, corpus_documents=500)
    assert budget.coverage_files > 0
    assert budget.top_k > budget.coverage_files


@pytest.mark.unit
def test_an_explicit_top_k_still_narrows(app):
    budget = _budget(app, 24576, RAG_TOP_K=5, corpus_documents=26)
    assert budget.top_k <= 5


@pytest.mark.unit
def test_a_manual_share_cap_still_applies(app):
    automatic = _budget(app, 24576, corpus_documents=500)
    capped = _budget(app, 24576, RAG_CONTEXT_PERCENT=10, corpus_documents=500)
    assert capped.fragments < automatic.fragments


@pytest.mark.unit
def test_a_fragment_costs_more_in_russian(app):
    assert tokens_per_fragment(app, "ru") >= tokens_per_fragment(app, "en")


@pytest.mark.unit
def test_the_history_need_shrinks_the_document_room(app):
    quiet = _budget(app, 24576)
    chatty = retrieval_budget.__wrapped__ if False else None  # keep linters honest
    app.config.update({"RAG_TOP_K": 0, "RAG_CONTEXT_PERCENT": 0, "TEMPLATE_OVERHEAD_TOKENS": 800})
    with patch("app.model_config.get_model_config", return_value={"context_length": 24576}):
        roomy = retrieval_budget(app, "ru", history_need=0, corpus_documents=None)
        squeezed = retrieval_budget(app, "ru", history_need=6000, corpus_documents=None)
    assert quiet.fragments > 0 and chatty is None
    assert squeezed.fragments < roomy.fragments


def _pair(doc: str, index: int = 0, score: float = 0.5) -> tuple[dict, float]:
    return ({"doc_id": doc, "chunk_index": index, "text": f"text {doc} {index}"}, score)


@pytest.mark.unit
def test_coverage_adds_one_chunk_per_unrepresented_document():
    relevance = [_pair("a"), _pair("a", 1)]
    extras = [_pair("b"), _pair("c"), _pair("d")]
    kept, added = apply_coverage(relevance, extras, coverage_max_files=2)
    # "a" is already represented in the relevance half; b and c fill the allowance.
    assert added == 2
    assert [chunk["doc_id"] for chunk, _score in kept] == ["a", "a", "b", "c"]
    assert [chunk.get("coverage") for chunk, _score in kept] == [None, None, True, True]


@pytest.mark.unit
def test_coverage_marks_only_what_it_added():
    kept, added = apply_coverage([_pair("a")], [_pair("b")], coverage_max_files=5)
    assert added == 1
    assert kept[0][0].get("coverage") is None
    assert kept[1][0].get("coverage") is True


@pytest.mark.unit
def test_sixteen_missing_documents_no_longer_skip_coverage():
    # The live failure: 17 documents were missing and the old all-or-nothing cap
    # of 6 threw the whole coverage pass away.
    relevance = [_pair("top")]
    extras = [_pair(f"doc{index}") for index in range(16)]
    kept, added = apply_coverage(relevance, extras, coverage_max_files=15)
    assert added == 15
    assert len(kept) == 16


@pytest.mark.unit
def test_the_allowance_is_a_ceiling_not_a_quota():
    kept, added = apply_coverage([_pair("a")], [_pair("a", 1), _pair("a", 2)], coverage_max_files=5)
    assert added == 0
    assert len(kept) == 1


@pytest.mark.unit
def test_the_context_share_has_one_default():
    """The trimming path kept a second, stale default (50) that never applied
    because config always sets the key — it only hid a disagreement."""
    import inspect

    import app.config as config_module
    import modules.rag as rag_module

    source = inspect.getsource(rag_module.RagModule.generate_answer)
    assert "RAG_CONTEXT_PERCENT\", 50" not in source
    assert "RAG_CONTEXT_PERCENT" in inspect.getsource(config_module.load_config)
