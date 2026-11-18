"""The three container formats a model can now produce, read back by real readers.

A file that cannot be opened is worse than a text answer: the person asked for a document
for Word, a spreadsheet, or a page that looks the same everywhere. These tests open each
one with the library that reads it, and check the two things that break silently — Cyrillic
and a cell that a spreadsheet would execute.
"""

import io

import pytest

from app import documents_write
from app.documents_write import DocumentWriteError, write_document_file

pytestmark = pytest.mark.requires_db

LOGIN = "format_test"


@pytest.fixture
def writer_env(test_app, tmp_path, monkeypatch):
    root = tmp_path / "documents"
    monkeypatch.setattr(documents_write, "_documents_root", lambda: str(root))
    with test_app.app_context():
        from app.database import get_db

        def clear():
            with get_db() as conn:
                conn.cursor().execute("DELETE FROM documents WHERE user_id = %s", (LOGIN,))

        clear()
        test_app.config["DOCUMENTS_FOLDER"] = str(root)
        yield root
        clear()


def _read(root, written):
    return (root / written.file_path).read_bytes()


def test_a_docx_keeps_headings_bullets_and_cyrillic(writer_env):
    written = write_document_file(
        LOGIN,
        "Отчёт за месяц",
        content="# Итоги\n\nПродажи выросли на 12%.\n\n- Первый пункт\n- Второй пункт\n\n## Раздел\n\nТекст.",
        fmt="docx",
    )

    from docx import Document

    document = Document(io.BytesIO(_read(writer_env, written)))
    texts = [p.text for p in document.paragraphs]
    assert "Итоги" in texts
    assert "Продажи выросли на 12%." in texts
    assert "Первый пункт" in texts
    assert written.filename.endswith(".docx")


def test_an_xlsx_keeps_the_sheet_name_and_rows(writer_env):
    written = write_document_file(
        LOGIN,
        "Продажи",
        fmt="xlsx",
        rows=[["Товар", "Цена"], ["Хлеб", "40"]],
    )

    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(_read(writer_env, written)))
    sheet = workbook["Sheet1"]
    assert [[cell.value for cell in row] for row in sheet.iter_rows()] == [
        ["Товар", "Цена"],
        ["Хлеб", "40"],
    ]


def test_an_xlsx_accepts_several_named_sheets(writer_env):
    written = write_document_file(
        LOGIN,
        "Книга",
        fmt="xlsx",
        sheets=[
            {"name": "Итоги", "rows": [["Всего", "100"]]},
            {"name": "Детали", "rows": [["Позиция", "Сумма"], ["A", "60"]]},
        ],
    )

    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(_read(writer_env, written)))
    assert workbook.sheetnames == ["Итоги", "Детали"]
    assert workbook["Детали"]["B2"].value == "60"


def test_a_formula_in_a_cell_stays_text(writer_env):
    """Formula injection: a generated sheet must not execute what the model wrote."""
    written = write_document_file(
        LOGIN,
        "Опасная таблица",
        fmt="xlsx",
        rows=[["=1+1", "@SUM(A1:A2)", "-2+2"]],
    )

    from openpyxl import load_workbook

    sheet = load_workbook(io.BytesIO(_read(writer_env, written)))["Sheet1"]
    values = [cell.value for cell in next(sheet.iter_rows())]
    assert all(value.startswith("'") for value in values), values


def test_a_pdf_renders_cyrillic_that_can_be_extracted(writer_env):
    written = write_document_file(
        LOGIN,
        "Страница",
        content="# Отчёт\n\nРусский текст в PDF.",
        fmt="pdf",
    )

    import pdfplumber

    with pdfplumber.open(io.BytesIO(_read(writer_env, written))) as document:
        text = document.pages[0].extract_text() or ""
    assert "Отчёт" in text
    assert "Русский текст" in text
    assert written.filename.endswith(".pdf")


def test_appending_to_a_binary_document_is_refused(writer_env):
    with pytest.raises(DocumentWriteError) as caught:
        write_document_file(LOGIN, "Страница", content="Текст", fmt="pdf", append=True)

    assert caught.value.code == "append_binary"


def test_a_table_without_rows_is_refused(writer_env):
    with pytest.raises(DocumentWriteError) as caught:
        write_document_file(LOGIN, "Пустая книга", fmt="xlsx")

    assert caught.value.code == "csv_rows"
