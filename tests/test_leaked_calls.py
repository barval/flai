"""A tool call printed as text must not reach the user, and must still run.

Measured: the model narrated, printed a whole SVG inside an escaped
``<parameter>`` and kept generating, so the user watched XML for minutes. Chat
stops at the first ``<function=`` marker and recovers the call from the text.
"""

import json

import pytest

from app.leaked_calls import LeakedCallFilter, parse_leaked_tool_calls

pytestmark = pytest.mark.unit


def test_plain_prose_passes_through_and_is_flushed():
    filter_ = LeakedCallFilter()
    first = filter_.feed("Вот схема, которую вы просили. ")

    assert filter_.leaked is False
    assert first + filter_.flush() == "Вот схема, которую вы просили. "


def test_a_marker_split_across_chunks_is_still_caught():
    filter_ = LeakedCallFilter()

    assert filter_.feed("Готово. <tool_ca") == ""
    assert filter_.leaked is False

    out = filter_.feed("ll><function=write_document>")

    assert filter_.leaked is True
    assert out == "Готово. "
    assert filter_.feed("<parameter=name>x.svg</parameter>") == ""


def test_the_text_before_the_marker_is_kept():
    filter_ = LeakedCallFilter()

    out = filter_.feed("Перерисовал. <function=write_document><parameter=name>x.svg</parameter>")

    assert out == "Перерисовал. "
    assert filter_.flush() == ""


def test_a_mention_of_the_wrapper_without_a_call_is_not_a_leak():
    filter_ = LeakedCallFilter()
    text = "Синтаксис <tool_call> вводит блок вызова."

    assert filter_.feed(text) + filter_.flush() == text
    assert filter_.leaked is False


def test_escaped_content_is_unescaped_into_the_arguments():
    text = (
        "<tool_call><function=write_document>"
        "<parameter=name>scene.svg</parameter>"
        "<parameter=content>&lt;svg&gt;&quot;a&quot;&lt;/svg&gt;</parameter>"
        "</function></tool_call>"
    )
    calls, remaining = parse_leaked_tool_calls(text)

    assert [call["function"]["name"] for call in calls] == ["write_document"]
    arguments = json.loads(calls[0]["function"]["arguments"])
    assert arguments["content"] == '<svg>"a"</svg>'
    assert remaining.strip() == ""
