"""The diagnostics sidecar's client: availability, ceilings and one call shape.

Two rules live here rather than in the tool executors. The health answer is cached
for half a minute, because one chat message builds the tool catalogue three times
and an uncached probe would let a hung container cost seconds on every turn. And
the per-tool ceiling is a table, so the caller cannot ask for a scan that outlives
the chat turn it runs inside.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests
from flask import current_app, has_app_context

logger = logging.getLogger(__name__)

AVAILABILITY_TTL_S = 30
HEALTH_TIMEOUT_S = 1.0

TOOL_TIMEOUTS: dict[str, float] = {
    "dns": 10.0,
    "ping": 15.0,
    "whois": 15.0,
    "http": 20.0,
    "speed": 20.0,
    "trace": 30.0,
    "ports": 45.0,
}

# (checked_at, verdict)
_available: tuple[float, bool] | None = None


def _setting(key: str, default: Any) -> Any:
    if has_app_context():
        return current_app.config.get(key, default)
    return os.getenv(key, default)


def reset_availability_cache() -> None:
    global _available
    _available = None


def _base_url() -> str:
    return str(_setting("NETDIAG_URL", "http://flai-netdiag:8099")).rstrip("/")


def _auth() -> dict[str, str]:
    token = str(_setting("NETDIAG_API_TOKEN", "") or "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _probe() -> bool:
    try:
        response = requests.get(f"{_base_url()}/health", headers=_auth(), timeout=HEALTH_TIMEOUT_S)
    except requests.RequestException as exc:
        logger.debug(f"netdiag health check failed: {exc}")
        return False
    return response.status_code == 200 and bool((response.json() or {}).get("ok"))


def available(ttl_s: int = AVAILABILITY_TTL_S) -> bool:
    """Whether the sidecar is answering — and whether the operator allows it at all."""
    global _available
    if not _setting("NET_TOOLS_ENABLED", False):
        return False
    now = time.monotonic()
    if _available is not None and (now - _available[0]) < ttl_s:
        return _available[1]
    verdict = _probe()
    _available = (now, verdict)
    return verdict


def call(tool: str, payload: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
    """POST one typed request. Raises ``requests.RequestException`` on transport failure."""
    limit = timeout if timeout is not None else TOOL_TIMEOUTS.get(tool, 20.0)
    response = requests.post(
        f"{_base_url()}/{tool}",
        json=payload,
        headers=_auth(),
        timeout=limit,
    )
    return response.json() or {}


def output_of(answer: dict[str, Any]) -> str:
    """The text a tool result should carry, whatever shape the sidecar answered in."""
    text = str(answer.get("output") or answer.get("error") or "").strip()
    if text:
        return text
    return "⚠️ the diagnostics service returned nothing"
