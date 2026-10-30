"""The deep-analysis tools live in the same manifest as everything else."""

from __future__ import annotations

import pytest

from app.tasks.tools import get_capabilities
from app.tool_registry import RLM_TOOL_SPECS, TOOL_SPECS, sandbox_tool_definitions


@pytest.mark.unit
def test_the_sandbox_tools_are_the_four_the_actor_loop_calls():
    assert [spec.id for spec in RLM_TOOL_SPECS] == ["python", "llm", "web_fetch", "final"]
    assert all(spec.exec == "sandbox" for spec in RLM_TOOL_SPECS)


@pytest.mark.unit
def test_a_sandbox_only_tool_is_never_a_planner_capability():
    # A planner names task capabilities; python/web_fetch/final exist only inside
    # the sandboxed actor loop. "llm" is the one name both inventories use: a
    # task capability that writes an answer, and a sub-model call inside the
    # sandbox — the same word for two tools that never meet.
    capability_ids = {item["id"] for item in get_capabilities()}
    for sandbox_only in ("python", "web_fetch", "final"):
        assert sandbox_only not in capability_ids


@pytest.mark.unit
def test_every_sandbox_tool_has_a_callable_schema():
    for item in sandbox_tool_definitions():
        function = item["function"]
        assert function["parameters"]["type"] == "object"
        assert function["parameters"]["required"], function["name"]
        assert function["description"], function["name"]


@pytest.mark.unit
def test_the_rlm_module_reads_the_manifest():
    from modules.rlm import RLM_TOOL_DEFINITIONS

    assert sandbox_tool_definitions() == RLM_TOOL_DEFINITIONS
    assert {item["function"]["name"] for item in RLM_TOOL_DEFINITIONS} == {spec.id for spec in RLM_TOOL_SPECS}


@pytest.mark.unit
def test_the_manifest_keeps_the_two_inventories_apart():
    capability_ids = {spec.id for spec in TOOL_SPECS}
    assert {"python", "web_fetch", "final"}.isdisjoint(capability_ids)
    assert all(spec.exec != "sandbox" for spec in TOOL_SPECS)
