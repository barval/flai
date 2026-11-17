# tests/test_admin_table_headers.py
"""The Users table narrows its widest headings instead of widening the page.

Two long captions ("Network tools", "Camera access") pushed the column widths up;
the project's convention for a narrow column is the two-line heading the token
columns already use, and the translation is split on its own first space so each
language keeps one msgid per header.
"""

import pytest

pytestmark = pytest.mark.unit


def _template() -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parent.parent / "app/templates/admin.html").read_text(encoding="utf-8")


def test_the_network_tools_header_is_two_lines():
    html = _template()

    assert "{% set net_tools_header = _('Network tools').split(' ', 1) %}" in html
    assert "<th>{{ net_tools_header[0] }}<br>{{ net_tools_header[1] }}</th>" in html


def test_the_camera_access_header_is_two_lines():
    html = _template()

    assert "{% set camera_header = _('Camera access').split(' ', 1) %}" in html
    assert "<th>{{ camera_header[0] }}<br>{{ camera_header[1] }}</th>" in html


def test_both_translations_have_two_words_to_split():
    """A translation without a space would make the heading render nothing."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for msgid in ("Network tools", "Camera access"):
        block = re.search(rf'^msgid "{msgid}"\nmsgstr "(.*)"$', (root / "translations/ru/LC_MESSAGES/messages.po").read_text(encoding="utf-8"), re.M)
        assert block, msgid
        assert " " in block.group(1), f"the Russian {msgid!r} heading has no space to split"


def test_the_mobile_card_labels_follow_the_columns_that_exist():
    """On a phone each cell prints its own label; a positional list shifts silently.

    Adding the network column without touching the label list gave the camera cell
    "Actions" and left the network cell with no label at all.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "app/static/js/admin.js").read_text(encoding="utf-8")

    assert "hasCamera" in source
    assert "'Camera access', 'Network tools', 'Actions'" in source
    assert "'Network tools', 'Actions'" in source


def test_the_network_label_is_available_to_the_script():
    from pathlib import Path

    html = (Path(__file__).resolve().parent.parent / "app/templates/admin.html").read_text(encoding="utf-8")

    assert "'Network tools': {{ _('Network tools')|tojson }}" in html
