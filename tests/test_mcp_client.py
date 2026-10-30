"""The general MCP client: the transport FLAI's camera module already proved."""

from __future__ import annotations

import json

import pytest

from app.mcp_client import McpClient, namespace, split_namespaced


class _Response:
    def __init__(self, payload, status=200, content_type="application/json"):
        self._payload = payload
        self.status_code = status
        self.text = payload if isinstance(payload, str) else json.dumps(payload)
        # A real requests.Response always carries the raw bytes.
        self.content = self.text.encode("utf-8")
        self.headers = {"content-type": content_type}

    def json(self):
        return self._payload


def _client(monkeypatch, handler):
    calls: list[dict] = []

    def fake_post(url, json=None, headers=None, timeout=None, **kwargs):
        calls.append({"url": url, "json": json, "headers": headers or {}})
        return handler(json)

    monkeypatch.setattr("app.mcp_client.requests.post", fake_post)
    return McpClient("http://mcp.test/mcp", token="t0ken", timeout=5), calls


@pytest.mark.unit
def test_a_configuration_without_a_url_is_not_configured():
    assert McpClient("").configured is False
    assert McpClient("http://x/mcp").configured is True


@pytest.mark.unit
def test_list_tools_returns_the_tools_array(monkeypatch):
    def handler(payload):
        if payload["method"] == "tools/list":
            return _Response({"jsonrpc": "2.0", "id": payload["id"], "result": {"tools": [{"name": "a"}]}})
        return _Response({"jsonrpc": "2.0", "id": payload["id"], "result": {}})

    client, _ = _client(monkeypatch, handler)
    assert client.list_tools() == [{"name": "a"}]


@pytest.mark.unit
def test_the_token_goes_out_as_a_bearer_header(monkeypatch):
    client, calls = _client(monkeypatch, lambda p: _Response({"result": {"tools": []}}))
    client.list_tools()
    assert calls[0]["headers"]["Authorization"] == "Bearer t0ken"


@pytest.mark.unit
def test_call_tool_sends_the_name_and_arguments(monkeypatch):
    def handler(payload):
        if payload["method"] == "tools/call":
            return _Response({"result": {"content": [{"type": "text", "text": "42"}]}})
        return _Response({"result": {}})

    client, calls = _client(monkeypatch, handler)
    assert client.call_tool("add", {"a": 1}) == "42"
    call = [c for c in calls if c["json"]["method"] == "tools/call"][0]
    assert call["json"]["params"] == {"name": "add", "arguments": {"a": 1}}


@pytest.mark.unit
def test_several_content_blocks_are_joined(monkeypatch):
    def handler(payload):
        if payload["method"] == "tools/call":
            return _Response({"result": {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}})
        return _Response({"result": {}})

    client, _ = _client(monkeypatch, handler)
    assert client.call_tool("t", {}) == "a\nb"


@pytest.mark.unit
def test_a_tool_level_error_is_a_message_not_an_exception(monkeypatch):
    def handler(payload):
        if payload["method"] == "tools/call":
            return _Response({"result": {"isError": True, "content": [{"type": "text", "text": "no such room"}]}})
        return _Response({"result": {}})

    client, _ = _client(monkeypatch, handler)
    assert "no such room" in client.call_tool("t", {})


@pytest.mark.unit
def test_an_http_failure_raises(monkeypatch):
    def handler(payload):
        return _Response("boom", status=500)

    client, _ = _client(monkeypatch, handler)
    with pytest.raises(RuntimeError):
        client.list_tools()


@pytest.mark.unit
def test_an_sse_answer_is_parsed(monkeypatch):
    body = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"tools":[{"name":"z"}]}}\n\n'

    def handler(payload):
        return _Response(body, content_type="text/event-stream")

    client, _ = _client(monkeypatch, handler)
    assert client.list_tools() == [{"name": "z"}]


@pytest.mark.unit
def test_namespacing_round_trips():
    # Sanitising is lossy by design: the registry keys servers by the sanitised
    # form, so looking a name up always finds the same server that produced it.
    name = namespace("room-snapshot", "get_snapshot")
    assert name == "mcp__room_snapshot__get_snapshot"
    assert split_namespaced(name) == ("room_snapshot", "get_snapshot")


@pytest.mark.unit
def test_a_plain_name_is_not_namespaced():
    assert split_namespaced("calculator") is None
    assert split_namespaced("mcp__only") is None

class _Utf8SseResponse:
    """What `requests` gives back when the server omits the charset."""

    def __init__(self, payload):
        raw = ("event: message\r\ndata: " + json.dumps(payload, ensure_ascii=False) + "\r\n\r\n").encode("utf-8")
        self.content = raw
        self.text = raw.decode("latin-1")
        self.status_code = 200
        self.headers = {"content-type": "text/event-stream"}


@pytest.mark.unit
def test_a_cyrillic_sse_payload_survives_a_missing_charset(monkeypatch):
    # Measured against the shipped camera MCP server: without a charset in the
    # content-type, requests decodes the body as latin-1, the Cyrillic bytes turn
    # into characters such as U+0085, and str.splitlines() splits the JSON in the
    # middle. The tool call then failed with "no JSON-RPC result".
    payload = {"jsonrpc": "2.0", "id": 3, "result": {"content": [{"type": "text", "text": "Спальня"}]}}

    def handler(_payload):
        if _payload["method"] == "tools/call":
            return _Utf8SseResponse(payload)
        return _Utf8SseResponse({"jsonrpc": "2.0", "id": _payload["id"], "result": {}})

    client, _ = _client(monkeypatch, handler)
    assert client.call_tool("list_rooms", {}) == "Спальня"


@pytest.mark.unit
def test_sse_parsing_does_not_split_on_unicode_line_boundaries():
    from app.mcp_client import _parse_sse

    # U+0085 is a line boundary to str.splitlines() but not to an SSE frame.
    body = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"tools":[{"name":"a\u0085b"}]}}\n\n'
    assert _parse_sse(body)["result"]["tools"][0]["name"] == "a\u0085b"
