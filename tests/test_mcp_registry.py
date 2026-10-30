"""The operator's MCP servers, and the definitions the model is allowed to see."""

from __future__ import annotations

import json

import pytest

from app.mcp_registry import (
    McpServer,
    call,
    load_servers,
    reset_cache,
    resolve,
    tool_definitions,
)


class _FakeClient:
    def __init__(self, tools, error=None, answer="ok"):
        self._tools = tools
        self._error = error
        self._answer = answer
        self.called: list[tuple[str, dict]] = []

    def list_tools(self):
        if self._error:
            raise RuntimeError(self._error)
        return self._tools

    def call_tool(self, name, arguments=None):
        self.called.append((name, arguments or {}))
        return self._answer


def _factory(tools, error=None, answer="ok"):
    client = _FakeClient(tools, error, answer)
    return lambda server: client, client


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_cache()
    yield
    reset_cache()


@pytest.mark.unit
def test_no_configuration_means_no_servers():
    assert load_servers("") == []
    assert load_servers("[]") == []


@pytest.mark.unit
def test_a_malformed_value_yields_nothing_instead_of_raising():
    assert load_servers("{not json") == []
    assert load_servers('{"name": "x"}') == []


@pytest.mark.unit
def test_a_server_without_a_url_is_skipped():
    raw = json.dumps([{"name": "a", "url": "http://a/mcp"}, {"name": "b"}])
    assert [server.name for server in load_servers(raw)] == ["a"]


@pytest.mark.unit
def test_a_name_that_sanitises_to_the_separator_is_rejected():
    # "a__b" would split into a different server and tool than it was built from.
    raw = json.dumps([{"name": "a__b", "url": "http://a/mcp"}])
    assert load_servers(raw) == []


@pytest.mark.unit
def test_no_configuration_produces_no_definitions_and_no_client():
    built: list[str] = []

    def factory(server):
        built.append(server.name)
        return _FakeClient([])

    assert tool_definitions(servers=[], client_factory=factory) == []
    assert built == []


@pytest.mark.unit
def test_a_tool_becomes_a_namespaced_openai_definition():
    server = McpServer(name="rooms", url="http://rooms/mcp", token="", allow=())
    factory, _ = _factory([{"name": "get_snapshot", "description": "take one", "inputSchema": {"type": "object"}}])
    definitions = tool_definitions(servers=[server], client_factory=factory)
    assert definitions == [
        {
            "type": "function",
            "function": {
                "name": "mcp__rooms__get_snapshot",
                "description": "take one",
                "parameters": {"type": "object"},
            },
        }
    ]


@pytest.mark.unit
def test_a_tool_without_a_schema_still_gets_an_object_schema():
    # llama-server rejects a tool whose parameters are not an object schema.
    server = McpServer(name="rooms", url="http://rooms/mcp", token="", allow=())
    factory, _ = _factory([{"name": "ping"}])
    parameters = tool_definitions(servers=[server], client_factory=factory)[0]["function"]["parameters"]
    assert parameters["type"] == "object"


@pytest.mark.unit
def test_a_non_object_schema_is_replaced():
    server = McpServer(name="rooms", url="http://rooms/mcp", token="", allow=())
    factory, _ = _factory([{"name": "ping", "inputSchema": {"type": "string"}}])
    parameters = tool_definitions(servers=[server], client_factory=factory)[0]["function"]["parameters"]
    assert parameters == {"type": "object", "properties": {}}


@pytest.mark.unit
def test_a_server_that_is_down_contributes_nothing():
    server = McpServer(name="rooms", url="http://rooms/mcp", token="", allow=())
    factory, _ = _factory([], error="connection refused")
    assert tool_definitions(servers=[server], client_factory=factory) == []


@pytest.mark.unit
def test_two_servers_exposing_the_same_tool_do_not_collide():
    one = McpServer(name="a", url="http://a/mcp", token="", allow=())
    two = McpServer(name="b", url="http://b/mcp", token="", allow=())

    def factory(server):
        return _FakeClient([{"name": "ping"}])

    definitions = tool_definitions(servers=[one, two], client_factory=factory)
    assert [item["function"]["name"] for item in definitions] == ["mcp__a__ping", "mcp__b__ping"]


@pytest.mark.unit
def test_an_allow_list_limits_what_is_advertised():
    server = McpServer(name="rooms", url="http://rooms/mcp", token="", allow=("get_snapshot",))
    factory, _ = _factory([{"name": "get_snapshot"}, {"name": "delete_everything"}])
    names = [item["function"]["name"] for item in tool_definitions(servers=[server], client_factory=factory)]
    assert names == ["mcp__rooms__get_snapshot"]


@pytest.mark.unit
def test_resolve_only_accepts_a_name_that_was_advertised():
    server = McpServer(name="rooms", url="http://rooms/mcp", token="", allow=())
    factory, _ = _factory([{"name": "ping"}])
    tool_definitions(servers=[server], client_factory=factory)
    assert resolve("mcp__rooms__ping") == (server, "ping")
    # Not advertised: never guessed.
    assert resolve("mcp__rooms__delete_everything") is None
    assert resolve("mcp__other__ping") is None
    assert resolve("calculator") is None


@pytest.mark.unit
def test_call_uses_the_resolved_tool(monkeypatch):
    server = McpServer(name="rooms", url="http://rooms/mcp", token="", allow=())
    factory, client = _factory([{"name": "ping"}], answer="pong")
    tool_definitions(servers=[server], client_factory=factory)
    monkeypatch.setattr("app.mcp_registry._client_for", lambda srv: client)
    assert call("mcp__rooms__ping", {"x": 1}) == "pong"
    assert client.called == [("ping", {"x": 1})]


@pytest.mark.unit
def test_call_refuses_a_name_that_is_not_advertised():
    with pytest.raises(ValueError):
        call("mcp__nope__ping", {})
