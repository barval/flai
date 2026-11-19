"""Editing what the assistant wrote, and only that.

A generated document can be replaced because this assistant owns its bytes. An upload cannot:
the blob layer may serve the same bytes to every user the document was shared with, so an
in-place edit would change a copy somebody else is holding. The rule is enforced here, not
only in the tool description.

History is append-only. A restore writes a new version with the old body, so undoing a
rollback is another rollback rather than a lost row.
"""

import pytest

from app import db, documents_write
from app.documents_write import (
    DocumentWriteError,
    edit_document_file,
    restore_document_version,
    write_document_file,
)

pytestmark = pytest.mark.requires_db

LOGIN = "edit_test"


@pytest.fixture
def writer_env(test_app, tmp_path, monkeypatch):
    root = tmp_path / "documents"
    monkeypatch.setattr(documents_write, "_documents_root", lambda: str(root))
    with test_app.app_context():
        from app.database import get_db

        def clear():
            with get_db() as conn:
                c = conn.cursor()
                c.execute("DELETE FROM documents WHERE user_id = %s", (LOGIN,))
                c.execute("DELETE FROM user_storage WHERE user_id = %s", (LOGIN,))

        clear()
        test_app.config["DOCUMENTS_FOLDER"] = str(root)
        yield root
        clear()


def _read(root, doc, extension):
    return (root / LOGIN / f"{doc.id}{extension}").read_bytes()


def test_editing_replaces_the_body_and_keeps_the_old_version(writer_env):
    doc = write_document_file(LOGIN, "notes", "first\n", fmt="txt")

    edited = edit_document_file(LOGIN, "notes.txt", content="second\n")

    assert edited.id == doc.id
    assert edited.filename == "notes.txt"
    assert _read(writer_env, doc, ".txt") == b"second\n"
    assert edited.version == 2
    history = db.get_document_versions(doc.id)
    assert [row["version"] for row in history] == [2, 1]
    assert db.get_document_version(doc.id, 1)["content"] == "first\n"


def test_an_upload_is_refused(writer_env):
    db.save_document(LOGIN, "upload1", "upload.txt", 5, ".txt", "edit_test/upload1.txt")

    with pytest.raises(DocumentWriteError) as refusal:
        edit_document_file(LOGIN, "upload.txt", content="changed\n")

    assert refusal.value.code == "not_generated"


def test_a_missing_document_is_named(writer_env):
    with pytest.raises(DocumentWriteError) as refusal:
        edit_document_file(LOGIN, "nothing.md", content="text\n")

    assert refusal.value.code == "not_found"


def test_the_previous_state_comes_back_as_a_new_version(writer_env):
    doc = write_document_file(LOGIN, "plan", "v1\n", fmt="txt")
    edit_document_file(LOGIN, "plan.txt", content="v2\n")

    restored = restore_document_version(LOGIN, "plan.txt")

    assert _read(writer_env, doc, ".txt") == b"v1\n"
    assert restored.version == 3
    assert [row["version"] for row in db.get_document_versions(doc.id)] == [3, 2, 1]


def test_a_named_version_can_be_brought_back(writer_env):
    doc = write_document_file(LOGIN, "log", "one\n", fmt="txt")
    edit_document_file(LOGIN, "log.txt", content="two\n")
    edit_document_file(LOGIN, "log.txt", content="three\n")

    restored = restore_document_version(LOGIN, "log.txt", version=1)

    assert _read(writer_env, doc, ".txt") == b"one\n"
    assert restored.version == 4


def test_an_unknown_version_is_refused(writer_env):
    write_document_file(LOGIN, "short", "only\n", fmt="txt")

    with pytest.raises(DocumentWriteError) as refusal:
        restore_document_version(LOGIN, "short.txt", version=7)

    assert refusal.value.code == "version_not_found"


def test_a_document_without_history_cannot_roll_back(writer_env):
    doc = write_document_file(LOGIN, "fresh", "one\n", fmt="txt")

    with pytest.raises(DocumentWriteError) as refusal:
        restore_document_version(LOGIN, "fresh.txt")

    assert refusal.value.code == "no_versions"
    assert db.get_document(doc.id, LOGIN) is not None


def test_a_spreadsheet_is_re_rendered_from_the_new_rows(writer_env):
    doc = write_document_file(LOGIN, "sheet", fmt="xlsx", rows=[["a", "b"]])

    edited = edit_document_file(LOGIN, "sheet.xlsx", rows=[["c", "d"]])

    data = _read(writer_env, doc, ".xlsx")
    assert data.startswith(b"PK")
    assert edited.version == 2
    assert db.get_document_version(doc.id, 2)["rows"] == [["c", "d"]]


def test_a_pdf_is_re_rendered_from_its_source(writer_env):
    doc = write_document_file(LOGIN, "letter", "Hello\n", fmt="pdf")

    edit_document_file(LOGIN, "letter.pdf", content="Goodbye\n")

    data = _read(writer_env, doc, ".pdf")
    assert data.startswith(b"%PDF")
    assert db.get_document_version(doc.id, 1)["content"] == "Hello\n"
    assert db.get_document_version(doc.id, 2)["content"] == "Goodbye\n"


def test_an_indexed_document_becomes_stale(writer_env):
    doc = write_document_file(LOGIN, "indexed", "old\n", fmt="txt")
    db.update_document_index_status(doc.id, db.INDEX_STATUS_INDEXED)

    edit_document_file(LOGIN, "indexed.txt", content="new\n")

    assert db.get_document(doc.id, LOGIN)["index_status"] == db.INDEX_STATUS_STALE


def test_editing_does_not_add_a_document(writer_env):
    write_document_file(LOGIN, "single", "one\n", fmt="txt")

    edit_document_file(LOGIN, "single.txt", content="two\n")

    assert len(db.get_user_documents(LOGIN)) == 1
