"""The kernel decision contract: parse one JSON object into a typed decision."""

from __future__ import annotations

import pytest

from app.kernel import KERNEL_INTENTS, SourceFlags, parse_kernel_decision


@pytest.mark.unit
def test_parses_bare_json():
    decision = parse_kernel_decision(
        '{"intent": "rag", "query": "IP addresses", "sources": {"memory": true}, '
        '"tools": ["rag_search"], "confidence": 0.9}'
    )
    assert decision is not None
    assert decision.intent == "rag"
    assert decision.query == "IP addresses"
    assert decision.sources.memory is True
    assert decision.tools == ("rag_search",)
    assert decision.confidence == 0.9


@pytest.mark.unit
def test_parses_fenced_json_with_prose_around_it():
    text = 'Sure.\n```json\n{"intent": "search", "query": "weather"}\n```\nDone.'
    decision = parse_kernel_decision(text)
    assert decision is not None
    assert decision.intent == "search"


@pytest.mark.unit
def test_unknown_intent_is_rejected():
    assert parse_kernel_decision('{"intent": "teleport", "query": "x"}') is None


@pytest.mark.unit
def test_missing_intent_is_rejected():
    assert parse_kernel_decision('{"query": "x"}') is None


@pytest.mark.unit
def test_truncated_json_is_rejected():
    assert parse_kernel_decision('{"intent": "rag", "query": "x') is None


@pytest.mark.unit
def test_empty_reply_is_rejected():
    assert parse_kernel_decision("") is None
    assert parse_kernel_decision(None) is None  # type: ignore[arg-type]


@pytest.mark.unit
def test_an_absent_tool_list_is_none_and_an_empty_one_is_a_tuple():
    # None means "the kernel had no opinion" (advertise everything);
    # () means "this message needs no tool" (advertise nothing).
    absent = parse_kernel_decision('{"intent": "none"}')
    empty = parse_kernel_decision('{"intent": "none", "tools": []}')
    assert absent is not None and absent.tools is None
    assert empty is not None and empty.tools == ()


@pytest.mark.unit
def test_a_non_list_tool_field_is_treated_as_absent():
    decision = parse_kernel_decision('{"intent": "none", "tools": "rag_search"}')
    assert decision is not None and decision.tools is None


@pytest.mark.unit
def test_tools_are_capped_and_deduplicated():
    decision = parse_kernel_decision('{"intent": "rag", "tools": ["a", "a", "b", "c", "d", "e", "f", "g"]}')
    assert decision is not None
    assert decision.tools == ("a", "b", "c", "d", "e", "f")


@pytest.mark.unit
def test_confidence_is_clamped():
    high = parse_kernel_decision('{"intent": "none", "confidence": 4.2}')
    missing = parse_kernel_decision('{"intent": "none"}')
    assert high is not None and high.confidence == 1.0
    assert missing is not None and missing.confidence == 0.0


@pytest.mark.unit
def test_only_the_memory_source_is_read():
    # documents / history / web duplicate what the intent already says, so the
    # kernel is not asked for them; memory is the one orthogonal flag.
    decision = parse_kernel_decision('{"intent": "none", "sources": {"memory": true, "documents": true}}')
    assert decision is not None
    assert decision.sources == SourceFlags(memory=True)


@pytest.mark.unit
def test_a_missing_or_non_boolean_memory_flag_is_none():
    # Tri-state on purpose: an absent field means "no opinion", and only an
    # explicit false may skip the recall.
    assert parse_kernel_decision('{"intent": "none"}').sources.memory is None
    assert parse_kernel_decision('{"intent": "none", "sources": {"memory": "false"}}').sources.memory is None


@pytest.mark.unit
def test_memory_is_skipped_only_on_an_explicit_negative():
    from app.kernel import memory_is_skipped

    assert memory_is_skipped(False) is True
    assert memory_is_skipped(True) is False
    assert memory_is_skipped(None) is False


@pytest.mark.unit
def test_every_intent_is_a_known_route_action():
    # The adapter maps an intent onto an action, so the two vocabularies must match.
    from app.kernel import INTENT_ACTIONS

    assert set(INTENT_ACTIONS) == set(KERNEL_INTENTS)
