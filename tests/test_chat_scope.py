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
