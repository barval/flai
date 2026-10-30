"""A small model repeats the call it already made; the guard breaks the loop."""

from __future__ import annotations

import pytest

from app.tools import ToolCallLoopGuard


@pytest.mark.unit
def test_the_first_two_identical_calls_pass():
    guard = ToolCallLoopGuard()
    assert guard.check("web_search", {"query": "x"}) is True
    assert guard.check("web_search", {"query": "x"}) is True


@pytest.mark.unit
def test_the_third_identical_call_is_refused():
    guard = ToolCallLoopGuard()
    for _ in range(2):
        guard.check("web_search", {"query": "x"})
    assert guard.check("web_search", {"query": "x"}) is False


@pytest.mark.unit
def test_a_different_argument_set_is_a_different_call():
    guard = ToolCallLoopGuard()
    for _ in range(3):
        guard.check("web_search", {"query": "x"})
    assert guard.check("web_search", {"query": "y"}) is True


@pytest.mark.unit
def test_key_order_does_not_matter():
    guard = ToolCallLoopGuard()
    assert guard.check("t", {"a": 1, "b": 2}) is True
    assert guard.check("t", {"b": 2, "a": 1}) is True
    assert guard.check("t", {"a": 1, "b": 2}) is False


@pytest.mark.unit
def test_a_different_tool_with_the_same_arguments_is_a_different_call():
    guard = ToolCallLoopGuard()
    for _ in range(3):
        guard.check("web_search", {"query": "x"})
    assert guard.check("rag_search", {"query": "x"}) is True


@pytest.mark.unit
def test_the_threshold_is_configurable():
    guard = ToolCallLoopGuard(repeats=2)
    assert guard.check("t", {}) is True
    assert guard.check("t", {}) is False


@pytest.mark.unit
def test_optional_field_drift_does_not_hide_a_repeat():
    # A model that alternates an optional field would otherwise never trip the
    # limit: the guard compares only what the schema requires.
    schema = {"properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}}, "required": ["query"]}
    guard = ToolCallLoopGuard()
    assert guard.check("rag_search", {"query": "x"}, schema) is True
    assert guard.check("rag_search", {"query": "x", "top_k": 5}, schema) is True
    assert guard.check("rag_search", {"query": "x", "top_k": 10}, schema) is False


@pytest.mark.unit
def test_without_a_schema_every_field_is_compared():
    guard = ToolCallLoopGuard()
    assert guard.check("t", {"a": 1, "b": 2}) is True
    assert guard.check("t", {"a": 1, "b": 2}) is True
    assert guard.check("t", {"a": 1, "b": 2}) is False
    # without a schema a different field set is a different call, which is the
    # drift the schema-aware form above closes
    assert guard.check("t", {"a": 1}) is True
