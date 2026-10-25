"""Snapshot route table and the inline asset map used by the offline demo."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from demo.io import DEMO_DIR

MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".ogg": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".pdf": "application/pdf",
}


def _split(path: str) -> tuple[str, str]:
    parts = urlsplit(path)
    return parts.path, parts.query


def find_route(snapshot: dict[str, Any], method: str, path: str) -> dict[str, Any] | None:
    """Return the recorded route for a method and a path; an exact query wins over a bare path."""
    pathname, query = _split(path)
    fallback = None
    for route in snapshot.get("routes", []):
        route_path, route_query = _split(str(route["path"]))
        if (str(route.get("method", "GET")).upper(), route_path) != (method.upper(), pathname):
            continue
        if query and route_query == query:
            return route
        if fallback is None:
            fallback = route
    return fallback


def route_body(snapshot: dict[str, Any], method: str, path: str) -> Any:
    """Return the recorded response body, failing loudly when the route was never captured."""
    route = find_route(snapshot, method, path)
    if route is None:
        raise KeyError(f"No route {method} {path} in the demo snapshot")
    return route.get("body")


def compile_routes(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn the recorded routes into the compact table the offline shim embeds."""
    return [
        {
            "m": str(route.get("method", "GET")).upper(),
            "p": _split(str(route["path"]))[0],
            "q": _split(str(route["path"]))[1],
            "s": int(route.get("status", 200)),
            "b": route.get("body"),
        }
        for route in snapshot.get("routes", [])
    ]


def compile_assets(snapshot: dict[str, Any], demo_dir: Path = DEMO_DIR) -> dict[str, str]:
    """Read every committed asset and turn it into a data: URL keyed by its request path."""
    assets: dict[str, str] = {}
    for url, relative in sorted(snapshot.get("assets", {}).items()):
        path = demo_dir / relative
        suffix = path.suffix.lower()
        if suffix not in MIME_BY_SUFFIX:
            raise ValueError(f"Unsupported demo asset type: {path}")
        if not path.is_file():
            raise FileNotFoundError(f"Missing demo asset: {path}")
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
        assets[url] = f"data:{MIME_BY_SUFFIX[suffix]};base64,{payload}"
    return assets
