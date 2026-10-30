# modules/cam_mcp.py
"""The camera MCP server's client.

The camera service exposes its tools over **streamable HTTP** at
``CAMERA_MCP_URL`` (``http://host:5006/mcp``): ``list_rooms``, ``get_snapshot``,
``room_info`` and ``health``. FLAI does not call a model through it — the chat
answers camera questions through ``CamModule`` — but the connection and the tool
list are surfaced in the admin panel.

The transport lives in :mod:`app.mcp_client`; this module only adds the camera
server's shared-key header and its status shape. Its tools are therefore also
callable through the generic MCP registry when an operator configures the server
there.
"""

from __future__ import annotations

import logging
from typing import Any

from app.mcp_client import McpClient

logger = logging.getLogger(__name__)


class CamMCPClient(McpClient):
    """Talk to the camera MCP server with the shared API key."""

    def __init__(self, url: str, api_key: str = "", timeout: int = 10) -> None:
        super().__init__(url, token=api_key, timeout=timeout)
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        # The camera server authenticates with its own shared key rather than a
        # bearer token, so the token is carried in the header it expects.
        headers.pop("Authorization", None)
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def tools(self) -> list[dict[str, Any]]:
        return self.list_tools()

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
