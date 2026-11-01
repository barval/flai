"""A restricted profile sees only the tools its role was granted."""

from __future__ import annotations

import pytest

from app.tasks import research
from app.tools import ROLE_TOOLS, TOOL_DEFINITIONS, definitions_for_role, get_tool_definitions


@pytest.mark.unit
def test_the_research_role_cannot_see_a_writing_tool():
    names = {item["function"]["name"] for item in definitions_for_role("research", "ru")}
    # Every granted tool is offered, minus the ones the installation lacks.
    assert names <= set(ROLE_TOOLS["research"])
    assert "web_search" in names
    assert "calculator" not in names
    assert "camera_snapshot" not in names
    assert "activate_skill" not in names
    assert not [name for name in names if name.startswith("mcp__")]


@pytest.mark.unit
def test_an_unknown_role_gets_the_full_catalogue():
    # A typo in a role name must never empty a profile's tool list.
    assert definitions_for_role("no_such_role", "ru") == get_tool_definitions("ru")


@pytest.mark.unit
def test_every_granted_name_exists_in_the_catalogue():
    known = {item["function"]["name"] for item in TOOL_DEFINITIONS}
    for role, names in ROLE_TOOLS.items():
        assert names, role
        missing = names - known
        assert not missing, f"{role} grants tools that do not exist: {sorted(missing)}"


@pytest.mark.unit
def test_the_research_step_reads_its_tool_set_from_the_role_table():
    assert set(research.RESEARCH_TOOLS) == ROLE_TOOLS["research"]
