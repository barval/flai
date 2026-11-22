"""Validation for SVG documents written by the assistant.

SVG is executable content: a script inside a file opened at the application's origin
would run with the reader's session. The preview is served as an image and carries a
sandbox CSP, but the stored file must also be safe to download and open elsewhere.

The checks are deliberately conservative: a drawing that needs a remote font, a remote
image or an embedded script is not something this product should store.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

# Animation is CSS-only. SMIL can retarget attributes such as href at runtime, and the
# writer has no parser that would prove the target stays local.
FORBIDDEN_ELEMENTS = frozenset(
    {
        "script",
        "foreignobject",
        "iframe",
        "object",
        "embed",
        "audio",
        "video",
        "canvas",
        "handler",
        "listener",
        "animate",
        "animatecolor",
        "animatetransform",
        "animatemotion",
        "set",
        "discard",
        "mpath",
    }
)
CSS_FORBIDDEN = re.compile(
    r"@import|@font-face|expression\s*\(|behavior\s*:|-moz-binding|javascript:|vbscript:|image-set\s*\(",
    re.IGNORECASE,
)
CSS_EXTERNAL = re.compile(r"https?://", re.IGNORECASE)
URL = re.compile(r"url\s*\(\s*(['\"]?)([^'\")]*)", re.IGNORECASE)
ENTITY = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b|<\?xml-stylesheet", re.IGNORECASE)


class SvgValidationError(ValueError):
    """A document the writer refuses; ``code`` is the sentence key, ``detail`` names it."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _local(name: str) -> str:
    """The local part of a namespaced XML name, lower-cased."""
    return name.rsplit("}", 1)[-1].lower()


def _squash(value: str) -> str:
    """Remove whitespace and control characters before comparing a URL scheme."""
    return re.sub(r"[\x00-\x20]+", "", value).lower()


def _check_css(css: str) -> None:
    text = css or ""
    if CSS_FORBIDDEN.search(text) or CSS_EXTERNAL.search(text):
        raise SvgValidationError("svg_unsafe", "CSS")
    for match in URL.finditer(text):
        if not match.group(2).strip().startswith("#"):
            raise SvgValidationError("svg_unsafe", "CSS url()")


def _check_value(attribute: str, value: str) -> None:
    text = str(value or "")
    if _squash(text).startswith(("javascript:", "vbscript:", "data:text/html")):
        raise SvgValidationError("svg_unsafe", attribute)
    if attribute in ("href", "src") and not text.strip().startswith("#"):
        raise SvgValidationError("svg_unsafe", attribute)
    if "url(" in text.lower():
        _check_css(text)


def validate_svg(text: str) -> None:
    """Raise ``SvgValidationError`` unless the text is a safe, standalone SVG document."""
    raw = str(text or "")
    if ENTITY.search(raw):
        raise SvgValidationError("svg_unsafe", "DOCTYPE or processing instruction")

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise SvgValidationError("svg_invalid", str(exc)) from exc

    if _local(root.tag) != "svg":
        raise SvgValidationError("svg_invalid", "root is not <svg>")

    for element in root.iter():
        name = _local(element.tag)
        if name in FORBIDDEN_ELEMENTS:
            raise SvgValidationError("svg_unsafe", name)
        for attribute, value in element.attrib.items():
            local = _local(attribute)
            if local.startswith("on"):
                raise SvgValidationError("svg_unsafe", local)
            _check_value(local, value)
            if local == "style":
                _check_css(str(value))
        if name == "style" and element.text:
            _check_css(element.text)
