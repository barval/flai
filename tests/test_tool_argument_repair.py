"""Tool arguments from a small model arrive in shapes the schema does not expect."""

from __future__ import annotations

import pytest

from app.tools import parse_tool_arguments, repair_tool_arguments


@pytest.mark.unit
def test_a_stringified_number_becomes_a_number():
    schema = {"properties": {"limit": {"type": "integer"}}}
    assert repair_tool_arguments({"limit": "20"}, schema) == {"limit": 20}


@pytest.mark.unit
def test_a_stringified_boolean_becomes_a_boolean():
    schema = {"properties": {"deep": {"type": "boolean"}}}
    assert repair_tool_arguments({"deep": "false"}, schema) == {"deep": False}


@pytest.mark.unit
def test_a_stringified_object_argument_is_parsed():
    schema = {"properties": {"options": {"type": "object"}}}
    assert repair_tool_arguments({"options": '{"a": 1}'}, schema) == {"options": {"a": 1}}


@pytest.mark.unit
def test_null_placeholders_are_dropped_for_optional_fields():
    schema = {"properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]}
    assert repair_tool_arguments({"query": "x", "limit": None}, schema) == {"query": "x"}


@pytest.mark.unit
def test_a_required_field_is_kept_even_when_null():
    schema = {"properties": {"query": {"type": "string"}}, "required": ["query"]}
    assert repair_tool_arguments({"query": None}, schema) == {"query": None}


@pytest.mark.unit
def test_unrepairable_values_are_left_alone():
    schema = {"properties": {"query": {"type": "string"}}}
    assert repair_tool_arguments({"query": "not json {"}, schema) == {"query": "not json {"}


@pytest.mark.unit
def test_no_schema_is_a_no_op():
    assert repair_tool_arguments({"a": "1"}, None) == {"a": "1"}


@pytest.mark.unit
def test_a_well_formed_argument_string_is_parsed():
    arguments, ok = parse_tool_arguments('{"query": "x"}', None)
    assert ok is True
    assert arguments == {"query": "x"}


@pytest.mark.unit
def test_a_broken_argument_string_is_reported_not_silently_emptied():
    # Running a search with an empty query teaches the model nothing.
    arguments, ok = parse_tool_arguments("{query: x}", None)
    assert ok is False
    assert arguments == {}


@pytest.mark.unit
def test_an_empty_argument_string_is_a_valid_empty_call():
    assert parse_tool_arguments("", None) == ({}, True)


@pytest.mark.unit
def test_raw_newlines_inside_a_string_are_accepted():
    """A long SVG arrives with literal newlines; strict JSON refuses them.

    Measured: a 19 KB drawing was rejected here, and the malformed call then
    poisoned the next request because llama-server re-parses the history.
    """
    raw = '{"name": "scene.svg", "format": "svg", "content": "<svg>\\n<text>x</text></svg>"}'
    arguments, ok = parse_tool_arguments(raw, None)

    assert ok is True
    assert "<svg>" in arguments["content"]


@pytest.mark.unit
def test_an_unparsable_call_enters_the_transcript_with_empty_arguments():
    from app.tools import transcript_safe_calls

    calls = [
        {"id": "a", "function": {"name": "write_document", "arguments": "{not json"}},
        {"id": "b", "function": {"name": "web_search", "arguments": '{"query": "x"}'}},
    ]
    safe = transcript_safe_calls(calls, {})

    assert safe[0]["function"]["arguments"] == "{}"
    assert safe[1] is calls[1]
    # The original object the caller still executes is untouched.
    assert calls[0]["function"]["arguments"] == "{not json"
