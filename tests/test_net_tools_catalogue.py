# tests/test_net_tools_catalogue.py
"""What the chat model is offered, and what the user sees while a tool runs.

A tool that is advertised while its sidecar is down hangs for its whole timeout, so
availability decides both the catalogue and the stream label.
"""

from pathlib import Path

import pytest

from app import netdiag, tools

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent


def test_no_sidecar_means_no_tools(monkeypatch, test_app):
    monkeypatch.setattr(netdiag, "available", lambda: False)
    with test_app.app_context():
        names = {d["function"]["name"] for d in tools.get_tool_definitions("ru")}

    assert not names & set(netdiag.NET_TOOL_IDS)


def test_a_sidecar_adds_seven(monkeypatch, test_app):
    monkeypatch.setattr(netdiag, "available", lambda: True)
    with test_app.app_context():
        names = {d["function"]["name"] for d in tools.get_tool_definitions("ru")}

    assert set(netdiag.NET_TOOL_IDS) <= names


def test_each_tool_has_a_stream_label():
    source = (ROOT / "app/static/js/events.js").read_text(encoding="utf-8")

    for tool_id in netdiag.NET_TOOL_IDS:
        assert f"{tool_id}:" in source, tool_id


def test_each_label_is_translated_in_both_catalogs():
    chat = (ROOT / "app/templates/chat.html").read_text(encoding="utf-8")
    catalogs = {
        lang: (ROOT / f"translations/{lang}/LC_MESSAGES/messages.po").read_text(encoding="utf-8")
        for lang in ("en", "ru")
    }

    for tool_id in netdiag.NET_TOOL_IDS:
        assert f"'tool_{tool_id}': {{{{ _('tool_{tool_id}')|tojson }}}}" in chat, tool_id
        msgid = f'msgid "tool_{tool_id}"\nmsgstr "'
        for lang, text in catalogs.items():
            start = text.index(msgid) + len(msgid)
            assert text[start] != '"', f"{lang}: tool_{tool_id} is not translated"


def test_the_definitions_are_typed_and_name_their_arguments():
    for definition in tools.NET_TOOL_DEFINITIONS:
        schema = definition["function"]["parameters"]
        assert schema["type"] == "object"
        assert schema["required"], definition["function"]["name"]
        for name, spec in schema["properties"].items():
            assert spec["type"] in ("string", "integer", "boolean"), (name, spec["type"])
