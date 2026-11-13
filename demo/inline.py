"""Inline every static asset of a rendered page and scrub server-only references."""

from __future__ import annotations

import base64
import re
from pathlib import Path

from demo.io import ROOT

MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}

LINK_RE = re.compile(r'<link\b[^>]*href="(?P<url>/static/css/[^"?]+)(?:\?[^"]*)?"[^>]*>')
SCRIPT_RE = re.compile(r'<script\b[^>]*src="(?P<url>/static/js/[^"?]+)(?:\?[^"]*)?"[^>]*>\s*</script>')
IMAGE_RE = re.compile(
    r'(?P<attr>src|href)="(?P<url>/static/[^"?]+\.(?:png|ico|jpg|jpeg|webp|gif|svg))(?:\?[^"]*)?"'
)

#: A vendor bundle is loaded lazily by URL from a JS constant, so an attribute
#: pass cannot see it: the demo page asked file:///static/vendor/... and fell back
#: to the 2D renderer every time. Matched by the string constant instead.
VENDOR_REF = re.compile(r"""(['"])/static/vendor/(?P<name>[^'"]+\.js)\1""")

FORBIDDEN = ('="/static/', "/static/vendor/", "<script src=", "@import", "url(http")


class MissingAssetError(RuntimeError):
    """Raised when a page refers to a static file that does not exist."""


class ScrubError(RuntimeError):
    """Raised when a server-only reference survived the inlining pass."""


def _read_asset(root: Path, url: str) -> Path:
    path = root / "app" / url.lstrip("/")
    if not path.is_file():
        raise MissingAssetError(f"Missing static asset: {url}")
    return path


def _style_block(root: Path, url: str) -> str:
    content = _read_asset(root, url).read_text(encoding="utf-8")
    return f"<style>{content}</style>"


def _script_block(root: Path, url: str) -> str:
    content = _read_asset(root, url).read_text(encoding="utf-8")
    return "<script>" + content.replace("</script", "<\\/script") + "</script>"


def _data_url(root: Path, url: str) -> str:
    path = _read_asset(root, url)
    suffix = path.suffix.lower()
    if suffix not in MIME_BY_SUFFIX:
        raise MissingAssetError(f"Unsupported static asset type: {url}")
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{MIME_BY_SUFFIX[suffix]};base64,{payload}"


def _inline_vendor(html: str, root: Path) -> str:
    """Preload every lazily-fetched vendor bundle and drop its URL.

    The bundle is a library, never data: it is injected as a plain script, and the
    constant that pointed at the server becomes empty. The graph then finds the
    global instead of a URL.
    """
    urls: list[str] = []
    for match in VENDOR_REF.finditer(html):
        url = "/static/vendor/" + match.group("name")
        if url not in urls:
            urls.append(url)
    if not urls:
        return html
    blocks = "".join(_script_block(root, url) for url in urls)
    html = VENDOR_REF.sub("''", html)
    return html.replace("</body>", blocks + "</body>", 1)


def inline_page(html: str, root: Path = ROOT) -> str:
    """Replace every CSS, JS and image reference with its inline equivalent."""
    html = LINK_RE.sub(lambda match: _style_block(root, match.group("url")), html)
    html = SCRIPT_RE.sub(lambda match: _script_block(root, match.group("url")), html)
    html = IMAGE_RE.sub(
        lambda match: f'{match.group("attr")}="{_data_url(root, match.group("url"))}"',
        html,
    )
    html = _inline_vendor(html, root)
    for fragment in FORBIDDEN:
        if fragment in html:
            raise ScrubError(f"Server reference survived the inlining pass: {fragment}")
    return html
