# modules/cam_mcp.py
"""Minimal MCP client for the room-snapshot-api MCP server.

The camera service exposes its tools over **streamable HTTP** at
``CAMERA_MCP_URL`` (``http://host:5006/mcp``): ``list_rooms``, ``get_snapshot``,
``room_info`` and ``health``. FLAI does not call a model through it today — the
chat answers camera questions through ``CamModule`` — but the agent layer will
need the camera tool surface, so the connection and the tool list are wired
here and surfaced in the admin panel.

Transport: JSON-RPC 2.0 over POST. The server answers either ``application/json``
or an SSE stream (``text/event-stream``); both are parsed here.
"""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"


class CamMCPClient:
    """Talk to the camera MCP server with the shared API key."""

    def __init__(self, url: str, api_key: str = "", timeout: int = 10) -> None:
        self.url = url.rstrip("/") if url else ""
        self.api_key = api_key
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
        if self.api_key:
            headers["X-API-Key"] = self.api_key
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
        if response.status_code >= 400:
            raise RuntimeError(f"MCP HTTP {response.status_code}: {response.text[:200]}")
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            return _parse_sse(response.text)
        return response.json()

    def initialize(self) -> dict[str, Any]:
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "flai", "version": "1"},
            },
        )
        # The spec expects a notification after initialization; a server that
        # ignores it still answers the following tools/list.
        with contextlib.suppress(Exception):
            # A notification has no response; a server that ignores it still
            # answers the following tools/list.
            self._rpc("notifications/initialized", {})
        return result

    def tools(self) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        self.initialize()
        result = self._rpc("tools/list", {})
        tools = (result.get("result") or {}).get("tools") or []
        return tools if isinstance(tools, list) else []

    def status(self) -> dict[str, Any]:
        """Availability and the exposed tool names (admin panel indicator)."""
        if not self.configured:
            return {"configured": False, "available": False, "tools": []}
        try:
            tools = self.tools()
        except Exception as exc:  # noqa: BLE001 - any transport error means "down"
            logger.debug(f"MCP camera server unavailable: {exc}")
            return {"configured": True, "available": False, "tools": [], "error": str(exc)[:200]}
        return {
            "configured": True,
            "available": True,
            "tools": [str(tool.get("name", "")) for tool in tools if isinstance(tool, dict)],
        }


def _parse_sse(body: str) -> dict[str, Any]:
    """Return the JSON-RPC message carried by an SSE response."""
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        chunk = line[5:].strip()
        if not chunk or chunk == "[DONE]":
            continue
        try:
            payload = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(payload, dict) and "result" in payload:
            return payload
    raise RuntimeError("MCP response carried no JSON-RPC result")
