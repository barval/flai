"""MCP tools appear in the chat catalogue only when an operator configured them."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.tools import SKILL_LOADER_TOOL, get_tool_definitions, select_tool_definitions

_FAKE = {
    "type": "function",
    "function": {"name": "mcp__rooms__ping", "description": "ping", "parameters": {"type": "object"}},
}


@pytest.mark.unit
def test_nothing_is_added_when_no_server_is_configured(app):
    with (
        app.app_context(),
        patch("app.mcp_registry.tool_definitions", side_effect=AssertionError("must not be reached")),
    ):
        names = [item["function"]["name"] for item in get_tool_definitions("en")]
    assert "mcp__rooms__ping" not in names


@pytest.mark.unit
def test_configured_servers_are_appended(app):
    app.config["MCP_ENABLED"] = True
    with app.app_context(), patch("app.mcp_registry.tool_definitions", return_value=[_FAKE]):
        names = [item["function"]["name"] for item in get_tool_definitions("en")]
    assert names[-1] == "mcp__rooms__ping"


@pytest.mark.unit
def test_a_broken_registry_leaves_the_built_ins_intact(app):
    app.config["MCP_ENABLED"] = True
    with app.app_context(), patch("app.mcp_registry.tool_definitions", side_effect=RuntimeError("boom")):
        names = [item["function"]["name"] for item in get_tool_definitions("en")]
    assert "calculator" in names
    assert not [name for name in names if name.startswith("mcp__")]


@pytest.mark.unit
def test_the_kernel_selection_can_name_an_mcp_tool(app):
    app.config["MCP_ENABLED"] = True
    with app.app_context(), patch("app.mcp_registry.tool_definitions", return_value=[_FAKE]):
        selected = select_tool_definitions(("mcp__rooms__ping",), "en")
    # The installed skills' loader rides along; see test_skills_wiring.py.
    names = [item["function"]["name"] for item in selected if item["function"]["name"] != SKILL_LOADER_TOOL]
    assert names == ["mcp__rooms__ping"]


@pytest.mark.unit
def test_dispatch_routes_a_namespaced_name_to_the_registry(app):
    from app.tools import execute_tool

    with patch("app.mcp_registry.call", return_value="pong") as called:
        result = execute_tool("mcp__rooms__ping", {"x": 1}, {"app": app, "lang": "en"})
    assert result == "pong"
    called.assert_called_once_with("mcp__rooms__ping", {"x": 1})


@pytest.mark.unit
def test_a_failing_mcp_call_returns_a_localized_message(app):
    from app.tools import execute_tool

    with patch("app.mcp_registry.call", side_effect=RuntimeError("connect http://10.0.0.5:1234 failed")):
        result = execute_tool("mcp__rooms__ping", {}, {"app": app, "lang": "en"})
    assert "10.0.0.5" not in result
    assert "could not complete" in result


@pytest.mark.unit
def test_an_unadvertised_namespaced_name_is_refused(app):
    from app.tools import execute_tool

    with patch("app.mcp_registry.call", side_effect=ValueError("unknown MCP tool: mcp__nope__x")):
        result = execute_tool("mcp__nope__x", {}, {"app": app, "lang": "en"})
    assert "could not complete" in result


@pytest.mark.unit
def test_an_mcp_result_is_bounded(app):
    from app.tools import execute_tool

    with patch("app.mcp_registry.call", return_value="q" * 9000):
        result = execute_tool("mcp__rooms__ping", {}, {"app": app, "lang": "en"})
    assert len(result) < 9000
    assert "truncated" in result
