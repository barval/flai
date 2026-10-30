"""A skill the kernel names is loaded before the answer, not left to the model."""

from __future__ import annotations

import json

import pytest

from app.kernel import (
    MAX_KERNEL_SKILLS,
    build_kernel_prompt,
    decision_to_route,
    parse_kernel_decision,
)
from app.queue import RedisRequestQueue


def _reply(**fields) -> str:
    payload = {"intent": "search", "query": "ecb rate", **fields}
    return json.dumps(payload)


@pytest.mark.unit
def test_a_known_skill_is_kept_and_an_invented_one_is_dropped():
    decision = parse_kernel_decision(_reply(skills=["web-research", "no-such-skill"]), {"web-research"})
    assert decision.skills == ("web-research",)


@pytest.mark.unit
def test_without_a_catalog_no_skill_survives():
    # The catalog is the permission list: a name the installation does not have
    # can never be loaded, so a stale prompt cannot reach uninstalled code.
    decision = parse_kernel_decision(_reply(skills=["web-research"]))
    assert decision.skills == ()


@pytest.mark.unit
def test_skills_are_capped_and_de_duplicated():
    known = {"a", "b", "c"}
    decision = parse_kernel_decision(_reply(skills=["a", "a", "b", "c"]), known)
    assert len(decision.skills) == MAX_KERNEL_SKILLS
    assert decision.skills == ("a", "b")


@pytest.mark.unit
def test_a_selected_skill_routes_the_request_through_the_chat_loop():
    decision = parse_kernel_decision(_reply(skills=["web-research"]), {"web-research"})
    route = decision_to_route(decision, "cross-check the ECB rate")
    # The skill's instructions name tools that live in the chat loop, so a
    # request with a skill is answered there even though the intent was search.
    assert route["action"] == "none"
    assert route["skills"] == ("web-research",)
    assert route["query"] == "cross-check the ECB rate"


@pytest.mark.unit
def test_a_request_without_a_skill_keeps_its_route():
    route = decision_to_route(parse_kernel_decision(_reply()), {"web-research"})
    assert route["action"] == "search"
    assert route["skills"] == ()


@pytest.mark.unit
def test_the_prompt_carries_the_skill_list_and_the_field_is_documented():
    prompt = build_kernel_prompt("q", "- calculator: sums", "12:00", "", "", skills="- `web-research`: gather sources")
    assert "web-research" in prompt
    assert '"skills"' in prompt
    assert "# SKILLS" in prompt


class _Stub:
    def __init__(self):
        import logging

        self.logger = logging.getLogger("test")


@pytest.mark.unit
def test_the_skill_body_goes_into_the_system_prompt(app):

    messages: list[dict] = [{"role": "system", "content": "BASE"}, {"role": "user", "content": "u"}]
    with app.app_context():
        RedisRequestQueue._preload_skills(_Stub(), messages, ("web-research",), "en")
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].startswith("BASE")
    assert len(messages[0]["content"]) > len("BASE")
    # No synthetic tool exchange: measured, the model reads that as "the tool
    # already ran" and answers from memory instead of searching.
    assert [message["role"] for message in messages] == ["system", "user"]


@pytest.mark.unit
def test_a_skill_that_is_not_installed_changes_nothing(app):

    messages: list[dict] = [{"role": "system", "content": "BASE"}, {"role": "user", "content": "u"}]
    with app.app_context():
        RedisRequestQueue._preload_skills(_Stub(), messages, ("no-such-skill",), "en")
    assert messages == [{"role": "system", "content": "BASE"}, {"role": "user", "content": "u"}]


@pytest.mark.unit
def test_without_a_system_message_one_is_inserted(app):

    messages: list[dict] = [{"role": "user", "content": "u"}]
    with app.app_context():
        RedisRequestQueue._preload_skills(_Stub(), messages, ("web-research",), "en")
    assert [message["role"] for message in messages] == ["system", "user"]
