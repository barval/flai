# tests/test_skills_admin_api.py
"""The panel's five routes: list, save, upload, toggle, delete.

The write path is where a bad name, a typo in `allowed-tools` and an attempt to
rewrite a tracked skill are all caught, so each of those gets its own test.
"""

import pytest
from flask import session

from app import skills
from app.routes.admin import skills_delete, skills_list, skills_save, skills_toggle, skills_upload

pytestmark = pytest.mark.unit

GOOD = "---\nname: {name}\ndescription: d\n{extra}---\nBody.\n"


def _call(view, app, path, *args, method="POST", json=None, auth=True):
    with app.test_request_context(path, method=method, json=json):
        if auth:
            session["is_admin"] = True
        response = view.__wrapped__(*args)
    body, status = response if isinstance(response, tuple) else (response, 200)
    return body.get_json(), status


@pytest.fixture
def install_root(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "INSTALLED_ROOT", tmp_path / "installed")
    monkeypatch.setattr(skills, "BUNDLED_ROOT", tmp_path / "bundled")
    skills.reset_cache()
    return tmp_path / "installed"


def test_save_writes_both_files(app, install_root):
    body, status = _call(
        skills_save,
        app,
        "/admin/api/skills/save",
        json={
            "name": "my-skill",
            "body_en": GOOD.format(name="my-skill", extra=""),
            "description_ru": "Описание",
            "body_ru": "Тело.",
        },
    )

    assert status == 200, body
    assert (install_root / "my-skill" / "SKILL.md").is_file()
    russian = (install_root / "my-skill" / "SKILL.ru.md").read_text(encoding="utf-8")
    assert "name: my-skill" in russian and "Описание" in russian


def test_a_typo_in_allowed_tools_blocks_the_save(app, install_root):
    body, status = _call(
        skills_save,
        app,
        "/admin/api/skills/save",
        json={"name": "typo-skill", "body_en": GOOD.format(name="typo-skill", extra="allowed-tools: web-serach\n")},
    )

    assert status == 400
    assert body["error"].startswith("⚠️ ")
    assert "web_search" in body["error"]
    assert not (install_root / "typo-skill").exists()


def test_a_foreign_tool_saves_with_a_warning(app, install_root):
    body, status = _call(
        skills_save,
        app,
        "/admin/api/skills/save",
        json={"name": "claude-skill", "body_en": GOOD.format(name="claude-skill", extra="allowed-tools: Read, Bash(git:*)\n")},
    )

    assert status == 200, body
    assert body["warnings"]
    assert (install_root / "claude-skill" / "SKILL.md").is_file()


def test_a_name_the_folder_cannot_hold_is_refused(app, install_root):
    for bad in ("../../etc", "My Skill", "Отчёт", "a" * 70, "skill.md"):
        body, status = _call(
            skills_save,
            app,
            "/admin/api/skills/save",
            json={"name": bad, "body_en": GOOD.format(name="x", extra="")},
        )
        assert status == 400, bad
        assert body["error"].startswith("⚠️ ")
    assert not install_root.exists() or not any(install_root.iterdir())


def test_a_bundled_skill_cannot_be_deleted_or_overwritten(app, install_root):
    (install_root.parent / "bundled" / "web-research").mkdir(parents=True)
    (install_root.parent / "bundled" / "web-research" / "SKILL.md").write_text(
        GOOD.format(name="web-research", extra=""), encoding="utf-8"
    )
    skills.reset_cache()

    body, status = _call(skills_delete, app, "/admin/api/skills/web-research", "web-research", method="DELETE")
    assert status == 409 and body["error"].startswith("⚠️ ")

    body, status = _call(
        skills_save,
        app,
        "/admin/api/skills/save",
        json={"name": "web-research", "body_en": GOOD.format(name="web-research", extra="")},
    )
    assert status == 409


def test_the_list_names_the_source_and_marks_each_tool(app, install_root):
    bundle = install_root.parent / "bundled" / "shipped"
    bundle.mkdir(parents=True)
    (bundle / "SKILL.md").write_text(GOOD.format(name="shipped", extra="allowed-tools: web_search, Read\n"), encoding="utf-8")
    skills.reset_cache()

    body, status = _call(skills_list, app, "/admin/api/skills", method="GET")

    assert status == 200
    shipped = next(s for s in body["skills"] if s["name"] == "shipped")
    assert shipped["source"] == "bundled"
    assert shipped["enabled"] is True
    assert {m["name"]: m["status"] for m in shipped["allowed_tools"]}["Read"] == "foreign"


def test_toggle_switches_a_skill_off_and_on(app, install_root):
    (install_root / "mine").mkdir(parents=True)
    (install_root / "mine" / "SKILL.md").write_text(GOOD.format(name="mine", extra=""), encoding="utf-8")
    skills.reset_cache()

    body, status = _call(skills_toggle, app, "/admin/api/skills/toggle", json={"name": "mine", "enabled": False})
    assert status == 200 and body["enabled"] is False

    body, status = _call(skills_toggle, app, "/admin/api/skills/toggle", json={"name": "mine", "enabled": True})
    assert status == 200 and body["enabled"] is True


def test_upload_reads_a_file_part(app, install_root):
    with app.test_request_context(
        "/admin/api/skills/upload",
        method="POST",
        data={"file": (__import__("io").BytesIO(GOOD.format(name="uploaded", extra="").encode()), "SKILL.md")},
        content_type="multipart/form-data",
    ):
        session["is_admin"] = True
        response = skills_upload.__wrapped__()
    body, status = response if isinstance(response, tuple) else (response, 200)

    assert status == 200, body.get_json()
    assert (install_root / "uploaded" / "SKILL.md").is_file()


def test_a_non_admin_is_forbidden(app, install_root):
    with app.test_request_context("/admin/api/skills", method="GET"):
        session["is_admin"] = False
        response = skills_list()
    body, status = response if isinstance(response, tuple) else (response, 200)
    assert status == 403
