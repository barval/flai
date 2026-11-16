"""The diagnostics sidecar's client: availability, ceilings and one call shape.

Two rules live here rather than in the tool executors. The health answer is cached
for half a minute, because one chat message builds the tool catalogue three times
and an uncached probe would let a hung container cost seconds on every turn. And
the per-tool ceiling is a table, so the caller cannot ask for a scan that outlives
the chat turn it runs inside.
"""

from __future__ import annotations

import json
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


# ── The chat tools' half: permission, limits, audit ───────────────────────────

NET_TOOL_IDS: tuple[str, ...] = (
    "net_dns",
    "net_whois",
    "net_ping",
    "net_trace",
    "net_ports",
    "net_http",
    "net_speed",
)

RATE_PER_MINUTE = 10
RATE_PER_HOUR = 60
AUDIT_KEY = "net:audit"
AUDIT_MAX = 500
AUDIT_TTL_S = 86_400

# Same set the container refuses; repeated here so a blocked target never leaves the
# application at all, whatever the container does.
_BLOCKED = ("127.0.0.0/8", "::1/128", "169.254.0.0/16", "fe80::/10")


_fallback_redis: Any = None


def _redis() -> Any:
    """The application's Redis client; a direct one for callers without an app.

    ``flask net-audit`` runs from a shell where there is no request queue, and a
    read-only report should not need a whole application to exist.
    """
    global _fallback_redis
    try:
        queue = getattr(current_app, "request_queue", None)
    except RuntimeError:
        queue = None
    client = getattr(queue, "redis", None)
    if client is not None:
        return client
    if queue is not None:
        return None
    if _fallback_redis is None:
        import redis as redis_module

        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        _fallback_redis = redis_module.Redis.from_url(url, decode_responses=True, socket_timeout=3)
    return _fallback_redis


def blocked_target(target: str) -> str | None:
    """The reason a literal address is always refused, or None."""
    import ipaddress

    text = str(target or "").strip().strip("[]").split("/", 1)[0]
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    for net in _BLOCKED:
        if address in ipaddress.ip_network(net):
            return "loopback or link-local"
    return None


def user_allowed(login: str) -> bool:
    """Whether an administrator granted this account the network tools."""
    if not login:
        return False
    try:
        from app.userdb import get_user_by_login

        row = get_user_by_login(login)
    except Exception as exc:  # noqa: BLE001 - an unreadable row is not a permission
        logger.warning(f"net tools permission could not be read for {login!r}: {exc}")
        return False
    return bool(row and row.get("net_tools_enabled"))


def check_rate_limit(login: str) -> bool:
    """False when this account has spent its budget. A refusal is not counted."""
    client = _redis()
    if client is None:
        return True
    now = int(time.time())
    minute_key = f"net:rate:{login}:m{now // 60}"
    hour_key = f"net:rate:{login}:h{now // 3600}"
    try:
        used_minute = int(client.get(minute_key) or 0)
        used_hour = int(client.get(hour_key) or 0)
        if used_minute >= RATE_PER_MINUTE or used_hour >= RATE_PER_HOUR:
            return False
        client.incr(minute_key)
        client.expire(minute_key, 120)
        client.incr(hour_key)
        client.expire(hour_key, 7200)
    except Exception as exc:  # noqa: BLE001 - a broken counter must not block the tool
        logger.warning(f"net tools rate limit unavailable: {exc}")
    return True


def record_call(login: str, tool: str, target: str, ok: bool, seconds: float) -> None:
    """One audit line, best effort: a lost Redis loses the day's record, not the call."""
    client = _redis()
    if client is None:
        return
    entry = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "login": login,
        "tool": tool,
        "target": str(target)[:253],
        "ok": bool(ok),
        "seconds": round(float(seconds), 2),
    }
    try:
        client.lpush(AUDIT_KEY, json.dumps(entry, ensure_ascii=False))
        client.ltrim(AUDIT_KEY, 0, AUDIT_MAX - 1)
        client.expire(AUDIT_KEY, AUDIT_TTL_S)
    except Exception as exc:  # noqa: BLE001 - best effort by design
        logger.warning(f"net tools audit write failed: {exc}")


def recent_calls(limit: int = 50) -> list[dict[str, Any]]:
    client = _redis()
    if client is None:
        return []
    try:
        raw = client.lrange(AUDIT_KEY, 0, max(0, limit - 1)) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"net tools audit read failed: {exc}")
        return []
    entries = []
    for item in raw:
        try:
            entries.append(json.loads(item))
        except (TypeError, ValueError):
            continue
    return entries
