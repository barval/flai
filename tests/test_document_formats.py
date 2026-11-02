"""Every advertised document format is accepted by the validator and readable.

The upload whitelist and the extractor have to agree: a format the panel offers
but the extractor cannot read produces a document that fails a second later
(which is what `.doc` did), and a format the extractor reads but the whitelist
rejects is a feature nobody can use (`.md`).
"""

import io
import zipfile
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
    if name.endswith(".doc"):
        from tests._doc_fixtures import build_doc

        return build_doc("Договор поставки\rВторой абзац документа.")
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
        "a.doc",
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


def test_legacy_doc_is_accepted_and_read(tmp_path):
    """Word 97-2003 is a supported format: the text is parsed from the OLE2 file."""
    from app.utils import extract_text_from_file
    from tests._doc_fixtures import build_doc

    path = tmp_path / "contract.doc"
    path.write_bytes(build_doc("Договор поставки\rВторой абзац документа."))
    text = extract_text_from_file(str(path)) or ""
    assert "Договор поставки" in text
    assert "Второй абзац" in text


def test_an_encrypted_doc_is_refused_at_upload(app):
    """A password-protected .doc would index as empty; say so at upload instead."""
    from app.routes.documents import validate_file
    from tests._doc_fixtures import build_doc

    with app.app_context():
        valid, error = validate_file(io.BytesIO(build_doc("секрет", encrypted=True)), "protected.doc")
    assert not valid
    # The message is localized: match the concept, not one language's wording.
    assert "парол" in (error or "").lower() or "password" in (error or "").lower()


def test_a_broken_doc_indexes_as_empty_rather_than_crashing(tmp_path):
    """A truncated .doc has no text; indexing reports "nothing", never an exception."""
    from app.utils import extract_text_from_file

    path = tmp_path / "broken.doc"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512)
    assert extract_text_from_file(str(path)) is None


def test_an_unknown_extension_is_still_refused(app):
    from app.routes.documents import validate_file

    with app.app_context():
        valid, error = validate_file(io.BytesIO(b"PK\x03\x04" + b"\x00" * 600), "archive.zip")
    assert not valid
    assert error, "a refusal must say why"


def test_an_ooxml_package_is_accepted_when_magic_sees_only_a_zip(app, monkeypatch):
    """libmagic is given the first two kilobytes; an OOXML marker can sit past
    them, and the same .xlsx is then reported as a plain archive on one build and
    recognised on another. The container itself decides."""
    from app.routes.documents import validate_file

    monkeypatch.setattr("app.routes.documents.magic.from_buffer", lambda data, mime=True: "application/zip")
    with app.app_context():
        valid, error = validate_file(io.BytesIO(_valid_file("a.xlsx")), "a.xlsx")
    assert valid, error


def test_an_archive_without_the_format_entry_is_refused(app, monkeypatch):
    """A .zip renamed to .xlsx never becomes a spreadsheet."""
    from app.routes.documents import validate_file

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "not a workbook")
    monkeypatch.setattr("app.routes.documents.magic.from_buffer", lambda data, mime=True: "application/zip")
    with app.app_context():
        valid, _error = validate_file(io.BytesIO(buffer.getvalue()), "fake.xlsx")
    assert not valid


def test_a_doc_is_accepted_when_magic_reports_the_generic_ole_type(app, monkeypatch):
    """libmagic reads 2 KB and calls most Word files `application/x-ole-storage`,
    which is exactly what a legacy .xls reports — the container decides instead.
    """
    from app.routes.documents import validate_file
    from tests._doc_fixtures import build_doc

    monkeypatch.setattr(
        "app.routes.documents.magic.from_buffer", lambda data, mime=True: "application/x-ole-storage"
    )
    with app.app_context():
        valid, error = validate_file(io.BytesIO(build_doc("Договор поставки")), "contract.doc")
    assert valid, error


def test_an_excel_workbook_renamed_to_doc_is_refused(app):
    """.xls is the same OLE2 container: without the Word stream it is not a .doc."""
    from app.routes.documents import validate_file

    with app.app_context():
        valid, _error = validate_file(io.BytesIO((DATA / "sample.xls").read_bytes()), "renamed.doc")
    assert not valid
