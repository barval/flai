"""A tool result must never be inserted into the prompt whole."""

from __future__ import annotations

import pytest

from app.tools import TOOL_OUTPUT_MAX_CHARS, bound_tool_output


@pytest.mark.unit
def test_a_short_result_is_untouched():
    assert bound_tool_output("hello") == "hello"


@pytest.mark.unit
def test_a_long_result_keeps_both_ends():
    text = "A" * 3000 + "B" * 3000
    out = bound_tool_output(text, limit=1000)
    assert len(out) <= 1100
    assert out.startswith("A")
    assert out.endswith("B")


@pytest.mark.unit
def test_a_single_line_without_newlines_is_still_bounded():
    # A minified page is one line; line-based truncation would do nothing here.
    assert len(bound_tool_output("x" * 50000, limit=1000)) <= 1100


@pytest.mark.unit
def test_the_marker_says_what_was_dropped():
    out = bound_tool_output("y" * 5000, limit=1000)
    assert "truncated" in out
    # The marker is charged against the limit, so the kept budget is 1000 minus
    # the marker's reserve and the drop is what is left.
    assert "4050" in out


@pytest.mark.unit
def test_the_default_limit_is_used_when_none_is_given():
    out = bound_tool_output("z" * (TOOL_OUTPUT_MAX_CHARS * 3))
    assert len(out) <= TOOL_OUTPUT_MAX_CHARS + 100


@pytest.mark.unit
def test_the_limit_is_a_hard_ceiling_including_the_marker():
    for source_len in (4001, 4100, 5000, 12345):
        out = bound_tool_output("z" * source_len, limit=1000)
        assert len(out) <= 1000, source_len


@pytest.mark.unit
def test_a_zero_or_negative_limit_bounds_to_nothing():
    assert bound_tool_output("something", limit=0) == ""
    assert bound_tool_output("something", limit=-5) == ""
