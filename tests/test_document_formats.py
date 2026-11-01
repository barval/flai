"""Every advertised document format is accepted by the validator and readable.

The upload whitelist and the extractor have to agree: a format the panel offers
but the extractor cannot read produces a document that fails a second later
(which is what `.doc` did), and a format the extractor reads but the whitelist
rejects is a feature nobody can use (`.md`).
"""

import io
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

DATA = Path("tests/data")


def _text(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_markdown_is_extracted(tmp_path):
    from app.utils import extract_text_from_file

    path = _text(tmp_path, "note.md", "# Заголовок\n\nТекст **markdown**.")
    text = extract_text_from_file(str(path)) or ""
    assert "Заголовок" in text and "markdown" in text


def test_html_keeps_the_readable_text(tmp_path):
    from app.utils import extract_text_from_file

    path = _text(tmp_path, "page.html", "<html><body><h1>Отчёт</h1><p>Текст страницы.</p></body></html>")
    text = extract_text_from_file(str(path)) or ""
    assert "Отчёт" in text and "Текст страницы" in text
    assert "<h1>" not in text


def test_xlsx_becomes_markdown_tables(tmp_path):
    from openpyxl import Workbook

    from app.utils import extract_text_from_file

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Данные"
    sheet.append(["Имя", "Сумма"])
    sheet.append(["Аня", 10])
    path = tmp_path / "book.xlsx"
    workbook.save(path)

    text = extract_text_from_file(str(path)) or ""
    assert "## Данные" in text
    assert "| Имя | Сумма |" in text
    assert "| Аня | 10 |" in text


def test_xls_becomes_markdown_tables():
    from app.utils import extract_text_from_file

    text = extract_text_from_file(str(DATA / "sample.xls")) or ""
    assert "## Data" in text
    assert "Аня" in text


def test_pptx_keeps_slide_text(tmp_path):
    from pptx import Presentation

    from app.utils import extract_text_from_file

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    slide.shapes.title.text = "Заголовок слайда"
    path = tmp_path / "deck.pptx"
    presentation.save(path)

    text = extract_text_from_file(str(path)) or ""
    assert "## Slide 1" in text
    assert "Заголовок слайда" in text


def test_fb2_flattens_the_book(tmp_path):
    from app.utils import extract_text_from_file

    body = (
        '<?xml version="1.0"?><FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
        "<body><section><p>Первый абзац.</p><p>Второй абзац.</p></section></body></FictionBook>"
    )
    path = _text(tmp_path, "book.fb2", body)
    text = extract_text_from_file(str(path)) or ""
    assert "Первый абзац" in text and "Второй абзац" in text


def test_eml_keeps_headers_and_body(tmp_path):
    from app.utils import extract_text_from_file

    raw = (
        b"From: a@example.com\r\nTo: b@example.com\r\nSubject: Test\r\n"
        b"MIME-Version: 1.0\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nHello body\r\n"
    )
    path = tmp_path / "mail.eml"
    path.write_bytes(raw)
    text = extract_text_from_file(str(path)) or ""
    assert "Subject" in text and "Test" in text
    assert "Hello body" in text


@pytest.mark.parametrize("name", ["a.xml", "a.yaml", "a.yml", "a.toml", "a.ini", "a.cfg", "a.log"])
def test_plain_text_dialects_are_read(tmp_path, name):
    from app.utils import extract_text_from_file

    path = _text(tmp_path, name, "ключ: значение\n")
    assert "ключ" in (extract_text_from_file(str(path)) or "")


def _valid_file(name: str) -> bytes:
    """Real bytes for each extension, so magic sees what a user would upload."""
    if name.endswith(".xlsx"):
        workbook = io.BytesIO()
        from openpyxl import Workbook

        Workbook().save(workbook)
        return workbook.getvalue()
    if name.endswith(".pptx"):
        stream = io.BytesIO()
        from pptx import Presentation

        Presentation().save(stream)
        return stream.getvalue()
    if name.endswith(".xls"):
        return (DATA / "sample.xls").read_bytes()
    if name.endswith(".eml"):
        return b"From: a@example.com\r\nSubject: Test\r\nContent-Type: text/plain\r\n\r\nBody\r\n"
    if name.endswith(".fb2"):
        return b'<?xml version="1.0"?><FictionBook><body><p>Text</p></body></FictionBook>'
    if name.endswith((".html", ".htm")):
        return b"<html><body><p>Text</p></body></html>"
    if name.endswith(".xml"):
        return b'<?xml version="1.0"?><root><item>text</item></root>'
    return b"# text\n"


@pytest.mark.parametrize(
    "name",
    [
        "a.md",
        "a.markdown",
        "a.html",
        "a.htm",
        "a.xlsx",
        "a.xls",
        "a.pptx",
        "a.fb2",
        "a.xml",
        "a.yaml",
        "a.yml",
        "a.toml",
        "a.ini",
        "a.cfg",
        "a.log",
        "a.eml",
    ],
)
def test_new_formats_pass_validation(app, name):
    from app.routes.documents import validate_file

    with app.app_context():
        valid, error = validate_file(io.BytesIO(_valid_file(name)), name)
    assert valid, f"{name} rejected: {error}"


def test_legacy_doc_is_refused_with_a_hint(app):
    from app.routes.documents import validate_file

    with app.app_context():
        valid, error = validate_file(io.BytesIO(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512), "old.doc")
    assert not valid
    assert "docx" in (error or "")


def test_an_unknown_extension_is_still_refused(app):
    from app.routes.documents import validate_file

    with app.app_context():
        valid, error = validate_file(io.BytesIO(b"PK\x03\x04" + b"\x00" * 600), "archive.zip")
    assert not valid
    assert error, "a refusal must say why"
