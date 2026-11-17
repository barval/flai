# tests/test_document_source.py
"""A document says where it came from: an upload, the crawler, a chat attachment,
or the assistant writing a file."""

import pytest

from app import db, documents_write

pytestmark = pytest.mark.requires_db

LOGIN = "source_test"


@pytest.fixture
def source_env(test_app, tmp_path, monkeypatch):
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


def test_a_written_document_is_marked_generated(source_env):
    doc = documents_write.write_document_file(LOGIN, "n", "c")

    assert db.get_document(doc.id, LOGIN)["source"] == "generated"


def test_an_ordinary_save_defaults_to_upload(source_env):
    db.save_document(LOGIN, "plain-doc", "plain.txt", 3, ".txt", f"{LOGIN}/plain-doc.txt")

    assert db.get_document("plain-doc", LOGIN)["source"] == "upload"


def test_the_two_programmatic_writers_name_their_origin():
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "app/queue.py").read_text(encoding="utf-8")

    assert 'source="crawl"' in source
    assert 'source="chat"' in source


def test_the_panel_knows_the_badge_for_every_origin():
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "app/static/js/chat-documents.js").read_text(encoding="utf-8")

    for value in ("generated", "crawl", "chat"):
        assert value in js, value


def test_the_badge_labels_are_translated():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for msgid in ("Generated", "Crawled", "From chat"):
        for lang in ("en", "ru"):
            catalog = (root / f"translations/{lang}/LC_MESSAGES/messages.po").read_text(encoding="utf-8")
            assert f'msgid "{msgid}"' in catalog, (msgid, lang)


def test_a_fresh_database_gets_every_documents_column():
    """The migration file is the only thing that creates them: `CREATE TABLE` does not.

    A commit once replaced a column's ALTER instead of adding to it, and nothing
    noticed because the development database already had it.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "app/database.py").read_text(encoding="utf-8")

    for column in ("blob_sha256 TEXT", "source TEXT NOT NULL DEFAULT 'upload'"):
        assert f"ALTER TABLE documents ADD COLUMN IF NOT EXISTS {column}" in source, column
