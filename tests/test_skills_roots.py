# tests/test_skills_roots.py
"""A skill can be installed without touching git (v13.5).

Two roots exist: ``skills/`` ships with the project and is tracked, ``data/skills``
is git-ignored and written by the admin panel. A release may later ship a bundled
skill whose name an administrator already installed, and the merge must stay
deterministic rather than resolving by directory order.
"""

import pytest

from app import skills

pytestmark = pytest.mark.unit


def _write(root, name, description="d", body="b", extra=""):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{extra}---\n{body}\n",
        encoding="utf-8",
    )
    return folder


def _two_roots(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "BUNDLED_ROOT", tmp_path / "bundled")
    monkeypatch.setattr(skills, "INSTALLED_ROOT", tmp_path / "installed")
    skills.reset_cache()


def test_both_roots_are_scanned(tmp_path, monkeypatch):
    _two_roots(tmp_path, monkeypatch)
    _write(tmp_path / "bundled", "shipped")
    _write(tmp_path / "installed", "mine")

    found = {s.name: s.source for s in skills.discover_all()}

    assert found == {"shipped": "bundled", "mine": "installed"}


def test_an_installed_name_shadowed_by_a_bundled_one_is_reported(tmp_path, monkeypatch):
    _two_roots(tmp_path, monkeypatch)
    _write(tmp_path / "bundled", "report", description="the shipped one")
    _write(tmp_path / "installed", "report", description="the admin's one")

    found = skills.discover_all()

    active = [s for s in found if not s.shadowed]
    assert [(s.name, s.source) for s in active] == [("report", "bundled")]
    assert active[0].description == "the shipped one"
    assert [(s.name, s.source) for s in found if s.shadowed] == [("report", "installed")]


def test_an_installed_skill_knows_where_its_files_are(tmp_path, monkeypatch):
    _two_roots(tmp_path, monkeypatch)
    _write(tmp_path / "installed", "mine")

    skill = next(s for s in skills.discover_all() if s.name == "mine")

    assert skill.installed_path == str(tmp_path / "installed" / "mine")


def test_a_missing_install_root_is_not_an_error(tmp_path, monkeypatch):
    _two_roots(tmp_path, monkeypatch)
    _write(tmp_path / "bundled", "shipped")

    assert [s.name for s in skills.discover_all()] == ["shipped"]
