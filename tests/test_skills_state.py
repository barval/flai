# tests/test_skills_state.py
"""A disabled skill leaves the catalogue and the name the kernel may return.

The flag lives in the database rather than in the frontmatter: a file edit must
not change behaviour without the panel knowing, and a stale model answer must not
be able to activate a skill somebody switched off.
"""

import json

import pytest

from app import skilldb, skills

pytestmark = pytest.mark.requires_db


@pytest.fixture
def one_skill(tmp_path, monkeypatch, test_app):
    """A single bundled skill in a throw-away root, enabled after the test."""
    monkeypatch.setattr(skills, "BUNDLED_ROOT", tmp_path / "bundled")
    monkeypatch.setattr(skills, "INSTALLED_ROOT", tmp_path / "installed")
    folder = tmp_path / "bundled" / "shipped"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: shipped\ndescription: The shipped one\n---\nDo the thing.\n",
        encoding="utf-8",
    )
    skills.reset_cache()
    with test_app.app_context():
        yield
        skilldb.set_enabled("shipped", True)
        skills.reset_cache()


def test_an_enabled_skill_is_in_the_catalog(one_skill):
    assert "`shipped`" in skills.catalog("en")


def test_a_disabled_skill_leaves_the_catalog_and_the_activation_enum(one_skill):
    skilldb.set_enabled("shipped", False)
    skills.reset_cache()

    assert skills.catalog("en") == ""
    assert skills.activate("shipped", "en") is None
    assert skills.skill_tool_definition() is None
    assert [s.name for s in skills.active_skills()] == []


def test_the_kernel_is_offered_no_name_for_a_disabled_skill(one_skill):
    from modules.base import _kernel_skills

    skilldb.set_enabled("shipped", False)
    skills.reset_cache()

    assert "shipped" not in _kernel_skills()


def test_the_catalog_is_still_shown_when_nothing_is_disabled(one_skill):
    skilldb.set_enabled("shipped", True)
    skills.reset_cache()

    assert "`shipped`" in skills.catalog("en")
    assert "shipped" in json.dumps(skills.skill_tool_definition())
