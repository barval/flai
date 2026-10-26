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
    assert '"filename filename filename"' in block, "the filename spans the whole card width"
    assert '"actions actions' in block
    assert "#backups-table td:nth-child(1) {\n        grid-area: num;" in block, "the row number stays"
    assert "overflow-wrap: anywhere;" in block


def _rule_block(selector: str) -> str:
    """One rule from a 768px block: a media query also holds rules of the other tab."""
    block = _mobile_block(selector)
    start = block.index(selector)
    return block[start : block.index("}", start) + 1]


def test_neither_tab_widens_the_page():
    block = _mobile_block("#cameras-tab,")
    assert "#cameras-tab,\n    #backups-tab {\n        overflow-x: visible;" in block
    assert "min-width:" not in _rule_block("#cameras-table tr {"), "no forced table width"
    assert "min-width:" not in _rule_block("#backups-table tr {")


def test_the_hub_badge_uses_a_translated_short_label():
    """`t('Model Hub')` read a key that was never exposed to the script."""
    assert "'Model Hub': {{ _('Model Hub')|tojson }}" in ADMIN_HTML
    js = Path("app/static/js/admin-models.js").read_text(encoding="utf-8")
    assert "badge.textContent = t('Model Hub');" in js
    ru = Path("translations/ru/LC_MESSAGES/messages.po").read_text(encoding="utf-8")
    assert 'msgid "Model Hub"\nmsgstr "Хаб Моделей"' in ru


def test_hub_badge_sits_in_its_own_column_before_the_size():
    """Fixed badge column: every footprint starts at the same x."""
    assert ".installed-file-badge {\n    flex: 0 0 7.2rem;" in ADMIN_CSS
    assert ".installed-file-badge:empty { background: transparent; }" in ADMIN_CSS

    js = Path("app/static/js/admin-models.js").read_text(encoding="utf-8")
    assert "details.appendChild(badge);" in js
    assert js.index("details.appendChild(badge);") < js.index("details.appendChild(meta);")


def test_settings_rows_become_two_columns_and_two_rows():
    """Variable over description, value over default — a four-column table
    pushed the default column off the screen and hid long values."""
    block = _mobile_block(".settings-table thead tr,")
    assert "display: grid;" in block
    assert "grid-template-areas:" in block
    assert '"name  value"' in block
    assert '"desc  default"' in block
    for area in ("name", "desc", "value", "default"):
        assert f"grid-area: {area};" in block
    # The search filter hides rows through the attribute; a block display revived them.
    assert ".settings-table tbody tr[hidden] {\n        display: none;" in block
    assert "overflow-wrap: anywhere;" in block, "long values must wrap instead of vanishing"
    assert "#settings-tab {\n        overflow-x: visible;" in block


def test_users_become_cards_with_their_own_headings():
    """Seventeen columns cannot fit a phone: the table turned into a card per
    user, and each cell keeps its localized heading (data-label)."""
    block = _mobile_block("#users-table tbody td::before {")
    assert "min-width: 0;" in block
    assert "overflow-x: visible;" in block
    assert "max-height: none;" in block, "the wrapper must not be a scroll box on a phone"
    assert "#users-table thead {\n        display: none;" in block
    assert "#users-table tbody tr {\n        display: block;" in block
    assert "content: attr(data-label);" in block
    assert "#users-table tbody td:nth-child(2)::before {\n        content: none;" in block, "login is the card title"

    js = Path("app/static/js/admin.js").read_text(encoding="utf-8")
    assert "function labelUserCardCells(row)" in js
    assert "labelUserCardCells(row);" in js
    assert "'Outgoing tokens'" in js and "'Camera access'" in js

    html = ADMIN_HTML
    for label in ("Outgoing tokens", "Incoming tokens", "SLM Facts", "Tavily API", "Camera access"):
        assert f"'{label}': {{{{ _('{label}')|tojson }}}}" in html, f"{label} is not in TRANSLATIONS"


def test_the_page_title_keeps_half_its_spacing():
    """The browser's default h2 margin over the container padding and the
    30 px gap below used a whole row of a phone screen (measured 35 / 30)."""
    block = _mobile_block(".admin-panel-title {\n        margin-top: 0;")
    assert "margin-top: 0;" in block
    assert "margin-bottom: 15px;" in block
