# tests/test_chat_scope.py
"""The chat search-scope control is wired end to end (static checks)."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _chat_html() -> str:
    return Path("app/templates/chat.html").read_text(encoding="utf-8")


def test_chat_has_the_scope_control_and_script():
    html = _chat_html()
    for element in ("scope-toggle", "scope-popover", "scope-folders", "scope-from", "scope-to", "scope-clear"):
        assert f'id="{element}"' in html, element
    assert 'id="scope-apply"' not in html, "folders/date edits are live; no Apply step"
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


def test_the_corpus_is_sent_with_every_message():
    """The Documents-panel snapshot travels as doc_ids, not as a folder filter."""
    js = Path("app/static/js/chat-init.js").read_text(encoding="utf-8")
    assert js.count("doc_ids: corpusDocIds()") == 1, "the JSON branch carries the corpus"
    assert js.count("formData.append('doc_ids', JSON.stringify(corpusDocIds()))") == 1
    assert "getChatScope" not in js, "the popover ticks documents now; no separate filter is sent"

    scope = Path("app/static/js/chat-scope.js").read_text(encoding="utf-8")
    assert "window.setCorpusSelection" in scope, "folder/date edits write the corpus live"
    assert "window.getSelectedDocIds" in scope, "the scope mirrors the Documents checkboxes"
    assert "window.getCorpusSummary" in scope


def test_every_submit_reads_the_corpus_state_not_a_hidden_select():
    """A corpus picked from the scope popover must reach the submit even when the
    Documents panel was never opened.

    The selection used to travel through the hidden ``#rlm-docs`` select, whose
    options are created only by the Documents panel. Choosing folders from the
    chat page left the select empty, so the request went out with an empty
    corpus: the chat silently un-checked the analysis toggle and a task was
    queued with ``doc_ids = []`` (searched every document instead of the
    selected ones).
    """
    for path in (
        "app/static/js/chat-init.js",
        "app/static/js/chat-tasks.js",
        "app/static/js/events.js",
    ):
        js = Path(path).read_text(encoding="utf-8")
        assert "getSelectedDocIds" in js, path
        assert "rlm-docs" not in js, f"{path} still reads the hidden select"

    assert 'id="rlm-docs"' not in _chat_html()
    docs = Path("app/static/js/chat-documents.js").read_text(encoding="utf-8")
    assert "window.getSelectedDocIds = function" in docs, "the corpus has one accessor"
    assert "syncRlmDocsSelect" not in docs, "the select mirror died with the select"


def test_deep_analysis_refuses_an_empty_corpus():
    """Toggle on without a corpus: refuse the send and say why.

    The old flow un-checked the toggle silently and sent an ordinary chat, so a
    user who thought the folders were selected got a plain answer and never
    learned that the analysis had not run.
    """
    js = Path("app/static/js/chat-init.js").read_text(encoding="utf-8")
    block = js[js.index("const rlmToggle = document.getElementById('rlm-toggle');"):]
    block = block[: block.index("isSending = true;")]

    assert "const hasCorpus = docIds.length > 0 || hasImage || hasDocs;" in block
    assert block.index("if (!hasCorpus)") < block.index("if (hasQuestion)")
    refusal = block[block.index("if (!hasCorpus)"):block.index("if (hasQuestion)")]
    assert "alert(t('rlm_needs_documents'))" in refusal
    assert "return;" in refusal
    assert "rlmToggle.checked = false" not in block, "the toggle must stay on"

    html = _chat_html()
    assert "'rlm_needs_documents':" in html
    for catalog, msgstr in (
        ("translations/en/LC_MESSAGES/messages.po", "Deep analysis needs at least one document or an image"),
        (
            "translations/ru/LC_MESSAGES/messages.po",
            "Для глубокого анализа выберите хотя бы один документ или приложите изображение",
        ),
    ):
        text = Path(catalog).read_text(encoding="utf-8")
        assert f'msgid "rlm_needs_documents"\nmsgstr "{msgstr}"' in text, catalog

    # A voice message whose corpus emptied before the transcription lands says
    # the same instead of only un-checking the toggle.
    events = Path("app/static/js/events.js").read_text(encoding="utf-8")
    assert "t('rlm_needs_documents')" in events


def test_documents_panel_has_a_root_group_and_feeds_the_corpus():
    js = Path("app/static/js/chat-documents.js").read_text(encoding="utf-8")
    assert "const ROOT_GROUP = '__root__';" in js
    assert "data-folder-id=\"${ROOT_GROUP}\"" in js
    assert "window.setCorpusSelection = function" in js
    assert "window.getSelectedDocIds = function" in js
    assert "window.getCorpusSummary = function" in js
    assert "window.applyCorpusFilter = function" not in js, "the old Apply step is gone"
    # Turning deep analysis off must not empty the corpus anymore.
    assert "rlmSelectedDocs = new Set();" not in js.split("rlmToggleEl.addEventListener")[1].split("});")[0]


def test_backend_reads_the_corpus_and_keeps_the_legacy_filter():
    routes = Path("app/routes/messages.py").read_text(encoding="utf-8")
    assert 'form_payload.get("filter_from")' in routes
    assert "resolve_scope" in routes
    assert '"scope_docs"' in routes
    assert "_corpus_doc_ids" in routes
    assert '"doc_ids"' in routes

    queue = Path("app/queue.py").read_text(encoding="utf-8")
    assert "_rag_scope" in queue
    assert queue.count("doc_ids=self._rag_scope") == 3


def test_scope_strings_are_translated():
    en = Path("translations/en/LC_MESSAGES/messages.po").read_text(encoding="utf-8")
    ru = Path("translations/ru/LC_MESSAGES/messages.po").read_text(encoding="utf-8")
    for msgid, msgstr in (
        ("Search scope", "Область поиска"),
        ("All documents", "Все документы"),
        ("Folders", "Папки"),
        ("folders ({count})", "папок ({count})"),
        ("documents ({count})", "документов ({count})"),
        ("Select at least one folder.", "Выберите хотя бы одну папку."),
        ("Uploaded from", "Загружено с"),
        ("Uploaded to", "Загружено по"),
    ):
        assert f'msgid "{msgid}"' in en
        assert f'msgid "{msgid}"\nmsgstr "{msgstr}"' in ru
