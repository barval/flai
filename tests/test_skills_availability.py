"""A skill is offered only when the tools it promises exist.

Network diagnostics is the case this exists for: the sidecar is optional, and a skill that
tells the model to measure a network it cannot reach is worse than no skill at all. The same
rule hides any future service-specific skill while its service is down.

Per-user permission is deliberately not part of this: the tools are advertised to everyone who
can chat and the refusal is a sentence from `execute_tool`, which is the documented design for
network diagnostics and cameras alike.
"""

import pytest

from app import skills

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def fresh_catalog():
    skills.reset_cache()
    yield
    skills.reset_cache()


def test_a_skill_without_its_tools_is_not_offered():
    lines = skills.catalog("en", available={"web_search", "read_page"})

    assert "net-diagnostics" not in lines
    assert "web-research" in lines


def test_one_available_tool_is_enough_to_offer_the_skill():
    lines = skills.catalog("en", available={"net_dns"})

    assert "net-diagnostics" in lines


def test_without_an_availability_set_nothing_is_hidden():
    lines = skills.catalog("en")

    assert "net-diagnostics" in lines
    assert "documents" in lines


def test_a_skill_without_tools_is_never_hidden(tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: plain\ndescription: plain instructions\n---\nDo the thing.\n", encoding="utf-8"
    )

    lines = skills.catalog("en", root=tmp_path, available=set())

    assert "plain" in lines


def test_net_tools_are_known_when_the_sidecar_is_down():
    """Otherwise the panel calls a real tool a name from another harness."""
    marks = skills.classify_allowed_tools(("net_dns",), set())

    assert [mark.status for mark in marks] == ["unavailable"]


def test_the_prompt_catalog_follows_the_live_tools(test_app, monkeypatch):
    import app.tools as tools

    monkeypatch.setattr(
        tools, "get_tool_definitions", lambda lang="ru": [{"function": {"name": "web_search"}}]
    )

    with test_app.app_context():
        from app.utils import _load_skill_catalog

        lines = _load_skill_catalog("en")

    assert "net-diagnostics" not in lines
    assert "web-research" in lines
