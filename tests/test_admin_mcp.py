"""The MCP tab's values are ordinary settings, and its Test button is a real probe."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from flask import session

from app.routes.admin import test_mcp_server as mcp_test_view
from app.settings_registry import build_schema, coerce


@pytest.mark.unit
def test_mcp_is_a_dedicated_group_of_three_keys():
    group = next(item for item in build_schema(_FakeApp()) if item["id"] == "mcp")
    assert group["dedicated"] is True
    assert [field["key"] for field in group["fields"]] == ["MCP_ENABLED", "MCP_TIMEOUT_S", "MCP_SERVERS"]


class _FakeApp:
    config: dict = {"MCP_ENABLED": False, "MCP_TIMEOUT_S": 10, "MCP_SERVERS": ""}


@pytest.mark.unit
def test_the_server_list_is_validated_by_the_loader_that_reads_it():
    assert coerce("MCP_SERVERS", '[{"name":"a","url":"http://a/mcp"}]', "str")[1] is None
    assert coerce("MCP_SERVERS", "[]", "str") == ("[]", None)
    assert coerce("MCP_SERVERS", "{not json", "str")[1]
    assert coerce("MCP_SERVERS", '{"name":"a"}', "str")[1]
    # A server without a url would be silently skipped at load time; the panel
    # must say so instead of saving a value that does nothing.
    assert coerce("MCP_SERVERS", '[{"name":"a"}]', "str")[1]


@pytest.mark.unit
def test_the_depth_keys_accept_only_their_profiles():
    assert coerce("DEPTH_LEVEL", "MAX", "str") == ("max", None)
    assert coerce("DEPTH_LEVEL", "deep", "str")[1]
    assert coerce("DEPTH_VERIFY", "final", "str") == ("final", None)
    assert coerce("DEPTH_VERIFY", "sometimes", "str")[1]


@pytest.mark.unit
def test_the_test_button_lists_the_tools(app):
    with (
        app.test_request_context("/admin/api/mcp/test", method="POST", json={"name": "rooms", "url": "http://x/mcp"}),
        patch("app.mcp_client.McpClient.list_tools", return_value=[{"name": "list_rooms"}, {"name": "health"}]),
    ):
        session["is_admin"] = True
        payload = mcp_test_view.__wrapped__().get_json()
    assert payload["tools"] == ["list_rooms", "health"]


@pytest.mark.unit
def test_an_unreachable_server_reports_generically(app):
    with (
        app.test_request_context("/admin/api/mcp/test", method="POST", json={"name": "rooms", "url": "http://10.0.0.5/mcp"}),
        patch("app.mcp_client.McpClient.list_tools", side_effect=RuntimeError("connect http://10.0.0.5 failed")),
    ):
        session["is_admin"] = True
        response = mcp_test_view.__wrapped__()
    body = response[0].get_json() if isinstance(response, tuple) else response.get_json()
    assert "10.0.0.5" not in body["error"]
    assert body["error"].startswith("⚠️ ")


@pytest.mark.unit
def test_a_server_without_a_name_or_url_is_refused(app):
    with app.test_request_context("/admin/api/mcp/test", method="POST", json={"name": "rooms"}):
        session["is_admin"] = True
        response = mcp_test_view.__wrapped__()
    status = response[1] if isinstance(response, tuple) else response.status_code
    assert status == 400


@pytest.mark.unit
def test_the_save_button_is_the_green_primary_button_and_never_disabled():
    """Save names in Personalization is green and always live; this one matches it.

    A disabled primary button renders grey, so the tab looked like it had no
    save action until something was typed.
    """
    from pathlib import Path

    html = Path("app/templates/admin.html").read_text(encoding="utf-8")
    assert '<button id="mcp-save" class="btn-primary">{{ _(\'Save changes\') }}</button>' in html
    assert '<button id="mcp-save" class="btn-primary" disabled>' not in html

    js = Path("app/static/js/admin-mcp.js").read_text(encoding="utf-8")
    assert "markDirty" not in js, "nothing toggles the button any more, so nothing may reference the old toggle"
    assert "saveButton.disabled = true;" in js, "it still locks during its own request"
