# tests/test_skills_admin_ui.py
"""The Skills tab: where it sits, what it offers, and that it speaks both languages.

The panel is the only way an administrator installs a skill, so a missing control
or an untranslated label is a defect in the feature, not a cosmetic one.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "app/templates/admin.html"
SCRIPT = ROOT / "app/static/js/admin-skills.js"
CATALOGS = {
    "en": ROOT / "translations/en/LC_MESSAGES/messages.po",
    "ru": ROOT / "translations/ru/LC_MESSAGES/messages.po",
}

pytestmark = pytest.mark.unit


def _msgids(path: Path) -> set[str]:
    return set(re.findall(r'^msgid "(.*)"$', path.read_text(encoding="utf-8"), re.M))


def test_the_tab_sits_right_after_models():
    html = TEMPLATE.read_text(encoding="utf-8")

    assert html.index('data-tab="models"') < html.index('data-tab="skills"')
    assert html.index('data-tab="skills"') < html.index('data-tab="mcp"')


def test_the_panel_section_carries_every_control():
    html = TEMPLATE.read_text(encoding="utf-8")

    for marker in (
        'id="skills-tab"',
        'id="skills-body"',
        'id="skills-upload"',
        'id="skills-new"',
        'id="skill-editor"',
        'id="skill-save"',
    ):
        assert marker in html, marker


def test_every_string_the_script_translates_exists_in_both_catalogs():
    source = SCRIPT.read_text(encoding="utf-8")
    keys = set(re.findall(r"\bt\('([^']+)'\)", source))

    assert keys, "the script must route its labels through t()"
    for lang, path in CATALOGS.items():
        missing = keys - _msgids(path)
        assert not missing, f"{lang} is missing: {sorted(missing)}"


def test_the_new_panel_strings_are_translated_in_both_catalogs():
    needed = {"Skills", "Enabled", "Edit", "Read-only", "Upload .md", "New skill"}
    for lang, path in CATALOGS.items():
        assert needed <= _msgids(path), f"{lang} is missing: {sorted(needed - _msgids(path))}"


def test_every_mutating_request_carries_the_csrf_token():
    """The panel is CSRF-protected; a bare fetch() is a 400 in a real browser.

    conftest disables CSRF and the API tests call the view directly, so nothing
    else in the suite would notice a missing token.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    writes = [
        line for line in source.splitlines() if "api('/admin/api/skills" in line and "api('/admin/api/skills'" in line
    ]
    assert writes, "the script must call the skills API"
    assert "fetchWithCSRF" in source
    assert "await fetch(" not in source.replace("const response = await fetchWithCSRF", "")


def test_the_toolbar_buttons_are_the_same_height():
    """«Upload .md» and «New skill» sit on one line; two paddings made one taller."""
    css = (ROOT / "app/static/css/admin.css").read_text(encoding="utf-8")
    block = css[css.index(".skills-toolbar") : css.index(".skills-table-wrapper")]
    assert ".skills-toolbar .btn-primary" in block or ".skills-toolbar button" in block
    assert "height:" in block


def test_the_russian_fields_explain_what_an_empty_translation_does():
    """Without the line a reader cannot tell whether empty means "no translation"."""
    html = TEMPLATE.read_text(encoding="utf-8")

    assert "skill-hint" in html
    assert "Leave it empty and the base description is shown instead" in html
    for lang, path in CATALOGS.items():
        msgid = 'msgid "Leave it empty and the base description is shown instead"'
        assert msgid in path.read_text(encoding="utf-8"), lang
