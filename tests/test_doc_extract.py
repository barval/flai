"""Legacy binary Word (.doc): the OLE2 container, the piece table and its text.

No Word was involved: the fixtures are built byte by byte in `_build_doc`, so the
test exercises the real layout (FIB, table stream, piece descriptors) without
shipping a binary blob and without a converter on the machine.
"""

import pytest

from app.doc_extract import DocExtractError, extract_doc_text
from tests._doc_fixtures import build_doc

pytestmark = pytest.mark.unit

SECTOR = 512
FREE = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD
NOSTREAM = 0xFFFFFFFF


def test_extracts_utf16_text():
    text = extract_doc_text(build_doc("Отчёт по проекту\rВторой абзац."))
    assert text == "Отчёт по проекту\nВторой абзац."


def test_extracts_single_byte_pieces():
    """Latin documents are stored one byte per character; bit 30 marks them."""
    assert extract_doc_text(build_doc("Report\rSecond line", compressed=True)) == "Report\nSecond line"


def test_field_instruction_is_dropped_and_its_result_kept():
    """Word stores a page number as \\x13PAGE\\x14 7\\x15 — the reader sees 7."""
    text = extract_doc_text(build_doc("Страница \x13PAGE\x14 7\x15 из 12"))
    assert text == "Страница  7 из 12"
    assert "PAGE" not in text


def test_table_cell_and_row_marks_become_whitespace():
    assert extract_doc_text(build_doc("ячейка\x07конец")) == "ячейка\tконец"


def test_encrypted_document_is_refused():
    with pytest.raises(DocExtractError, match="encrypted"):
        extract_doc_text(build_doc("секрет", encrypted=True))


def test_a_file_that_is_not_an_ole_container_is_refused():
    with pytest.raises(DocExtractError):
        extract_doc_text(b"plain text pretending to be a document")


def test_an_ole_container_without_the_word_stream_is_refused():
    assert "WordDocument".encode("utf-16-le") in build_doc("x")
    with pytest.raises(DocExtractError):
        extract_doc_text(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(4096))


def test_empty_input_is_refused():
    with pytest.raises(DocExtractError):
        extract_doc_text(b"")
