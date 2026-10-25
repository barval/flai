# tests/test_chat_scope.py
"""The chat search-scope control is wired end to end (static checks)."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _chat_html() -> str:
    return Path("app/templates/chat.html").read_text(encoding="utf-8")


def test_chat_has_the_scope_control_and_script():
    html = _chat_html()
    for element in ("scope-toggle", "scope-popover", "scope-folder", "scope-from", "scope-to"):
        assert f'id="{element}"' in html, element
    assert "js/chat-scope.js" in html


def test_scope_control_leads_the_deep_analysis_row():
    """Scope first, then the analysis toggle — on desktop and on a phone.

    The row no longer carries a second margin-left:auto for the scope (that is
    what used to push the page sideways); the relocated buttons keep theirs.
    """
    html = _chat_html()
    rlm_row = html[html.index('<div class="rlm-controls">'):html.index('<div class="message-input">')]
    input_row = html[html.index('<div class="message-input">'):]

    assert 'id="scope-control"' in rlm_row
    assert 'id="scope-control"' not in input_row
    assert rlm_row.index('id="scope-control"') < rlm_row.index('id="rlm-toggle"'), "scope comes first"

    # Mobile JS still relocates only 🎤/📎/Send into that row.
    js = Path("app/static/js/chat-init.js").read_text(encoding="utf-8")
    assert "insertBefore(scope, attach)" not in js


def test_phone_row_shows_two_emoji_and_hides_the_toggle_wording():
    """A 360px row carries scope + 🔬 + three buttons: no room for words."""
    css = Path("app/static/css/chat.css").read_text(encoding="utf-8")
    assert "#scope-icon {\n    display: none;\n}" in css, "the folder icon is phone-only"
    phone = css[css.index("/* On a phone the row carries the relocated buttons too"):]
    assert "#scope-label,\n    .rlm-label-text {\n        display: none;" in phone
    assert "#scope-icon {\n        display: inline;" in phone

    html = _chat_html()
    assert '<span id="scope-icon">📁</span>' in html
    assert '<span class="rlm-label-text">{{ _(' in html


def test_scope_popover_cannot_overflow_the_viewport():
    """A fixed layer clamped in JS: the panel must never widen the document."""
    css = Path("app/static/css/chat.css").read_text(encoding="utf-8")
    scope_css = css[css.index("/* ===== Search scope (RAG filter) ===== */"):]
    assert ".scope-popover {" in scope_css
    assert "position: fixed;" in scope_css
    assert "max-width: calc(100vw - 16px);" in scope_css
    assert "#scope-icon" in scope_css

    js = Path("app/static/js/chat-scope.js").read_text(encoding="utf-8")
    assert "function positionPopover()" in js
    assert "window.innerWidth <= 768" in js, "phones get the bottom sheet"
    assert "Math.min(rect.right - width, window.innerWidth - width - 8)" in js, "desktop clamps to the right edge"


def test_scope_is_sent_with_every_message():
    js = Path("app/static/js/chat-init.js").read_text(encoding="utf-8")
    assert js.count("getChatScope") >= 2, "both the multipart and the JSON branch must send the scope"


def test_backend_reads_the_scope_and_resolves_it():
    routes = Path("app/routes/messages.py").read_text(encoding="utf-8")
    assert 'form_payload.get("filter_from")' in routes
    assert "resolve_scope" in routes
    assert '"scope_docs"' in routes

    queue = Path("app/queue.py").read_text(encoding="utf-8")
    assert "_rag_scope" in queue
    assert queue.count("doc_ids=self._rag_scope") == 3


def test_scope_strings_are_translated():
    en = Path("translations/en/LC_MESSAGES/messages.po").read_text(encoding="utf-8")
    ru = Path("translations/ru/LC_MESSAGES/messages.po").read_text(encoding="utf-8")
    for msgid, msgstr in (
        ("Search scope", "Область поиска"),
        ("All documents", "Все документы"),
        ("Uploaded from", "Загружено с"),
        ("Uploaded to", "Загружено по"),
    ):
        assert f'msgid "{msgid}"' in en
        assert f'msgid "{msgid}"\nmsgstr "{msgstr}"' in ru
