"""Turn what the model writes into a file format a person expects to open.

The tool's input is text and rows, never a binary blob: a model produces words, not
a zip container. This module does the conversion, so the write path stays the same
for every format — naming, quota and the row are `documents_write`'s business.

Markup is deliberately small: `#`, `##`, `###` headings, `-`/`*` bullets, blank lines
between paragraphs. Anything else is text. A file that promises a layout it cannot
render is worse than a plain one.

PDF needs a font with Cyrillic; DejaVu ships with the image (and is added explicitly
in the Dockerfile). Without it the renderer refuses instead of producing squares.
"""

from __future__ import annotations

import io
import os
import re

MAX_SHEETS = 5
MAX_ROWS = 5000
MAX_COLUMNS = 50

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
)
BOLD_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
)

HEADING = re.compile(r"^(#{1,3})\s+(.*)$")
BULLET = re.compile(r"^[-*]\s+(.*)$")

# A cell that starts with one of these is a formula to a spreadsheet, not text.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class RenderError(Exception):
    """A format cannot be produced; the caller turns ``code`` into a sentence."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def blocks(text: str) -> list[tuple[str, str]]:
    """Split the text into (kind, value): heading levels, bullets and paragraphs."""
    result: list[tuple[str, str]] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            result.append(("p", " ".join(part.strip() for part in paragraph).strip()))
            paragraph.clear()

    for raw in str(text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        heading = HEADING.match(line)
        if heading:
            flush()
            result.append((f"h{len(heading.group(1))}", heading.group(2).strip()))
            continue
        bullet = BULLET.match(line)
        if bullet:
            flush()
            result.append(("li", bullet.group(1).strip()))
            continue
        paragraph.append(line)
    flush()
    return [item for item in result if item[1]]


def render_docx(text: str) -> bytes:
    from docx import Document

    document = Document()
    for kind, value in blocks(text):
        if kind == "h1":
            document.add_heading(value, level=1)
        elif kind == "h2":
            document.add_heading(value, level=2)
        elif kind == "h3":
            document.add_heading(value, level=3)
        elif kind == "li":
            document.add_paragraph(value, style="List Bullet")
        else:
            document.add_paragraph(value)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _font_path(candidates: tuple[str, ...]) -> str | None:
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _pdf_styles(font_name: str):
    from reportlab.lib.styles import ParagraphStyle

    base = ParagraphStyle("body", fontName=font_name, fontSize=10.5, leading=15, spaceAfter=6)
    return {
        "body": base,
        "h1": ParagraphStyle("h1", parent=base, fontSize=17, leading=22, spaceBefore=10, spaceAfter=8),
        "h2": ParagraphStyle("h2", parent=base, fontSize=14, leading=19, spaceBefore=8, spaceAfter=6),
        "h3": ParagraphStyle("h3", parent=base, fontSize=12, leading=17, spaceBefore=6, spaceAfter=4),
        "li": ParagraphStyle(
            "li",
            parent=base,
            leftIndent=12,
            bulletIndent=2,
            # The bullet is drawn with the body font: Helvetica has no U+2022 in
            # the embedded subset, and the extracted text came out as (cid:127).
            bulletFontName=font_name,
            bulletFontSize=base.fontSize,
        ),
    }


def render_pdf(text: str, title: str = "") -> bytes:
    from xml.sax.saxutils import escape

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    regular = _font_path(FONT_CANDIDATES)
    bold = _font_path(BOLD_CANDIDATES)
    if regular is None:
        # Refusing is better than a page of empty boxes: the caller reports it.
        raise RenderError("pdf_font")
    if "FLAIDejaVu" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("FLAIDejaVu", regular))
        if bold:
            pdfmetrics.registerFont(TTFont("FLAIDejaVu-Bold", bold))

    styles = _pdf_styles("FLAIDejaVu")
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=title or None,
    )
    story = []
    for kind, value in blocks(text):
        safe = escape(value)
        if kind == "li":
            story.append(Paragraph(safe, styles["li"], bulletText="•"))
        else:
            story.append(Paragraph(safe, styles.get(kind, styles["body"])))
        if kind.startswith("h"):
            story.append(Spacer(1, 2))
    if not story:
        story.append(Paragraph(" ", styles["body"]))
    document.build(story)
    return buffer.getvalue()


def _safe_cell(value: object) -> object:
    """Text that looks like a formula stays text (spreadsheet formula injection)."""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def _check_sheet(rows: list) -> None:
    if len(rows) > MAX_ROWS:
        raise RenderError("too_many_rows", str(len(rows)))
    for row in rows:
        if not isinstance(row, (list, tuple)):
            raise RenderError("bad_rows")
        if len(row) > MAX_COLUMNS:
            raise RenderError("too_many_columns", str(len(row)))


def render_xlsx(rows: list | None = None, sheets: list | None = None) -> bytes:
    """One workbook; ``sheets`` wins when both are given."""
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.remove(workbook.active)

    plan: list[tuple[str, list]] = []
    if isinstance(sheets, list) and sheets:
        if len(sheets) > MAX_SHEETS:
            raise RenderError("too_many_sheets", str(len(sheets)))
        for index, sheet in enumerate(sheets, 1):
            if not isinstance(sheet, dict):
                raise RenderError("bad_sheets")
            name = str(sheet.get("name") or f"Sheet{index}")[:31]
            values = sheet.get("rows")
            if not isinstance(values, list) or not values:
                raise RenderError("csv_rows")
            plan.append((name, values))
    else:
        if not isinstance(rows, list) or not rows:
            raise RenderError("csv_rows")
        plan.append(("Sheet1", rows))

    for name, values in plan:
        _check_sheet(values)
        sheet = workbook.create_sheet(title=name)
        for row in values:
            sheet.append([_safe_cell(cell) for cell in row])

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
