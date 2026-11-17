"""The diagnostics container's only interface.

Every tool the chat model can reach is a typed field with a bounded value, and
every command is built as a **list** for ``subprocess`` — no caller string is ever
interpolated into a shell. The container is deliberately dumb: it validates,
runs one program, and returns text. Policy that needs a user identity lives in the
application, not here.

Failures come back as text beginning with ``⚠️`` rather than as exceptions, because
the caller's only use for them is to show the sentence to the model.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import re
import socket
import subprocess
from urllib.parse import urlsplit

from flask import Flask, jsonify, request

APP_TOKEN = os.getenv("NETDIAG_API_TOKEN", "")
MAX_OUTPUT_CHARS = 20_000
MAX_HOST_CHARS = 253

# Always refused, whatever the permission says: the container's own loopback and the
# link-local range, which is where cloud metadata endpoints live.
BLOCKED_NETS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
)

_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_DNS_TYPES = {"A", "AAAA", "MX", "TXT", "NS", "SOA", "CNAME", "PTR"}

DEFAULT_TIMEOUT = {
    "dns": 10.0,
    "whois": 15.0,
    "ping": 15.0,
    # Never longer than the caller waits: the application gives up first, and a
    # container that kept working would answer into a closed request.
    "trace": 20.0,
    "ports": 30.0,
    "http": 20.0,
    "speed": 20.0,
}


class ToolError(ValueError):
    """A value the caller sent that this container will not run."""


def _blocked(ip: ipaddress._BaseAddress) -> bool:
    return any(ip in net for net in BLOCKED_NETS)


def is_blocked_target(target: str) -> str | None:
    """``None`` when the target may be reached, otherwise the reason it may not.

    A name that does not resolve is not blocked: the tool reports the failure itself
    and the message is more useful than a refusal.
    """
    text = str(target or "").strip().strip("[]")
    if not text:
        return "target is empty"
    with_ip = text.split("/", 1)[0]
    try:
        address = ipaddress.ip_address(with_ip)
    except ValueError:
        address = None
    if address is not None:
        return "target is loopback or link-local" if _blocked(address) else None
    if "/" in text:
        try:
            network = ipaddress.ip_network(text, strict=False)
        except ValueError:
            return "not an address"
        for net in BLOCKED_NETS:
            if network.overlaps(net):
                return "target is loopback or link-local"
        return None
    try:
        infos = socket.getaddrinfo(text, None)
    except (socket.gaierror, UnicodeError):
        return None
    except OSError:
        return None
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        except ValueError:
            continue
        if _blocked(address):
            return "target is loopback or link-local"
    return None


def _text(value: object) -> str:
    return str(value if value is not None else "").strip()


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text.strip("[]"))
    except ValueError:
        return False
    return True


def _host(payload: dict, key: str = "host") -> str:
    text = _text(payload.get(key))
    if not text or len(text) > MAX_HOST_CHARS:
        raise ToolError("the target is not a host name or an address")
    if not _HOST_RE.match(text) and not _is_ip(text):
        raise ToolError("the target is not a host name or an address")
    return text.strip("[]") if text.startswith("[") else text


def _network(payload: dict) -> str:
    text = _text(payload.get("host"))
    if not text or len(text) > MAX_HOST_CHARS:
        raise ToolError("the target is not a host name or an address")
    try:
        network = ipaddress.ip_network(text, strict=False)
    except ValueError as exc:
        raise ToolError("the target is not a network") from exc
    if network.prefixlen < 24:
        raise ToolError("scan at most 256 addresses (/24)")
    return str(network)


def _is_network(text: str) -> bool:
    if "/" not in text:
        return False
    try:
        ipaddress.ip_network(text, strict=False)
    except ValueError:
        return False
    return True


def _int(payload: dict, key: str, low: int, high: int, default: int) -> int:
    raw = payload.get(key, default)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ToolError(f"{key} must be a number") from exc
    if value < low or value > high:
        raise ToolError(f"{key} must be between {low} and {high}")
    return value


def _timeout(tool: str, payload: dict) -> float:
    raw = payload.get("timeout")
    limit = DEFAULT_TIMEOUT.get(tool, 20.0)
    if raw in (None, ""):
        return limit
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ToolError("timeout must be a number") from exc
    return max(1.0, min(value, limit))


def _guarded(target: str) -> None:
    reason = is_blocked_target(target)
    if reason:
        raise ToolError(reason)


# ── the seven tools ───────────────────────────────────────────────────────────


def _dns(payload: dict) -> list[str]:
    name = _host(payload, "name")
    _guarded(name)
    record = _text(payload.get("type") or "A").upper()
    if record not in _DNS_TYPES:
        raise ToolError("unsupported record type")
    command = ["dig", "+time=3", "+tries=1", name, record]
    server = _text(payload.get("server"))
    if server:
        if not _HOST_RE.match(server):
            raise ToolError("the resolver is not an address")
        command.insert(1, f"@{server}")
    return command


def _whois(payload: dict) -> list[str]:
    target = _text(payload.get("target"))
    if not target or len(target) > MAX_HOST_CHARS:
        raise ToolError("the target is empty")
    if not (_HOST_RE.match(target) or _is_network(target)):
        raise ToolError("the target is not a domain or an address")
    _guarded(target)
    return ["whois", target]


def _ping(payload: dict) -> list[str]:
    host = _host(payload)
    _guarded(host)
    return ["ping", "-c", str(_int(payload, "count", 1, 5, 3)), "-W", "2", host]


def _trace(payload: dict) -> list[str]:
    host = _host(payload)
    _guarded(host)
    return ["mtr", "-r", "-c", "3", "-m", str(_int(payload, "hops", 1, 15, 15)), host]


def _ports(payload: dict) -> list[str]:
    raw = _text(payload.get("host"))
    if _is_network(raw):
        # A /24 with ports is minutes of work; discovery answers "who is there" and
        # a port scan stays one host.
        network = _network(payload)
        _guarded(network)
        return ["nmap", "-sn", "-T4", network]
    host = _host(payload)
    _guarded(host)
    command = ["nmap", "-sT", "-T4", "--top-ports", str(_int(payload, "ports", 1, 1000, 100))]
    if payload.get("service_version"):
        command.append("-sV")
    command.append(host)
    return command


def _http(payload: dict) -> list[str]:
    url = _text(payload.get("url"))
    if not url.lower().startswith(("http://", "https://")):
        raise ToolError("the url must be http or https")
    try:
        host = (urlsplit(url).hostname or "").strip("[]")
    except ValueError as exc:
        raise ToolError("the url is not valid") from exc
    if not host:
        raise ToolError("the url has no host")
    _guarded(host)
    return ["curl", "-sS", "-I", "--max-time", "10", url]


def _speed(payload: dict) -> list[str]:
    host = _host(payload)
    _guarded(host)
    return [
        "iperf3",
        "-c",
        host,
        "-p",
        str(_int(payload, "port", 1, 65535, 5201)),
        "-t",
        str(_int(payload, "seconds", 1, 10, 5)),
    ]


_HANDLERS = {
    "dns": _dns,
    "whois": _whois,
    "ping": _ping,
    "trace": _trace,
    "ports": _ports,
    "http": _http,
    "speed": _speed,
}


def execute(tool: str, payload: dict) -> tuple[bool, str]:
    """Run one tool. Returns (succeeded, text); text is what the model would read."""
    handler = _HANDLERS.get(tool)
    if handler is None:
        return False, "⚠️ unknown tool"
    try:
        command = handler(payload or {})
        timeout = _timeout(tool, payload or {})
    except ToolError as exc:
        return False, f"⚠️ {exc}"
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"⚠️ the tool did not answer within {timeout:.0f} s"
    except FileNotFoundError:
        return False, "⚠️ the program is not installed in this container"
    output = (completed.stdout or "") + (completed.stderr or "")
    output = output.strip()[:MAX_OUTPUT_CHARS]
    if not output:
        return True, f"(no output, exit code {completed.returncode})"
    return True, output


def run(tool: str, payload: dict) -> str:
    """The tool's text, with a refusal written into it rather than raised."""
    _ok, text = execute(tool, payload)
    return text


# ── HTTP ──────────────────────────────────────────────────────────────────────

app = Flask(__name__)


@app.before_request
def _require_token():
    if not APP_TOKEN:
        # Fail closed: a container without a token is a misconfiguration, not an
        # open relay for whoever shares its network.
        return jsonify({"ok": False, "error": "token is not configured"}), 503
    header = request.headers.get("Authorization", "")
    if not hmac.compare_digest(header, f"Bearer {APP_TOKEN}"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return None


@app.get("/health")
def health():
    return jsonify({"ok": True, "tools": sorted(_HANDLERS)})


@app.post("/<tool>")
def call_tool(tool: str):
    payload = request.get_json(silent=True) or {}
    ok, text = execute(tool, payload)
    if tool not in _HANDLERS:
        return jsonify({"ok": False, "error": text}), 404
    return jsonify({"ok": ok, "output": text})
