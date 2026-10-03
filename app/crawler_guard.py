"""SSRF guard for user- and model-supplied URLs sent to the crawler.

The web app validates every URL before it reaches the Crawl4AI container:
only globally routable http(s) targets are allowed. This keeps a pasted or
model-generated link from probing the Docker network (Redis, Postgres, Qdrant,
llama-swap) or the cloud metadata endpoint.
"""

import ipaddress
import socket
from urllib.parse import urlsplit

ALLOWED_SCHEMES = ("http", "https")


class BlockedUrlError(ValueError):
    """Raised when a URL may not be handed to the crawler."""


def _is_public_ip(ip_text: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    if addr.is_multicast or addr.is_reserved or addr.is_unspecified:
        return False
    return addr.is_global


def validate_url(url: str) -> str:
    """Return the URL unchanged when it is safe to crawl; raise otherwise."""
    candidate = (url or "").strip()
    try:
        parts = urlsplit(candidate)
    except ValueError as exc:
        raise BlockedUrlError("Unparsable URL") from exc
    if parts.scheme.lower() not in ALLOWED_SCHEMES or not parts.hostname:
        raise BlockedUrlError("Only http(s) URLs are allowed")
    if parts.username is not None or parts.password is not None:
        raise BlockedUrlError("Credentials in URLs are not allowed")
    try:
        infos = socket.getaddrinfo(
            parts.hostname,
            parts.port or (443 if parts.scheme.lower() == "https" else 80),
            proto=socket.IPPROTO_TCP,
        )
    except (socket.gaierror, OSError) as exc:
        raise BlockedUrlError("URL could not be resolved") from exc
    if not infos:
        raise BlockedUrlError("URL did not resolve to any address")
    for info in infos:
        ip = info[4][0]
        if not _is_public_ip(ip):
            raise BlockedUrlError("URL resolves to a non-public address")
    return candidate
