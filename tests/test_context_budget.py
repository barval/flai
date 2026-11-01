"""The window is split by measured need, not by fixed percentages."""

from __future__ import annotations

import pytest

from app.context_budget import (
    MAX_ANSWER_TOKENS,
    MIN_ANSWER_TOKENS,
    MIN_HISTORY_TOKENS,
    answer_reserve,
    cap_share,
    context_budget,
)


@pytest.mark.unit
def test_the_reply_reserve_scales_with_the_window_and_is_bounded():
    assert answer_reserve(4096) == MIN_ANSWER_TOKENS
    assert answer_reserve(8192) == int(8192 * 0.10)
    assert answer_reserve(32768) == int(32768 * 0.10)
    assert answer_reserve(200000) == MAX_ANSWER_TOKENS


@pytest.mark.unit
def test_the_fixed_prompt_is_subtracted_before_anything_is_shared():
    budget = context_budget(16384, fixed_tokens=2000)
    assert budget.fixed == 2000
    assert budget.available == 16384 - 2000 - budget.answer


@pytest.mark.unit
def test_a_small_corpus_gets_what_it_needs_and_no_more():
    # A CPU host with 8 192 tokens and one short session: the old 30 % share gave
    # documents 2 458 tokens whatever the corpus was; now the corpus asks for its
    # size and the rest of the window stays unused.
    budget = context_budget(8192, fixed_tokens=1000)
    documents, history = budget.split(history_need=400, document_need=3000)
    assert documents == 3000
    assert history == 400
    assert documents + history < budget.available


@pytest.mark.unit
def test_a_long_conversation_still_leaves_evidence_half_the_room():
    budget = context_budget(16384, fixed_tokens=1000)
    documents, history = budget.split(history_need=100_000, document_need=100_000)
    assert documents == int(budget.available * 0.5)
    assert history == budget.available - documents


@pytest.mark.unit
def test_history_is_never_granted_more_than_it_holds():
    budget = context_budget(24576, fixed_tokens=1000)
    documents, history = budget.split(history_need=300, document_need=500)
    assert history == 300
    assert documents == 500


@pytest.mark.unit
def test_a_long_conversation_keeps_a_floor_even_with_a_huge_corpus():
    budget = context_budget(16384, fixed_tokens=1000)
    documents, history = budget.split(history_need=5000, document_need=1_000_000)
    assert history >= MIN_HISTORY_TOKENS
    assert documents <= budget.available - MIN_HISTORY_TOKENS


@pytest.mark.unit
def test_nothing_is_granted_when_the_window_is_already_spent():
    budget = context_budget(2048, fixed_tokens=5000)
    assert budget.available == 0
    assert budget.split(history_need=1000, document_need=1000) == (0, 0)


@pytest.mark.unit
def test_a_manual_cap_only_narrows():
    budget = context_budget(16384, fixed_tokens=1000)
    documents, _history = budget.split(history_need=0, document_need=budget.available)
    assert cap_share(documents, budget.available, 0) == documents
    assert cap_share(documents, budget.available, 10) == int(budget.available * 0.10)
    assert cap_share(documents, budget.available, 200) == documents
