"""The prompt carries the catalog, never a body."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.skills import Skill
from app.utils import format_prompt

_SKILL = Skill(
    name="web-research",
    description="Gather sources before answering.",
    body="BODY-SENTINEL: search twice, then read.",
    allowed_tools=("web_search",),
    path="skills/web-research/SKILL.md",
)

_VARIABLES = {
    "response_language": "English",
    "style_instruction": "neutral",
    "current_time_str": "2026-10-31 12:00",
    "context_str": "",
}


@pytest.mark.unit
def test_the_catalog_is_in_the_prompt_and_the_body_is_not(app):
    with app.app_context(), patch("app.skills.discover", return_value=[_SKILL]):
        prompt = format_prompt("chat.template", dict(_VARIABLES), lang="en")
    assert "web-research" in prompt
    assert "Gather sources before answering." in prompt
    assert "BODY-SENTINEL" not in prompt
    # The catalog is a rule the model must be able to act on: the condition and
    # the order (activate before any other tool, then follow it).
    assert "call `activate_skill`" in prompt
    assert "BEFORE" in prompt


@pytest.mark.unit
def test_the_placeholder_renders_without_skills(app):
    with app.app_context(), patch("app.skills.discover", return_value=[]):
        prompt = format_prompt("chat.template", dict(_VARIABLES), lang="en")
    assert "{skill_catalog}" not in prompt
    assert "Matching skills" not in prompt
