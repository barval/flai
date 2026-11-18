"""The reminder popup is wired end to end in the markup, the stream and the script.

A popup that no event reaches, or an event that no handler knows, fails silently: the
reminder is stored and the person never sees it until they open the chat.
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_the_chat_page_carries_the_popup():
    html = _read("app/templates/chat.html")

    assert 'id="scheduler-popup"' in html
    assert "data-scheduler-close" in html
    assert "scheduler-popup-text" in html


def test_the_popup_script_loads_before_the_event_stream():
    html = _read("app/templates/chat.html")

    script = html.index("js/chat-scheduler.js")
    stream = html.index("js/events.js")

    assert script < stream, "the handler must exist before the stream starts calling it"


def test_the_stream_dispatches_the_popup_event():
    events = _read("app/static/js/events.js")

    assert "case 'scheduler_popup':" in events
    assert "onSchedulerPopup(event.data)" in events


def test_the_script_closes_on_escape_and_keeps_a_queue():
    script = _read("app/static/js/chat-scheduler.js")

    assert "function onSchedulerPopup" in script
    assert "Escape" in script
    assert "schedulerPopupQueue" in script
    assert "textContent" in script and "innerHTML" not in script


def test_a_delivered_message_names_its_chat_instead_of_a_model():
    """The reminder is not this model's answer: the header names the chat that sent it."""
    messages = _read("app/static/js/chat-messages.js")

    assert "schedulerKind" in messages
    assert "scheduler_chat_reminder" in messages
    assert "message-scheduler-label" in messages
    # The response-style emoji describes how an answer was phrased; a delivered
    # sentence has no style, so the header must not carry the neutral robot.
    assert "!isSystemError && !schedulerKind" in messages


def test_the_wrapper_forwards_the_scheduler_kind():
    init = _read("app/static/js/chat-init.js")

    assert "schedulerKind" in init


def test_the_sidebar_reloads_for_a_chat_it_has_never_seen():
    """A scheduler chat is created on the server: without this it stays invisible."""
    events = _read("app/static/js/events.js")
    sessions = _read("app/static/js/chat-sessions.js")

    assert "reloadSessionsForIncomingMessage" in sessions
    assert "reloadSessionsForIncomingMessage()" in events
    assert "!sessionsData[data.session_id]" in events


def test_a_scheduler_chat_keeps_its_own_mark():
    """A delivered reminder must carry one mark, not two icons for one thing."""
    sessions = _read("app/static/js/chat-sessions.js")
    template = _read("app/templates/chat.html")

    assert "function sessionTitleMark" in sessions
    assert "function splitTitleMark" in sessions
    assert "Extended_Pictographic" in sessions
    assert "splitTitleMark(s.title).mark" in sessions
    assert "s.title[:1] in ('🔔', '⚙️')" in template


def test_a_delivery_chat_pulses_its_own_mark_not_an_envelope():
    """An envelope in a delivery channel reads as a letter nobody will answer."""
    sessions = _read("app/static/js/chat-sessions.js")
    css = _read("app/static/css/chat.css")

    assert "scheduler_kind: sessionsData[id].scheduler_kind" in sessions
    assert "unreadMark = true" in sessions
    assert "session-title-mark" in sessions
    assert "@keyframes session-mark-pulse" in css
    assert "prefers-reduced-motion: reduce" in css


def test_the_popup_scrolls_instead_of_leaving_the_screen():
    """Many scheduled items must not push the lower half of the panel out of reach."""
    css = _read("app/static/css/header-footer.css")

    assert "max-height: calc(100dvh - 70px)" in css
    assert "overflow-y: auto" in css
    assert "overscroll-behavior: contain" in css
    # The list itself scrolls first; the panel is the second line of defence.
    assert "max-height: 220px" in css


def test_the_scheduler_section_comes_first_in_the_profile_popup():
    """The list is what people open the popup for; the keys are configured once."""
    base = _read("app/templates/base.html")

    scheduler = base.index('id="scheduler-section"')
    tavily = base.index('id="tavily-section"')
    # The panel's own header, not the button label in the page header above it.
    api_keys = base.index("<span>{{ _('api_keys_title') }}</span>")

    assert scheduler < tavily < api_keys


def test_a_service_chat_has_no_composer():
    """A scheduler chat is a delivery channel: nothing there is a question."""
    template = _read("app/templates/chat.html")
    css = _read("app/static/css/chat.css")
    sessions = _read("app/static/js/chat-sessions.js")
    routes = _read("app/routes/chat.py")
    db = _read("app/db.py")

    assert "current_session_kind" in routes
    assert "scheduler_kind" in db.split("def get_user_sessions")[1][:2500]
    assert "CURRENT_SESSION_KIND" in template
    assert "body.service-chat .rlm-controls" in css
    assert "body.service-chat .message-input" in css
    assert "function applyServiceChatMode" in sessions
    assert "updateChatModeForSession(sessionId)" in sessions


def test_every_sidebar_renderer_builds_the_row_the_same_way():
    """The queue-status refresh rebuilt the list without the kind, so the actions chat got
    an envelope. One shape, three callers."""
    sessions = _read("app/static/js/chat-sessions.js")
    queue = _read("app/static/js/chat-queue.js")

    assert "function sessionsForRender()" in sessions
    assert "scheduler_kind: sessionsData[id].scheduler_kind" in sessions
    assert sessions.count("sessionsForRender()") == 3
    assert "sessionsForRender()" in queue
    # No other place may rebuild the row by hand.
    assert "Object.keys(sessionsData).map" not in queue
