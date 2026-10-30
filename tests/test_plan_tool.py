"""The planner records its plan as a tool call, and text is still the fallback."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from app.tasks import executor as ex
from app.tasks.tools import get_capabilities


@pytest.mark.unit
def test_the_schema_offers_exactly_the_capabilities_a_plan_may_use():
    parameters = ex.write_plan_tool()["function"]["parameters"]
    enum = parameters["properties"]["steps"]["items"]["properties"]["tool"]["enum"]
    expected = sorted({str(item["id"]) for item in get_capabilities()} - {"continuity"})
    assert enum == expected
    assert "continuity" not in enum
    assert parameters["properties"]["steps"]["items"]["required"] == ["goal", "tool", "checkpoint_criteria"]


@pytest.mark.unit
def test_a_tool_call_becomes_the_plan_json(monkeypatch):
    seen: list[dict] = []

    def fake_call(app, prompt, cancel_check, lang, temperature=None, status_callback=None, tools=None):
        seen.append({"tools": tools})
        return "", {}

    plan = {"title": "Two steps", "steps": [{"goal": "Search", "tool": "search", "checkpoint_criteria": "sources"}]}
    monkeypatch.setattr(ex, "_run_planner_full", fake_call)
    monkeypatch.setattr("app.tasks.runner.last_tool_calls", lambda: [{"function": {"arguments": plan}}])

    raw = ex._run_planner(None, "prompt", lambda: False, "en")

    assert seen[0]["tools"][0]["function"]["name"] == "write_plan"
    assert json.loads(raw) == plan
    # The text parser and the tool call agree on one shape.
    assert ex._parse_plan(raw)["steps"][0]["tool"] == "search"


@pytest.mark.unit
def test_prose_is_returned_unchanged_when_the_model_ignores_the_tool(monkeypatch):
    monkeypatch.setattr(
        ex,
        "_run_planner_full",
        lambda *a, **k: ('{"title": "t", "steps": [{"goal": "g", "tool": "llm"}]}', {}),
    )
    monkeypatch.setattr("app.tasks.runner.last_tool_calls", lambda: [])
    raw = ex._run_planner(None, "prompt", lambda: False, "en")
    assert ex._parse_plan(raw)["steps"][0]["goal"] == "g"


@pytest.mark.unit
def test_a_model_without_tool_support_is_retried_as_text(monkeypatch):
    calls: list[dict] = []

    def fake_call(app, prompt, cancel_check, lang, temperature=None, status_callback=None, tools=None):
        calls.append({"tools": tools})
        if tools:
            return "⚠️ Failed to get an answer: tools are not supported", {}
        return '{"title": "t", "steps": [{"goal": "g", "tool": "llm"}]}', {}

    monkeypatch.setattr(ex, "_run_planner_full", fake_call)
    monkeypatch.setattr("app.tasks.runner.last_tool_calls", lambda: [])

    raw = ex._run_planner(Mock(), "prompt", lambda: False, "en")

    assert len(calls) == 2
    assert calls[0]["tools"] is not None
    assert calls[1]["tools"] is None
    assert ex._parse_plan(raw)["steps"][0]["goal"] == "g"


@pytest.mark.unit
def test_the_planner_usage_comes_from_the_call_that_produced_the_plan(monkeypatch):
    responses = iter([("⚠️ Failed", {"prompt_tokens": 1}), ("{}", {"prompt_tokens": 7, "completion_tokens": 3})])
    monkeypatch.setattr(ex, "_run_planner_full", lambda *a, **k: next(responses))
    monkeypatch.setattr("app.tasks.runner.last_tool_calls", lambda: [])

    ex._run_planner(Mock(), "prompt", lambda: False, "en")

    assert ex._planner_usage() == {"prompt_tokens": 7, "completion_tokens": 3}
