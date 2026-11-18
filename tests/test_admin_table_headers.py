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


def test_the_mobile_card_labels_follow_the_column_order():
    """A card label must name its own column, in the order the header prints them.

    Two rounds of this bug: the list was positional first (adding a column shifted
    every label), then ordered wrongly (the network and camera labels swapped).
    The header is the authority, so the test compares against it.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    source = (root / "app/static/js/admin.js").read_text(encoding="utf-8")
    html = (root / "app/templates/admin.html").read_text(encoding="utf-8")

    assert html.index("net_tools_header[0]") < html.index("camera_header[0]"), "header order"
    assert source.index("'Network tools', 'Camera access', 'Actions'") > 0, "camera present"
    assert source.index("'Network tools', 'Actions'") > 0, "camera absent"

def test_the_network_label_is_available_to_the_script():
    from pathlib import Path

    html = (Path(__file__).resolve().parent.parent / "app/templates/admin.html").read_text(encoding="utf-8")

    assert "'Network tools': {{ _('Network tools')|tojson }}" in html


def test_the_three_api_headers_are_two_lines_too():
    """SLM facts, Tavily API and the FLAI API count are narrow columns; the header
    was the widest thing in them."""
    from pathlib import Path

    html = (Path(__file__).resolve().parent.parent / "app/templates/admin.html").read_text(encoding="utf-8")

    for name in ("slm_facts_header", "tavily_header", "flai_api_header"):
        assert f"{{% set {name} = " in html, name
        assert f"{{{{ {name}[0] }}}}<br>{{{{ {name}[1] }}}}" in html, name
