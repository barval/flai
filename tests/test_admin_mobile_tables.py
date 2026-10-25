# tests/test_admin_mobile_tables.py
"""Admin tabs that must not scroll sideways on a phone (Cameras, Backups)."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ADMIN_CSS = Path("app/static/css/admin.css").read_text(encoding="utf-8")
ADMIN_HTML = Path("app/templates/admin.html").read_text(encoding="utf-8")


def _mobile_block(needle: str) -> str:
    """The 768px media block holding ``needle`` (blocks are not contiguous)."""
    position = ADMIN_CSS.index(needle)
    index = 0
    while True:
        start = ADMIN_CSS.find("@media (max-width: 768px)", index)
        assert start != -1, f"{needle!r} is not inside a 768px media block"
        depth = 0
        end = start
        for end in range(start, len(ADMIN_CSS)):
            if ADMIN_CSS[end] == "{":
                depth += 1
            elif ADMIN_CSS[end] == "}":
                depth -= 1
                if depth == 0:
                    break
        if start <= position <= end:
            return ADMIN_CSS[start : end + 1]
        index = end + 1


def test_cameras_tab_becomes_a_card_list():
    block = _mobile_block("#cameras-table tr {")
    assert "display: grid;" in block
    assert '"num status code room"' in block
    assert '"preview preview preview preview"' in block
    assert "#cameras-table thead {\n        display: none;" in block
    assert "aspect-ratio: 16 / 9;" in block, "the preview shrinks instead of widening the row"


def test_backups_tab_becomes_a_card_list():
    block = _mobile_block("#backups-table tr {")
    assert "display: grid;" in block
    assert '"filename filename"' in block
    assert '"actions  actions"' in block
    assert "#backups-table td:nth-child(1) {\n        display: none;" in block, "row numbers go away"
    assert "overflow-wrap: anywhere;" in block


def test_neither_tab_widens_the_page():
    block = _mobile_block("#cameras-tab,")
    assert "#cameras-tab,\n    #backups-tab {\n        overflow-x: visible;" in block
    assert "min-width:" not in _mobile_block("#cameras-table tr {"), "no forced table width"
    assert "min-width:" not in _mobile_block("#backups-table tr {")


def test_the_hub_badge_uses_a_translated_short_label():
    """`t('Model Hub')` read a key that was never exposed to the script."""
    assert "'Model Hub': {{ _('Model Hub')|tojson }}" in ADMIN_HTML
    js = Path("app/static/js/admin-models.js").read_text(encoding="utf-8")
    assert "badge.textContent = t('Model Hub');" in js
    ru = Path("translations/ru/LC_MESSAGES/messages.po").read_text(encoding="utf-8")
    assert 'msgid "Model Hub"\nmsgstr "Хаб Моделей"' in ru
