"""A skill is a folder with a SKILL.md: catalog by description, body on demand."""

from __future__ import annotations

import pytest

from app.skills import Skill, activate, catalog, discover, reset_cache

VALID = """---
name: web-research
description: Gather sources before answering a factual question.
allowed-tools: web_search, read_page
---
1. Search.
2. Open the two best sources.
"""


def _skill_file(root, name, text, folder=None):
    directory = root / (folder or name)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(text, encoding="utf-8")
    return directory


@pytest.fixture(autouse=True)
def _fresh_cache():
    reset_cache()
    yield
    reset_cache()


@pytest.mark.unit
def test_a_valid_file_yields_its_fields(tmp_path):
    _skill_file(tmp_path, "web-research", VALID)
    skills = discover(tmp_path)
    assert len(skills) == 1
    skill = skills[0]
    assert isinstance(skill, Skill)
    assert skill.name == "web-research"
    assert skill.description == "Gather sources before answering a factual question."
    assert skill.allowed_tools == ("web_search", "read_page")
    assert skill.body.startswith("1. Search.")


@pytest.mark.unit
def test_a_broken_file_does_not_hide_its_siblings(tmp_path):
    _skill_file(tmp_path, "no-frontmatter", "just some text\n")
    _skill_file(tmp_path, "no-description", "---\nname: no-description\n---\nbody\n")
    _skill_file(tmp_path, "web-research", VALID)
    assert [skill.name for skill in discover(tmp_path)] == ["web-research"]


@pytest.mark.unit
def test_an_overlong_description_is_rejected(tmp_path):
    text = f"---\nname: wordy\ndescription: {'x' * 1025}\n---\nbody\n"
    _skill_file(tmp_path, "wordy", text)
    assert discover(tmp_path) == []


@pytest.mark.unit
def test_an_unportable_name_is_rejected(tmp_path):
    # The name travels as a tool argument and as a folder; a space breaks both.
    text = "---\nname: my skill\ndescription: d\n---\nbody\n"
    _skill_file(tmp_path, "x", text, folder="x")
    assert discover(tmp_path) == []


@pytest.mark.unit
def test_the_folder_must_match_the_name(tmp_path):
    text = "---\nname: alpha\ndescription: d\n---\nbody\n"
    _skill_file(tmp_path, "beta", text)
    assert discover(tmp_path) == []


@pytest.mark.unit
def test_the_russian_variant_is_preferred_for_russian(tmp_path):
    directory = _skill_file(tmp_path, "web-research", VALID)
    (directory / "SKILL.ru.md").write_text(
        "---\nname: web-research\ndescription: Сначала собери источники.\n---\n1. Поиск.\n",
        encoding="utf-8",
    )
    skill = discover(tmp_path)[0]
    assert skill.for_lang("ru") == ("Сначала собери источники.", "1. Поиск.\n")
    assert skill.for_lang("en") == (skill.description, skill.body)


@pytest.mark.unit
def test_two_folders_cannot_claim_one_name(tmp_path, caplog):
    caplog.set_level("WARNING", logger="app.skills")
    # Requiring folder == name is what makes a duplicate impossible: two folders
    # cannot share a name, so neither claimer is loaded rather than one silently
    # shadowing the other.
    first = "---\nname: dup\ndescription: first\n---\nbody one\n"
    second = "---\nname: dup\ndescription: second\n---\nbody two\n"
    _skill_file(tmp_path, "dup-a", first)
    _skill_file(tmp_path, "dup-b", second)
    assert discover(tmp_path) == []
    assert "dup" in caplog.text


@pytest.mark.unit
def test_the_catalog_is_empty_without_skills_and_one_line_per_skill_with_them(tmp_path):
    assert catalog("en", root=tmp_path) == ""
    _skill_file(tmp_path, "web-research", VALID)
    reset_cache()
    text = catalog("en", root=tmp_path)
    assert "web-research" in text
    assert "Gather sources" in text
    assert "1. Search." not in text


@pytest.mark.unit
def test_activation_returns_the_body_or_nothing(tmp_path):
    _skill_file(tmp_path, "web-research", VALID)
    reset_cache()
    assert activate("web-research", "en", root=tmp_path).startswith("1. Search.")
    assert activate("does-not-exist", "en", root=tmp_path) is None
