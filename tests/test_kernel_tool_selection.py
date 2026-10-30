"""Only the tools the kernel selected are advertised — but silence must not disable tools."""

from __future__ import annotations

import pytest

from app.tools import get_tool_definitions, select_tool_definitions


@pytest.mark.unit
def test_none_returns_everything():
    assert select_tool_definitions(None) == get_tool_definitions()


@pytest.mark.unit
def test_empty_tuple_advertises_nothing():
    # () is a decision: this message needs no tool, so the schema block is not sent.
    assert select_tool_definitions(()) == []


@pytest.mark.unit
def test_subset_is_returned_in_catalog_order():
    selected = select_tool_definitions(("calculator",))
    assert [item["function"]["name"] for item in selected] == ["calculator"]


@pytest.mark.unit
def test_several_names_keep_catalog_order_not_argument_order():
    selected = select_tool_definitions(("rag_search", "calculator"))
    assert [item["function"]["name"] for item in selected] == ["calculator", "rag_search"]


@pytest.mark.unit
def test_unknown_names_do_not_disable_everything():
    assert select_tool_definitions(("does_not_exist",)) == get_tool_definitions()


@pytest.mark.unit
def test_mixing_a_known_and_an_unknown_name_keeps_the_known_one():
    selected = select_tool_definitions(("does_not_exist", "calculator"))
    assert [item["function"]["name"] for item in selected] == ["calculator"]
