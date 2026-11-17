# tests/test_write_document.py
"""A generated document is a document: named, contained, charged, and on disk only
if it passed every check first."""

import csv
import io

import pytest

from app import db, documents_write
from app.documents_write import (
    MAX_CONTENT_BYTES,
    DocumentWriteError,
    write_document_file,
)

pytestmark = pytest.mark.requires_db

LOGIN = "writer_test"


@pytest.fixture
def writer_env(test_app, tmp_path, monkeypatch):
    """A throw-away documents root, clean rows for this login."""
    root = tmp_path / "documents"
    monkeypatch.setattr(documents_write, "_documents_root", lambda: str(root))
    # The writer reads the documents folder from the application config, as it does
    # inside a request; the context stays open for the whole test.
    with test_app.app_context():
        conn_cleanup()
        yield root
        conn_cleanup()


def conn_cleanup():
    from app.database import get_db

    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM documents WHERE user_id = %s", (LOGIN,))
        c.execute("DELETE FROM user_storage WHERE user_id = %s", (LOGIN,))


def test_markdown_is_written_under_the_users_folder(writer_env):
    doc = write_document_file(LOGIN, "Отчёт", "# Итоги\n\nТекст.", fmt="md")

    assert doc.filename == "Отчёт.md"
    written = (writer_env / LOGIN / f"{doc.id}.md").read_text(encoding="utf-8")
    assert written.strip() == "# Итоги\n\nТекст."   # a text file ends with a newline
    assert db.get_document(doc.id, LOGIN)["filename"] == "Отчёт.md"


def test_a_path_shaped_name_is_only_a_name(writer_env):
    doc = write_document_file(LOGIN, "../../etc/passwd", "x", fmt="txt")

    assert doc.filename == "passwd.txt"
    assert not (writer_env.parent / "etc").exists()
    assert not (writer_env / "etc").exists()


def test_an_existing_name_gets_a_suffix_and_nothing_is_overwritten(writer_env):
    first = write_document_file(LOGIN, "note", "one", fmt="md")
    second = write_document_file(LOGIN, "note", "two", fmt="md")

    assert first.filename == "note.md"
    assert second.filename == "note (2).md"
    assert (writer_env / LOGIN / f"{first.id}.md").read_text(encoding="utf-8").strip() == "one"


def test_csv_quotes_values_that_need_it(writer_env):
    doc = write_document_file(LOGIN, "table", rows=[["имя", "заметка"], ["a", 'пункт, "цитата"']], fmt="csv")

    text = (writer_env / LOGIN / f"{doc.id}.csv").read_text(encoding="utf-8")
    assert 'пункт, ""цитата"""' in text
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[1] == ["a", 'пункт, "цитата"']


def test_invalid_json_is_refused_and_leaves_nothing(writer_env):
    with pytest.raises(DocumentWriteError) as error:
        write_document_file(LOGIN, "bad", "{not json", fmt="json")

    assert error.value.code == "json"
    folder = writer_env / LOGIN
    assert not folder.exists() or not any(folder.iterdir())


def test_an_unknown_format_is_refused(writer_env):
    with pytest.raises(DocumentWriteError) as error:
        write_document_file(LOGIN, "x", "y", fmt="pdf")

    assert error.value.code == "format"


def test_oversized_content_is_refused_before_the_write(writer_env):
    with pytest.raises(DocumentWriteError) as error:
        write_document_file(LOGIN, "big", "x" * (MAX_CONTENT_BYTES + 1), fmt="txt")

    assert error.value.code == "too_large"
    folder = writer_env / LOGIN
    assert not folder.exists() or not any(folder.iterdir())


def test_a_full_byte_quota_is_refused_before_the_write(writer_env, test_app):
    test_app.config["MAX_DOCUMENTS_STORAGE_MB"] = 0

    with pytest.raises(DocumentWriteError) as error:
        write_document_file(LOGIN, "x", "y", fmt="md")

    assert error.value.code == "quota"
    folder = writer_env / LOGIN
    assert not folder.exists() or not any(folder.iterdir())


def test_the_document_count_quota_is_refused_too(writer_env, test_app):
    """The byte limit alone lets a loop create thousands of tiny files."""
    test_app.config["MAX_DOCUMENTS_PER_USER"] = 0

    with pytest.raises(DocumentWriteError) as error:
        write_document_file(LOGIN, "x", "y", fmt="md")

    assert error.value.code == "quota"


def test_a_written_document_is_pending_for_indexing(writer_env):
    doc = write_document_file(LOGIN, "n", "c")

    assert db.get_document(doc.id, LOGIN)["index_status"] == db.INDEX_STATUS_PENDING


def test_a_foreign_extension_is_replaced_not_doubled(writer_env):
    doc = write_document_file(LOGIN, "report.pdf", "x", fmt="md")

    assert doc.filename == "report.md"


def test_csv_without_rows_names_the_real_mistake(writer_env):
    with pytest.raises(DocumentWriteError) as error:
        write_document_file(LOGIN, "t", "a,b", fmt="csv")

    assert error.value.code == "csv_rows"


def test_csv_rows_must_be_rows(writer_env):
    with pytest.raises(DocumentWriteError) as error:
        write_document_file(LOGIN, "t", rows=["a", "b"], fmt="csv")

    assert error.value.code == "csv_rows"


def test_empty_content_is_refused(writer_env):
    with pytest.raises(DocumentWriteError) as error:
        write_document_file(LOGIN, "x", "   ", fmt="md")

    assert error.value.code == "empty"


def test_the_usage_counter_grows_by_the_file_size(writer_env):
    before = db.get_user_storage_usage(LOGIN)
    doc = write_document_file(LOGIN, "n", "hello", fmt="txt")
    after = db.get_user_storage_usage(LOGIN)

    assert after - before == doc.size
