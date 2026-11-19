"""Only the tools the kernel selected are advertised — but silence must not disable tools."""

from __future__ import annotations

import pytest

from app.tools import (
    SCHEDULER_TOOLS,
    SKILL_LOADER_TOOL,
    WRITE_DOCUMENT_TOOL,
    get_tool_definitions,
    select_tool_definitions,
)

# Kept on purpose when the kernel narrows the list: the skill loader, the only way to
# hand the user a file, and the scheduling tools. They are not part of "what was chosen".
ALWAYS_KEPT = {SKILL_LOADER_TOOL, WRITE_DOCUMENT_TOOL, *SCHEDULER_TOOLS}


def _names(definitions):
    return [item["function"]["name"] for item in definitions if item["function"]["name"] not in ALWAYS_KEPT]


@pytest.mark.unit
def test_none_returns_everything():
    assert select_tool_definitions(None) == get_tool_definitions()


@pytest.mark.unit
def test_empty_tuple_advertises_everything():
    # Measured on Qwen3VL-8B: asked for the tools a message needs, it answers
    # "[]" for "what time is it?" even when the prompt names get_current_time
    # explicitly. An empty list is therefore the one answer that is confidently
    # wrong, and it is also the one that removes every capability at once, so it
    # is read as "no opinion" while a non-empty selection is obeyed.
    assert select_tool_definitions(()) == get_tool_definitions()


@pytest.mark.unit
def test_subset_is_returned_in_catalog_order():
    selected = select_tool_definitions(("calculator",))
    assert _names(selected) == ["calculator"]
    kept = {item["function"]["name"] for item in selected}
    assert SKILL_LOADER_TOOL in kept
    assert WRITE_DOCUMENT_TOOL in kept


@pytest.mark.unit
def test_several_names_keep_catalog_order_not_argument_order():
    selected = select_tool_definitions(("rag_search", "calculator"))
    assert _names(selected) == ["calculator", "rag_search"]


@pytest.mark.unit
def test_unknown_names_do_not_disable_everything():
    assert select_tool_definitions(("does_not_exist",)) == get_tool_definitions()


@pytest.mark.unit
def test_mixing_a_known_and_an_unknown_name_keeps_the_known_one():
    selected = select_tool_definitions(("does_not_exist", "calculator"))
    assert _names(selected) == ["calculator"]
