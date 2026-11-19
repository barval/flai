"""The SVG validator refuses executable content and malformed documents.

The preview is served as an image with a sandbox CSP, but a stored file can also be
downloaded and opened on its own; these checks are what makes that safe.
"""

import pytest

from app.svg_safety import SvgValidationError, validate_svg

pytestmark = pytest.mark.unit

VALID_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20">
  <title>Pulse</title>
  <desc>A red square with a gradient and a CSS animation.</desc>
  <defs>
    <linearGradient id="g"><stop offset="0" stop-color="#fff"/></linearGradient>
    <marker id="arrow"><path d="M0 0L4 2L0 4Z"/></marker>
  </defs>
  <style>
    @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .4; } }
    .pulse { animation: pulse 2s ease-in-out infinite; }
  </style>
  <rect class="pulse" width="20" height="20" fill="url(#g)"/>
  <path d="M0 0L10 10" marker-end="url(#arrow)"/>
  <text x="2" y="12" font-family="system-ui">Привет</text>
</svg>"""


def test_a_self_contained_svg_passes():
    validate_svg(VALID_SVG)


def test_an_internal_reference_passes():
    validate_svg('<svg xmlns="http://www.w3.org/2000/svg"><use href="#icon"/></svg>')
    validate_svg('<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:url(#g)"/></svg>')
    validate_svg('<svg xmlns="http://www.w3.org/2000/svg"><rect fill="url( #g )"/></svg>')


@pytest.mark.parametrize(
    ("document", "code"),
    [
        ("<svg><rect></svg>", "svg_invalid"),
        ("<div/>", "svg_invalid"),
        ("<svg><script>alert(1)</script></svg>", "svg_unsafe"),
        ("<svg><SCRIPT>alert(1)</SCRIPT></svg>", "svg_unsafe"),
        ('<svg><rect onload="alert(1)"/></svg>', "svg_unsafe"),
        ('<svg><image href="https://example.test/x.png"/></svg>', "svg_unsafe"),
        ('<svg><image xlink:href="https://example.test/x.png" xmlns:xlink="http://www.w3.org/1999/xlink"/></svg>', "svg_unsafe"),
        ('<svg><rect href="javascript:alert(1)"/></svg>', "svg_unsafe"),
        ('<svg><rect fill="url(https://example.test/x.svg#g)"/></svg>', "svg_unsafe"),
        ("<svg><style>@import url(https://example.test/x.css);</style></svg>", "svg_unsafe"),
        ("<svg><style>@font-face{font-family:x;src:url(https://example.test/x.woff)}</style></svg>", "svg_unsafe"),
        ('<svg><style>rect{background:image-set("//example.test/x.png" 1x)}</style></svg>', "svg_unsafe"),
        ('<svg><rect style="background:url(//example.test/x.png)"/></svg>', "svg_unsafe"),
        ("<svg><foreignObject><div/></foreignObject></svg>", "svg_unsafe"),
        ('<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd"><svg/>', "svg_unsafe"),
        ('<!ENTITY x "y"><svg/>', "svg_unsafe"),
        ('<?xml-stylesheet href="https://example.test/x.css"?><svg/>', "svg_unsafe"),
        ('<svg><animate attributeName="href" to="javascript:alert(1)"/></svg>', "svg_unsafe"),
        ('<svg><set attributeName="onload" to="alert(1)"/></svg>', "svg_unsafe"),
    ],
)
def test_executable_or_external_content_is_refused(document, code):
    with pytest.raises(SvgValidationError) as exc:
        validate_svg(document)
    assert exc.value.code == code
