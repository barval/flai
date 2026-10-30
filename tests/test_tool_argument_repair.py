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
