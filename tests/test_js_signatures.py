"""JS wrapper signature integrity: window.displayMessage must forward ALL
positional parameters of the underlying displayMessage in chat-messages.js.

Regression: the chat-init.js wrapper declared 18 parameters while the wrapped
function gained a 19th (promptTokens) — the token-counter feature silently
rendered «↓0»/no input tokens in every UI path that goes through
window.displayMessage (history reload, result_completed, message_new, camera).

These tests extract both signatures with a regex and compare parameter lists,
so the next added parameter cannot be dropped silently again.
"""

import re
from pathlib import Path

import pytest

JS_DIR = Path(__file__).resolve().parent.parent / "app" / "static" / "js"


def _extract_params(source: str, func_name: str) -> list[str]:
    """Return the parameter list of a function declaration (top-level)."""
    pattern = re.compile(
        r"function\s+" + re.escape(func_name) + r"\s*\(([^)]*)\)",
        re.DOTALL,
    )
    match = pattern.search(source)
    if not match:
        pytest.fail(f"function {func_name} not found in source")
    params = [p.strip() for p in match.group(1).split(",") if p.strip()]
    return params


def test_display_message_wrapper_forwards_all_parameters():
    """chat-init.js wrapper must declare exactly the same positional
    parameters as the displayMessage it wraps (chat-messages.js)."""
    base_src = (JS_DIR / "chat-messages.js").read_text(encoding="utf-8")
    init_src = (JS_DIR / "chat-init.js").read_text(encoding="utf-8")

    base_params = _extract_params(base_src, "displayMessage")
    wrapper_src = re.search(r"window\.displayMessage\s*=\s*function\s*\(([^)]*)\)", init_src, re.DOTALL)
    assert wrapper_src, "window.displayMessage wrapper not found in chat-init.js"
    wrapper_params = [p.strip() for p in wrapper_src.group(1).split(",") if p.strip()]

    assert wrapper_params == base_params, (
        f"window.displayMessage wrapper drops parameters: base={base_params} wrapper={wrapper_params}"
    )
    # And the wrapper must forward every parameter positionally, in order.
    # Anchor on the wrapper body: the originalDisplayMessage call follows the
    # window.displayMessage assignment.
    wrapper_block = init_src[wrapper_src.start() : wrapper_src.start() + 1200]
    call_match = re.search(r"originalDisplayMessage\(([^)]*)\)", wrapper_block, re.DOTALL)
    assert call_match, "originalDisplayMessage call not found in wrapper"
    forwarded = [p.strip() for p in call_match.group(1).split(",") if p.strip()]
    assert forwarded == wrapper_params, f"wrapper forwards {forwarded} but declares {wrapper_params}"


def test_load_messages_wrapper_keeps_signature():
    """The window.loadMessages wrapper (chat-init.js) must not shadow the
    loadMessages defined in chat-messages.js with a different behaviour."""
    init_src = (JS_DIR / "chat-init.js").read_text(encoding="utf-8")
    # Wrapper must delegate to the original with the same arguments.
    match = re.search(r"originalLoadMessages\(([^)]*)\)", init_src)
    assert match, "originalLoadMessages call not found in chat-init.js"
    forwarded = [p.strip() for p in match.group(1).split(",") if p.strip()]
    assert forwarded == ["sessionId"], f"unexpected forward args: {forwarded}"


def test_on_message_new_skips_user_echo_during_active_outgoing():
    """onMessageNew (events.js) must NOT render a user message echoed by SSE
    (message_new, role=user) while an unconfirmed optimistic element for the
    same session exists in the DOM.

    Regression (repeated on 2026-09-21 13:15): sending a message rendered the
    user bubble TWICE; after F5 it settled back to one.

    Root cause: the optimistic element (displayUserMessage, chat-init.js) has
    data-tempId but NO data-message-id yet, while the SSE message_new echo
    (published by save_message inside the POST handler, before the HTTP
    response returns) carries the real message_id. Neither dedup guard in
    onMessageNew matches:
      * displayedMessageIds.has(real_id)  -> false (added only by the POST
        response handler, which runs strictly later)
      * querySelector('[data-message-id=real_id]') -> the optimistic element
        has only data-tempId, so the selector misses it
    -> displayMessage renders a second copy (disappears after F5, since history
    loads the message once with real ids).

    The guard MUST be DOM-based (an optimistic element is one that carries
    data-tempId WITHOUT data-message-id for the echoing session). It must NOT
    rely on pendingRequestIds: the fetchQueueStatus() "safety valve"
    (chat-queue.js) wipes ALL pendingRequestIds whenever the server reports
    idle — and that poll races the in-flight POST, deleting the very entry the
    guard would need (observed duplicate on 2026-09-21 with the
    pendingRequestIds-based guard).
    """
    events_src = (JS_DIR / "events.js").read_text(encoding="utf-8")

    # The guard must reject the SSE echo for role=user while an unconfirmed
    # optimistic element exists.
    assert re.search(r"data\.role\s*(==|===|!=|!==)\s*['\"]user['\"]", events_src), (
        "no role===user branch guard in onMessageNew"
    )

    body = events_src[re.search(r"function\s+onMessageNew\s*\(", events_src).start() :]

    # The guard must consult the DOM for the optimistic element, keyed by
    # data-temp-id (the only marker the optimistic element has before the
    # POST response assigns the real data-message-id).
    assert re.search(r"querySelectorAll\s*\(\s*['\"`][^'\"`]*data-temp-id", body), (
        "onMessageNew user-echo guard does not look up optimistic elements via data-temp-id"
    )

    # CSS attribute selectors are CASE-SENSITIVE: dataset.tempId serialises to
    # data-temp-id (kebab-case), so a [data-tempId] selector matches nothing
    # (headless repro 2026-09-21: guard silently never ran, duplicate returned).
    for js_file in ("events.js", "chat-init.js", "chat-messages.js"):
        src = (JS_DIR / js_file).read_text(encoding="utf-8")
        for m in re.finditer(r"querySelector(?:All)?\s*\([^;]*?\[data-tempId\][^;]*?\)", src, re.DOTALL):
            pytest.fail(
                f"{js_file}: selector uses [data-tempId] which never matches "
                f"(must be [data-temp-id]): {m.group(0)[:120]}"
            )

    # The optimistic element must be treated as unconfirmed only when it has
    # no real id yet (data-message-id), and must belong to the echoing session.
    assert re.search(r"dataset\.messageId|data-message-id", body), (
        "onMessageNew guard does not check whether the optimistic element is unconfirmed"
    )

    # And the echo must be skipped (return) BEFORE displayMessage runs.
    user_guard = body[: body.find("displayMessage")] if "displayMessage" in body else body
    assert re.search(r"return", user_guard), "user-role SSE echo is not short-circuited before displayMessage"


def test_display_message_returned_element_is_optimistic_marker_source():
    """displayUserMessage sets tempId via dataset.tempId — the DOM attribute
    is data-temp-id. Every consumer that looks the element up by attribute
    must use the kebab-case selector; verified above. This test pins the
    serialisation itself so the casing contract cannot drift silently."""
    init_src = (JS_DIR / "chat-init.js").read_text(encoding="utf-8")
    assert re.search(r"msgElement\.dataset\.tempId\s*=", init_src), (
        "displayUserMessage no longer sets dataset.tempId — update the data-temp-id selector contract in consumers"
    )


def test_rlm_optimistic_message_is_reconciled_before_sse_echo():
    """RLM's optimistic user bubble must carry the same DOM marker used by
    onMessageNew to suppress the persisted message_new echo."""
    init_src = (JS_DIR / "chat-init.js").read_text(encoding="utf-8")
    rlm_src = init_src[init_src.index("async function sendRlmAnalysis") : init_src.index("async function sendMessage")]

    assert re.search(r"optimisticMessage\.dataset\.tempId\s*=\s*`temp-\$\{timestamp\}`", rlm_src)
    assert "optimisticMessage.dataset.sessionId = currentSessionId" in rlm_src
    assert re.search(r"data\.user_message_id[\s\S]*?delete optimisticMessage\.dataset\.tempId", rlm_src)
    assert "optimisticMessage.dataset.messageId = data.user_message_id" in rlm_src


def test_send_message_locks_after_rlm_branch():
    """RLM must be dispatched before the normal send path acquires isSending,
    otherwise sendRlmAnalysis rejects its own call as already in progress."""
    init_src = (JS_DIR / "chat-init.js").read_text(encoding="utf-8")
    send_src = init_src[init_src.index("async function sendMessage") :]
    rlm_branch = send_src[: send_src.index("const input = document.getElementById('message-input')")]

    assert "sendRlmAnalysis();" in rlm_branch
    assert rlm_branch.index("sendRlmAnalysis();") < rlm_branch.index("isSending = true;")
    assert "if (isSending) return;" in init_src
    assert "isSending = true;" in rlm_branch


def test_documents_panel_shows_model_names_with_distinct_icons():
    docs_src = (JS_DIR / "chat-documents.js").read_text(encoding="utf-8")
    chat_template = (JS_DIR.parent.parent / "templates" / "chat.html").read_text(encoding="utf-8")

    assert 'document-embedding"><span class="document-status-icon">🔄</span> ${escapeHtml(displayModel)}' in docs_src
    assert (
        'document-description-model"><span class="document-status-icon">🖼️</span> ${escapeHtml(doc.description_model)}'
        in docs_src
    )
    assert "document_embedding_model" not in docs_src
    assert "document_recognition_model" not in docs_src
    assert "document_embedding_model" not in chat_template
    assert "document_recognition_model" not in chat_template


def test_documents_panel_displays_processing_duration_as_whole_seconds():
    docs_src = (JS_DIR / "chat-documents.js").read_text(encoding="utf-8")

    assert "const processingSeconds = Math.round(doc.processing_time)" in docs_src
    assert "t('seconds_suffix')" in docs_src
    assert "⏱️ ${processingSeconds}${t('seconds_suffix')}" in docs_src
    assert "minutes_abbr" not in docs_src


def test_document_upload_does_not_show_success_alert():
    docs_src = (JS_DIR / "chat-documents.js").read_text(encoding="utf-8")
    upload_src = docs_src[docs_src.index("function uploadDocument") : docs_src.index("function initDocumentsView")]

    assert "alert(t('document_uploaded'))" not in upload_src
    assert "loadDocuments()" in upload_src


def test_document_indexing_timer_ticks_and_stops_when_indexed():
    docs_src = (JS_DIR / "chat-documents.js").read_text(encoding="utf-8")

    assert "setInterval(updateDocumentProcessingTimers, 1000)" in docs_src
    assert "performance.now()" in docs_src
    assert "querySelectorAll('.doc-live-timer[data-index-status=\"indexing\"]')" in docs_src
    assert 'data-processing-seconds="${processingSeconds}"' in docs_src
    assert "Math.round(doc.processing_time)" in docs_src
    assert "processingTimeStr = ` ⏱️ ${processingSeconds}${t('seconds_suffix')}`" in docs_src
    assert "existingTimer?.dataset.timerStartedAt || performance.now()" in docs_src


def test_documents_panel_shows_queue_position_from_server_status():
    docs_src = (JS_DIR / "chat-documents.js").read_text(encoding="utf-8")
    queue_src = (JS_DIR / "chat-queue.js").read_text(encoding="utf-8")
    queue_css = (JS_DIR.parent / "css" / "chat.css").read_text(encoding="utf-8")

    assert "function updateDocumentQueueStatus(queueStatus)" in docs_src
    assert "updateDocumentQueueStatus(data)" in queue_src
    assert "queueStatus.queued" in docs_src
    assert "item.doc_id" in docs_src
    assert "position_info?.position" in docs_src
    assert "document-status-icon queued" in docs_src
    assert ".document-status-icon.queued" in queue_css


def test_hub_context_length_is_editable_input():
    """Model Hub 'Context length' value must be a manually editable input, not
    a read-only span next to the slider."""
    admin_template = (JS_DIR.parent.parent / "templates" / "admin.html").read_text(encoding="utf-8")

    assert re.search(r'<input[^>]*id="hub-context-value"', admin_template), (
        "hub-context-value must be an <input> (typing a context length by hand)"
    )
    assert not re.search(r'<span[^>]*id="hub-context-value"', admin_template), (
        "hub-context-value must no longer be a <span>"
    )


def test_hub_context_input_filters_and_clamps():
    """The ctxValue input must (a) strip non-digit characters while typing,
    (b) clamp the committed value into the slider range [1024, 262144], and
    (c) keep the slider + value display in sync and re-run the fit recalc."""
    hub_src = (JS_DIR / "admin-modelHub.js").read_text(encoding="utf-8")

    assert "CTX_MIN = 1024" in hub_src
    assert "CTX_MAX = 262144" in hub_src
    # Digit-only whitelist filter while typing.
    assert re.search(r"replace\(\s*/\D", hub_src), "ctxValue input must strip non-digit characters"
    # Commit path (Enter/blur) clamps into the slider range.
    assert re.search(r"Math\.max\s*\([^)]*CTX_MIN", hub_src)
    assert re.search(r"Math\.min\s*\([^)]*CTX_MAX", hub_src)
    # Committed value must be written back to the slider and re-render fits.
    assert "ctxSlider.value = ctx" in hub_src
    assert "ctxValue.value = String(ctx)" in hub_src
    assert "ctxValue.addEventListener('change'" in hub_src
    # Slider drag still syncs the input display.
    assert "ctxValue.value = ctxSlider.value" in hub_src


def test_hub_status_messages_blink_smoothly():
    """Model Hub status lines must pulse smoothly (as the chat task-progress
    indicators do), not jump between opacity steps."""
    admin_css = (JS_DIR.parent / "css" / "admin.css").read_text(encoding="utf-8")

    # The animation must live on the shared .hub-scan-status class (both the
    # search line and the recalc line blink), easing smoothly.
    assert re.search(r"\.hub-scan-status\s*\{[^}]*animation:\s*hub-status-pulse 2s ease-in-out", admin_css)
    # Smooth pulse, not a step: opacity eases between 0.65 and 1 over the cycle.
    keyframes = admin_css[admin_css.index("@keyframes hub-status-pulse") :]
    assert re.search(r"0%,\s*100%\s*\{[^}]*opacity:\s*0\.6", keyframes)
    assert re.search(r"50%\s*\{[^}]*opacity:\s*1", keyframes)
    assert "49%" not in keyframes
    # The old hard step blink must be gone.
    assert "hub-status-blink" not in admin_css


def test_hub_fit_calc_status_shown_on_first_render():
    """The fit-calculation status must be shown (with the 'Computing fit…'
    label) while the colored fit tiers are fetched after the first render."""
    hub_src = (JS_DIR / "admin-modelHub.js").read_text(encoding="utf-8")
    admin_template = (JS_DIR.parent.parent / "templates" / "admin.html").read_text(encoding="utf-8")

    # First render starts the calc-status + a shared recalc helper.
    assert "recalcFits('hub_calculating')" in hub_src
    assert "function recalcFits" in hub_src
    # The helper shows the status, runs collectFits, hides on completion.
    recalc_body = hub_src[hub_src.index("function recalcFits") :]
    assert "showStatus(recalcStatus, true)" in recalc_body
    assert "collectFits(() => {" in recalc_body
    assert "showStatus(recalcStatus, false)" in recalc_body
    # Repeated recalculation (slider) reuses the same helper with the old label.
    assert "recalcFits('hub_recalculating')" in hub_src
    # The new label must be wired in the admin TRANSLATIONS block.
    assert "'hub_calculating': {{ _('Computing fit…')|tojson }}" in admin_template


def test_hub_recalc_status_ticks_elapsed_seconds():
    """The fit-calculation status must tick elapsed seconds ('(N сек)') while
    shown — mirroring the chat phase timers — and stop when the calc finishes."""
    hub_src = (JS_DIR / "admin-modelHub.js").read_text(encoding="utf-8")

    # A dedicated per-run interval + stop helper live in the hub module.
    assert re.search(r"let recalcTimer\s*=\s*null", hub_src)
    assert re.search(r"function _stopRecalcTimer\b", hub_src)
    assert re.search(r"clearInterval\s*\(\s*recalcTimer\s*\)", hub_src)
    # The recalc path starts a 1-second interval that rewrites the status text
    # with a seconds suffix ('(5 сек)'), localized via the existing hub_seconds key.
    recalc_body = hub_src[hub_src.index("function recalcFits") :]
    assert "setInterval(" in recalc_body
    assert re.search(r"recalcStatus\.textContent\s*=\s*[^;]*\([^;]*secs[^;]*suffix", recalc_body)
    assert "t('hub_seconds')" in recalc_body
    # Completion stops the timer before hiding the status.
    assert "_stopRecalcTimer()" in recalc_body[: recalc_body.index("showStatus(recalcStatus, false)")]


def test_hub_calculating_msgid_in_both_catalogs():
    """The new 'Computing fit…' message must exist in both .po catalogs."""
    for lang_dir in ("en", "ru"):
        po = (JS_DIR.parent.parent.parent / "translations" / lang_dir / "LC_MESSAGES" / "messages.po").read_text(
            encoding="utf-8"
        )
        assert 'msgid "Computing fit…"' in po, f"msgid missing in {lang_dir}"
        assert "msgstr" in po[po.index('msgid "Computing fit…"') : po.index('msgid "Computing fit…"') + 200]


def test_admin_active_tab_survives_language_switch_reload():
    """Admin tab position must persist across the language-change full reload:
    initAdminTabs saves the active data-tab on click and restores it on load."""
    src = (JS_DIR / "admin-models.js").read_text(encoding="utf-8")

    tabs_src = src[: src.index("function loadModelConfigs")]
    assert "localStorage.setItem('admin_active_tab'" in tabs_src
    assert "localStorage.getItem('admin_active_tab'" in tabs_src
    # Restore must verify the saved tab still exists (Cameras is conditional).
    assert "document.querySelector('.admin-tab[data-tab=\"' + saved" in tabs_src
