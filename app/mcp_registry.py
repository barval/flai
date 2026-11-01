"""The operator's MCP servers: who is configured, and what may be called.

Two rules shape this module:

* nothing is advertised unless the operator configured it, and nothing is called
  unless it was advertised — a name the model invents is refused, never guessed;
* one unreachable server must cost a chat turn nothing, so enumeration is
  cached and a failure contributes an empty list.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.mcp_client import NAMESPACE_SEPARATOR, McpClient, namespace

logger = logging.getLogger(__name__)

DEFAULT_TOOLS_TTL_S = 300
_UNSAFE = re.compile(r"[^A-Za-z0-9_]")
_EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


@dataclass(frozen=True)
class McpServer:
    """One configured server. ``allow`` empty means every tool it lists.

    ``user_scoped`` marks a server whose answers depend on *who* is asking.
    MCP carries no user identity, so such a server cannot be offered to chat:
    FLAI's own permission checks would be bypassed. The flag keeps the entry in
    the configuration (the endpoint stays documented) while contributing no
    tools to the model.
    """

    name: str
    url: str
    token: str = ""
    allow: tuple[str, ...] = field(default_factory=tuple)
    user_scoped: bool = False


def load_servers(raw: str) -> list[McpServer]:
    """Parse the ``MCP_SERVERS`` value.

    A malformed value yields no servers: a configuration typo must leave the
    assistant exactly as it was, not half-configured.
    """
    if not raw or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.warning("MCP_SERVERS is not valid JSON; no MCP tools are exposed")
        return []
    if not isinstance(parsed, list):
        logger.warning("MCP_SERVERS must be a JSON array; no MCP tools are exposed")
        return []
    servers: list[McpServer] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        url = str(entry.get("url") or "").strip()
        if not name or not url:
            logger.warning("MCP server entry without a name or a url is ignored")
            continue
        if NAMESPACE_SEPARATOR in _UNSAFE.sub("_", name):
            # The sanitised name would split into a different server and tool.
            logger.warning(f"MCP server name {name!r} collides with the namespace separator; ignored")
            continue
        allow = entry.get("allow") or []
        servers.append(
            McpServer(
                name=name,
                url=url,
                token=str(entry.get("token") or ""),
                allow=tuple(str(item) for item in allow) if isinstance(allow, list) else (),
                user_scoped=bool(entry.get("user_scoped")),
            )
        )
    return servers


# The last enumeration and when it was taken. A chat turn must not re-list every
# configured server on every message.
_cache: dict[str, Any] = {"at": 0.0, "definitions": [], "resolved": {}}


def reset_cache() -> None:
    """Drop the cached enumeration (tests, and after a configuration change)."""
    _cache.update({"at": 0.0, "definitions": [], "resolved": {}})


def _client_for(server: McpServer) -> McpClient:
    return McpClient(server.url, token=server.token)


def _parameters(schema: Any) -> dict[str, Any]:
    """A JSON Schema llama-server will accept.

    A tool whose ``inputSchema`` is missing, is not an object, or is not an
    object schema is still advertised: the model can decide whether to call it,
    and a request that needs no arguments is common.
    """
    if not isinstance(schema, dict) or schema.get("type") != "object":
        if isinstance(schema, dict) and schema.get("properties"):
            return {"type": "object", "properties": schema["properties"]}
        return dict(_EMPTY_SCHEMA)
    return schema


def tool_definitions(
    servers: list[McpServer] | None = None,
    client_factory: Callable[[McpServer], Any] | None = None,
    ttl_s: int = DEFAULT_TOOLS_TTL_S,
) -> list[dict[str, Any]]:
    """Every advertised MCP tool, in the OpenAI shape, cached for ``ttl_s``.

    Never raises: a server that is down contributes nothing, because losing an
    external tool must not cost the user their answer.
    """
    if servers is None:
        servers = load_servers(_configured_servers_raw())
    if not servers:
        return []
    now = time.monotonic()
    if _cache["definitions"] and now - _cache["at"] < ttl_s:
        return list(_cache["definitions"])

    factory = client_factory or _client_for
    definitions: list[dict[str, Any]] = []
    resolved: dict[str, tuple[McpServer, str]] = {}
    for server in servers:
        if server.user_scoped:
            # Its answers depend on the caller, and MCP has no caller identity.
            logger.info(f"MCP server {server.name!r} is user_scoped: its tools are not offered to chat")
            continue
        try:
            tools = factory(server).list_tools()
        except Exception as exc:  # noqa: BLE001 - a down server is not an error
            logger.warning(f"MCP server {server.name!r} is unavailable: {exc}")
            continue
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            tool_name = str(tool.get("name") or "").strip()
            if not tool_name or (server.allow and tool_name not in server.allow):
                continue
            full_name = namespace(server.name, tool_name)
            if full_name in resolved:
                # Two servers sanitising to the same name: the first wins and the
                # second is dropped rather than shadowing it silently.
                logger.warning(f"MCP tool name collision on {full_name!r}; {server.name!r} is dropped")
                continue
            definitions.append(
                {
                    "type": "function",
                    "function": {
                        "name": full_name,
                        "description": str(tool.get("description") or server.name),
                        "parameters": _parameters(tool.get("inputSchema")),
                    },
                }
            )
            resolved[full_name] = (server, tool_name)

    _cache.update({"at": now, "definitions": definitions, "resolved": resolved})
    return list(definitions)


def resolve(name: str) -> tuple[McpServer, str] | None:
    """The server and tool behind an advertised name, or None."""
    return _cache["resolved"].get(name)


def call(name: str, arguments: dict[str, Any] | None = None) -> str:
    """Call an advertised MCP tool.

    Raises ``ValueError`` for a name that was never advertised; a transport
    failure propagates for the caller to turn into a localized message.
    """
    resolved = resolve(name)
    if resolved is None:
        raise ValueError(f"unknown MCP tool: {name}")
    server, tool_name = resolved
    return _client_for(server).call_tool(tool_name, arguments or {})


def _configured_servers_raw() -> str:
    """The raw configuration, or an empty string outside an app context."""
    try:
        from flask import current_app

        return str(current_app.config.get("MCP_SERVERS", "") or "")
    except Exception:  # noqa: BLE001 - no app context means not configured
        return ""
