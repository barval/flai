"""A general MCP client over streamable HTTP.

The transport began as ``modules/cam_mcp.py``, built for the camera server and
never used to call a tool. It is general enough for any server, so it lives here
and the camera module subclasses it.

Two things the spec insists on and this module enforces:

* a tool-level failure (``isError: true``) is a message the model can act on, not
  an exception that fails the turn;
* a tool name is namespaced with its server, so one server can never address
  another's tool.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from typing import Any

import requests

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"

NAMESPACE_PREFIX = "mcp__"
NAMESPACE_SEPARATOR = "__"
_UNSAFE = re.compile(r"[^A-Za-z0-9_]")


def namespace(server: str, tool: str) -> str:
    """The single name a tool is advertised and called under."""
    return f"{NAMESPACE_PREFIX}{_UNSAFE.sub('_', server)}{NAMESPACE_SEPARATOR}{_UNSAFE.sub('_', tool)}"


def split_namespaced(name: str) -> tuple[str, str] | None:
    """Undo :func:`namespace`, or None for a name that is not one of ours.

    Both parts come back sanitised — the mapping is lossy by design, and the
    registry keys servers by the sanitised form so a lookup always finds the
    server that produced the name. A tool name that itself contains the
    separator still splits on the first one, so it cannot address another
    server; a *server* name that sanitises to something containing the separator
    is rejected when the configuration is loaded.
    """
    if not name.startswith(NAMESPACE_PREFIX):
        return None
    rest = name[len(NAMESPACE_PREFIX) :]
    if NAMESPACE_SEPARATOR not in rest:
        return None
    server, tool = rest.split(NAMESPACE_SEPARATOR, 1)
    if not server or not tool:
        return None
    return server, tool


class McpClient:
    """JSON-RPC 2.0 over POST, with an SSE answer accepted as well."""

    def __init__(self, url: str, token: str = "", timeout: int = 10) -> None:
        self.url = url.rstrip("/") if url else ""
        self.token = token
        self.timeout = timeout
        self._session_id: str | None = None
        self._next_id = 0

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            payload["params"] = params
        response = requests.post(self.url, json=payload, headers=self._headers(), timeout=self.timeout)
        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            self._session_id = session_id
        # JSON is UTF-8 by definition, but a server that omits the charset in its
        # content-type makes requests decode the body as latin-1: a Cyrillic
        # payload then contains characters like U+0085, which str.splitlines()
        # treats as a line break — it cut the JSON in half and the call failed
        # with "no JSON-RPC result". The bytes are decoded here instead.
        body = response.content.decode("utf-8", errors="replace")
        if response.status_code >= 400:
            raise RuntimeError(f"MCP HTTP {response.status_code}: {body[:200]}")
        if "text/event-stream" in response.headers.get("content-type", ""):
            return _parse_sse(body)
        parsed = json.loads(body)
        return parsed if isinstance(parsed, dict) else {}

    def initialize(self) -> dict[str, Any]:
        """Handshake once per client instance."""
        if not self.configured:
            return {}
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "flai", "version": "1"},
            },
        )
        # A notification has no response; a server that ignores it still answers
        # the following tools/list.
        with contextlib.suppress(Exception):
            self._rpc("notifications/initialized", {})
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        """The server's tools, or an empty list when it is unreachable."""
        if not self.configured:
            return []
        self.initialize()
        result = self._rpc("tools/list", {})
        tools = (result.get("result") or {}).get("tools") or []
        return tools if isinstance(tools, list) else []

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        """Call one tool and flatten its content blocks.

        Raises only on a transport failure: a tool-level error is returned as
        text, because that is what the model needs to see and act on.
        """
        self.initialize()
        result = (self._rpc("tools/call", {"name": name, "arguments": arguments or {}}) or {}).get("result") or {}
        texts = [
            str(block.get("text", ""))
            for block in (result.get("content") or [])
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        body = "\n".join(text for text in texts if text)
        if result.get("isError"):
            return body or "The MCP tool reported an error."
        return body

    def close(self) -> None:
        self._session_id = None


def _parse_sse(body: str) -> dict[str, Any]:
    """Return the JSON-RPC message carried by an SSE response."""
    # Split on real line breaks only: str.splitlines() also breaks on U+0085,
    # U+2028 and the C1 controls, which can legitimately appear inside a JSON
    # string and would cut the payload in half.
    for line in body.split("\n"):
        if not line.startswith("data:"):
            continue
        chunk = line[5:].strip("\r").strip()
        if not chunk or chunk == "[DONE]":
            continue
        try:
            payload = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(payload, dict) and "result" in payload:
            return payload
    raise RuntimeError("MCP response carried no JSON-RPC result")
