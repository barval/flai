"""The activation tool is advertised only when a skill exists, and never pruned."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.skills import Skill
from app.tools import execute_tool, get_tool_definitions, select_tool_definitions

_SKILL = Skill(
    name="web-research",
    description="Gather sources before answering.",
    body="1. Search.\n2. Open the best source.\n",
    allowed_tools=("web_search",),
    path="skills/web-research/SKILL.md",
)


@pytest.mark.unit
def test_no_skills_means_no_activation_tool(app):
    with app.app_context(), patch("app.skills.discover", return_value=[]):
        names = [item["function"]["name"] for item in get_tool_definitions("en")]
    assert "activate_skill" not in names


@pytest.mark.unit
def test_a_skill_adds_the_activation_tool(app):
    with app.app_context(), patch("app.skills.discover", return_value=[_SKILL]):
        definitions = get_tool_definitions("en")
    matching = [item for item in definitions if item["function"]["name"] == "activate_skill"]
    assert len(matching) == 1
    parameters = matching[0]["function"]["parameters"]
    assert parameters["required"] == ["name"]
    assert parameters["properties"]["name"]["enum"] == ["web-research"]


@pytest.mark.unit
def test_the_kernel_cannot_prune_the_activation_tool(app):
    # Narrowing to one unrelated tool would otherwise remove the only way to load
    # a skill for the rest of the turn.
    with app.app_context(), patch("app.skills.discover", return_value=[_SKILL]):
        selected = [item["function"]["name"] for item in select_tool_definitions(("calculator",), "en")]
    assert "activate_skill" in selected
    assert "calculator" in selected


@pytest.mark.unit
def test_activation_returns_the_body(app):
    with app.app_context(), patch("app.skills.discover", return_value=[_SKILL]):
        body = execute_tool("activate_skill", {"name": "web-research"}, {"app": app, "lang": "en"})
    assert body.startswith("1. Search.")


@pytest.mark.unit
def test_an_unknown_skill_is_refused_by_name(app):
    with app.app_context(), patch("app.skills.discover", return_value=[_SKILL]):
        message = execute_tool("activate_skill", {"name": "nope"}, {"app": app, "lang": "en"})
    assert "nope" in message
    assert "1. Search." not in message


@pytest.mark.unit
def test_an_oversized_body_is_bounded(app):
    huge = Skill(
        name="huge",
        description="d",
        body="x" * 9000,
        allowed_tools=(),
        path="skills/huge/SKILL.md",
    )
    with app.app_context(), patch("app.skills.discover", return_value=[huge]):
        body = execute_tool("activate_skill", {"name": "huge"}, {"app": app, "lang": "en"})
    assert len(body) < 9000
    assert "truncated" in body
