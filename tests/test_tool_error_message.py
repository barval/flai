"""A tool failure must not leak its technical detail into the conversation."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.tools import _EXECUTOR_MAP, execute_tool


@pytest.mark.unit
def test_a_tool_exception_returns_a_generic_message(app):
    def boom(context, **kwargs):
        raise RuntimeError("connection to flai-qdrant refused at 10.0.0.5:6333")

    with patch.dict(_EXECUTOR_MAP, {"boom": boom}):
        result = execute_tool("boom", {}, {"app": app, "lang": "en"})

    assert "10.0.0.5" not in result
    assert "6333" not in result
    assert "could not complete" in result


@pytest.mark.unit
def test_an_unknown_tool_still_names_itself(app):
    result = execute_tool("no_such_tool", {}, {"app": app, "lang": "en"})
    assert "no_such_tool" in result


@pytest.mark.unit
def test_a_large_result_is_bounded_on_the_way_out(app):
    def big(context, **kwargs):
        return "q" * 20000

    with patch.dict(_EXECUTOR_MAP, {"big": big}):
        result = execute_tool("big", {}, {"app": app, "lang": "en"})

    assert len(result) < 20000
    assert "truncated" in result
