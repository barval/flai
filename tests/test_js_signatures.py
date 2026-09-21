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
    (message_new, role=user) while an outgoing request carrying an optimistic
    element for the same session is still in flight.

    Regression: sending a message rendered the user bubble TWICE. Root cause:
    the optimistic element (displayUserMessage, chat-init.js) has data-tempId
    but NO data-message-id yet, while the SSE message_new echo (published by
    save_message inside the POST handler, before the HTTP response returns)
    carries the real message_id. Neither dedup guard in onMessageNew matches:
      * displayedMessageIds.has(real_id)  -> false (added only by the POST
        response handler, which runs strictly later)
      * querySelector('[data-message-id=real_id]') -> the optimistic element
        has only data-tempId, so the selector misses it
    -> displayMessage renders a second copy (disappears after F5, since history
    loads the message once with real ids).

    The guard must check pendingRequestIds for an entry whose sessionId matches
    the echoing session_id AND which has NOT yet been confirmed (no resolved
    message id) — i.e. the optimistic element is still unconfirmed.
    """
    events_src = (JS_DIR / "events.js").read_text(encoding="utf-8")

    # The guard must reject the SSE echo for role=user while an unconfirmed
    # outgoing request for the same session exists.
    assert re.search(r"data\.role\s*(==|===|!=|!==)\s*['\"]user['\"]", events_src), (
        "no role===user branch guard in onMessageNew"
    )

    # It must consult the in-flight outgoing requests (pendingRequestIds)
    # filtered by the echoing session.
    body = events_src[re.search(r"function\s+onMessageNew\s*\(", events_src).start() :]
    assert re.search(r"pendingRequestIds", body), "onMessageNew does not consult pendingRequestIds"
    assert re.search(r"sessionId\s*(==|===|!=|!==)\s*data\.session_id", body), (
        "onMessageNew echo guard not filtered by session_id"
    )

    # And the message must be skipped (return) BEFORE displayMessage runs.
    user_guard = body[: body.find("displayMessage")] if "displayMessage" in body else body
    assert re.search(r"return", user_guard), "user-role SSE echo is not short-circuited before displayMessage"
