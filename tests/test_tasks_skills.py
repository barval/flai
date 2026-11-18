# tests/test_tasks_skills.py
"""A background task can be told *how* to work by a skill, the way a chat turn is.

The catalog reaches the planner; the body of the chosen skill reaches every step, because
a task that writes a report needs the same instructions a chat answer gets.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.unit


def test_a_named_skill_contributes_its_body(test_app):
    from app.tasks.executor import _task_skills_block

    with test_app.app_context():
        block = _task_skills_block(("web-research",), "en")

    assert "web_search" in block
    assert "Web research" in block


def test_an_unknown_or_disabled_skill_is_skipped(test_app):
    from app.tasks.executor import _task_skills_block

    with test_app.app_context():
        assert _task_skills_block(("no-such-skill",), "en") == ""
        assert _task_skills_block((), "en") == ""


def test_both_plan_templates_ask_for_skills():
    for lang in ("en", "ru"):
        template = (ROOT / f"prompts/{lang}/task_plan.template").read_text(encoding="utf-8")
        assert "{skills_catalog}" in template, lang
        assert '"skills"' in template, lang


def test_the_parser_keeps_the_skills_field():
    from app.tasks.executor import _parse_plan

    plan = _parse_plan('{"title": "t", "skills": ["web-research"], "steps": [{"seq": 1, "goal": "g", "tool": "llm"}]}')

    assert plan["skills"] == ["web-research"]


def test_the_parser_refuses_a_skill_that_is_not_a_string():
    from app.tasks.executor import _parse_plan

    plan = _parse_plan('{"title": "t", "skills": [1, null], "steps": [{"seq": 1, "goal": "g", "tool": "llm"}]}')

    assert plan["skills"] == []


def test_the_skills_block_leads_the_context(test_app):
    from app.tasks.executor import _context_with_skills

    with test_app.app_context():
        digest = _context_with_skills("Step 1 (llm): done", ("web-research",), "en")

    assert digest.index("web_search") < digest.index("Step 1")


def test_an_empty_skill_list_leaves_the_context_alone(test_app):
    from app.tasks.executor import _context_with_skills

    with test_app.app_context():
        assert _context_with_skills("Step 1 (llm): done", (), "en") == "Step 1 (llm): done"
