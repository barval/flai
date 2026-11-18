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
