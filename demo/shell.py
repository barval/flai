"""Assemble one self-contained demo page: the bar, the iframe and both documents."""

from __future__ import annotations

import json
import re
from pathlib import Path

from demo.io import DEMO_DIR

LANGS = ("ru", "en")
KINDS = ("chat", "admin")
OTHER_LANG = {"ru": "en", "en": "ru"}

BAR_TEXT = {
    "ru": {
        "demo": "демо",
        "desktop": "Десктоп",
        "phone": "Телефон",
        "hint": "Только демонстрация интерфейса",
    },
    "en": {
        "demo": "demo",
        "desktop": "Desktop",
        "phone": "Phone",
        "hint": "Interface demo only",
    },
}

HEAD_RE = re.compile(r"<head\b[^>]*>")


def escape_document(html: str) -> str:
    """Make a rendered document safe to embed inside a script element."""
    return html.replace("</script", "<\\/script").replace("<!--", "<\\!--")


def _read(name: str) -> str:
    return (DEMO_DIR / name).read_text(encoding="utf-8")


def _with_bootstrap(html: str, bootstrap: str) -> str:
    match = HEAD_RE.search(html)
    if not match:
        return bootstrap + html
    end = match.end()
    return html[:end] + bootstrap + html[end:]


def build_shell(
    lang: str,
    kind: str,
    *,
    login_html: str,
    app_html: str,
    payload: dict,
) -> str:
    """Build the page the visitor opens: demo bar, iframe, login and application documents."""
    if lang not in LANGS:
        raise ValueError(f"Unknown demo language: {lang}")
    if kind not in KINDS:
        raise ValueError(f"Unknown demo page kind: {kind}")

    text = BAR_TEXT[lang]
    sibling = f"{kind}-{OTHER_LANG[lang]}.html"
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False).replace("</", "<\\/")
    shim = _read("shim.js")
    bootstrap = (
        "<script>\n"
        f"window.__DEMO__ = {data};\n"
        "</script>\n"
        "<script>\n"
        f"{shim}\n"
        "</script>\n"
    )

    login_document = escape_document(_with_bootstrap(login_html, bootstrap))
    app_document = escape_document(_with_bootstrap(app_html, bootstrap))

    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FLAI — {text["demo"]}</title>
<style>
{_read("shell.css")}
</style>
</head>
<body class="demo-desktop">
<header class="demo-bar">
<span class="demo-brand">FLAI <em>{text["demo"]}</em></span>
<div class="demo-devices">
<button type="button" class="demo-device" data-device="desktop">{text["desktop"]}</button>
<button type="button" class="demo-device" data-device="mobile">{text["phone"]}</button>
</div>
<a class="demo-lang" href="{sibling}">{OTHER_LANG[lang].upper()}</a>
<span class="demo-hint">{text["hint"]}</span>
</header>
<iframe id="demo-screen" title="FLAI {text["demo"]}"></iframe>
<script type="text/plain" id="demo-login">{login_document}</script>
<script type="text/plain" id="demo-app">{app_document}</script>
<script>
{_read("shell.js")}
</script>
</body>
</html>
"""
