# tests/test_write_document_append.py
"""Appending is what makes a log possible: one file, growing, never a numbered pile.

The rules are narrow on purpose — only a document the assistant wrote, only up to a cap,
and never an upload whose bytes another user may share.
"""

import os

import pytest

from app import db, documents_write
from app.documents_write import (
    MAX_APPEND_BYTES,
    MAX_CONTENT_BYTES,
    DocumentWriteError,
    write_document_file,
)

pytestmark = pytest.mark.requires_db

LOGIN = "append_test"


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


def _text(root, doc_id, ext=".txt"):
    return (root / LOGIN / f"{doc_id}{ext}").read_text(encoding="utf-8")


def test_append_adds_to_the_same_document(writer_env):
    first = write_document_file(LOGIN, "peoples", "line one\n", fmt="txt")
    again = write_document_file(LOGIN, "peoples", "line two\n", fmt="txt", append=True)

    assert again.id == first.id
    assert _text(writer_env, first.id).split() == ["line", "one", "line", "two"]
    assert len(db.get_user_documents(LOGIN)) == 1


def test_append_to_a_missing_document_creates_it(writer_env):
    doc = write_document_file(LOGIN, "cats", "first sighting\n", fmt="txt", append=True)

    assert db.get_document(doc.id, LOGIN)["filename"] == "cats.txt"


def test_append_refuses_an_upload(writer_env):
    db.save_document(LOGIN, "upload-doc", "peoples.txt", 1, ".txt", f"{LOGIN}/upload-doc.txt")

    with pytest.raises(DocumentWriteError) as error:
        write_document_file(LOGIN, "peoples.txt", "x", fmt="txt", append=True)

    assert error.value.code == "append_target"


def test_append_refuses_past_the_cap(writer_env):
    """A log may grow, but not without end: the file stops at 2 MB."""
    doc = write_document_file(LOGIN, "big", "start", fmt="txt")
    chunk = "x" * (MAX_CONTENT_BYTES - 1)
    while os.path.getsize(writer_env / LOGIN / f"{doc.id}.txt") + len(chunk) + 1 <= MAX_APPEND_BYTES:
        write_document_file(LOGIN, "big", chunk, fmt="txt", append=True)

    with pytest.raises(DocumentWriteError) as error:
        write_document_file(LOGIN, "big", chunk, fmt="txt", append=True)

    assert error.value.code == "too_large"
    assert os.path.getsize(writer_env / LOGIN / f"{doc.id}.txt") <= MAX_APPEND_BYTES


def test_append_charges_only_the_delta(writer_env):
    write_document_file(LOGIN, "log", "abc", fmt="txt")
    before = db.get_user_storage_usage(LOGIN)
    write_document_file(LOGIN, "log", "de", fmt="txt", append=True)

    assert db.get_user_storage_usage(LOGIN) - before == 3  # "de\n"


def test_append_marks_an_indexed_document_stale(writer_env):
    doc = write_document_file(LOGIN, "log", "abc", fmt="txt")
    db.update_document_index_status(doc.id, db.INDEX_STATUS_INDEXED)

    write_document_file(LOGIN, "log", "more", fmt="txt", append=True)

    assert db.get_document(doc.id, LOGIN)["index_status"] == db.INDEX_STATUS_STALE


def test_append_keeps_rows_in_place(writer_env):
    doc = write_document_file(LOGIN, "log", "abc", fmt="txt")
    size_before = db.get_document(doc.id, LOGIN)["file_size"]

    write_document_file(LOGIN, "log", "de", fmt="txt", append=True)

    assert db.get_document(doc.id, LOGIN)["file_size"] > size_before
