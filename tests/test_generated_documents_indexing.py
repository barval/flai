# tests/test_generated_documents_indexing.py
"""A document the assistant wrote stays out of RAG and the graph until its owner asks.

A log appended to every 15 minutes would otherwise be re-indexed 96 times a day and pour
entities into the graph from a file nobody asked about.
"""

import os

import pytest
from flask import session

from app import db, documents_write

pytestmark = pytest.mark.requires_db

LOGIN = "generated_index_test"


@pytest.fixture
def env(test_app, tmp_path, monkeypatch):
    root = tmp_path / "documents"
    monkeypatch.setattr(documents_write, "_documents_root", lambda: str(root))
    with test_app.app_context():
        from app.database import get_db

        def clear():
            with get_db() as conn:
                conn.cursor().execute("DELETE FROM documents WHERE user_id = %s", (LOGIN,))

        clear()
        test_app.config["DOCUMENTS_FOLDER"] = str(root)
        yield test_app
        clear()


@pytest.fixture
def queued(test_app, monkeypatch):
    calls = []
    monkeypatch.setattr(test_app.request_queue, "add_request", lambda **kw: calls.append(kw) or "id")
    return calls


def test_a_written_document_is_not_indexed_and_queues_nothing(env, queued):
    doc = documents_write.write_document_file(LOGIN, "log", "x", fmt="txt")

    assert db.get_document(doc.id, LOGIN)["index_status"] == db.INDEX_STATUS_NOT_INDEXED
    assert queued == []


def test_ticking_the_box_queues_one_index_task_with_an_absolute_path(env, queued):
    from app.routes.documents import set_document_indexing

    doc = documents_write.write_document_file(LOGIN, "log", "x", fmt="txt")
    with env.test_request_context(f"/api/documents/{doc.id}/index", method="POST", json={"enabled": True}):
        session["login"] = LOGIN
        session["language"] = "ru"
        set_document_indexing(doc.id)

    assert [call["request_data"]["type"] for call in queued] == ["index_document"]
    assert os.path.isabs(queued[0]["request_data"]["file_path"])
    assert db.get_document(doc.id, LOGIN)["index_status"] == db.INDEX_STATUS_PENDING


def test_unticking_removes_the_vectors_and_the_graph_rows(env, queued, monkeypatch):
    from app.routes.documents import set_document_indexing

    doc = documents_write.write_document_file(LOGIN, "log", "x", fmt="txt")
    deleted = {"rag": 0, "graph": 0}

    class _Rag:
        available = True

        def delete_document(self, doc_id, login, refresh_stats=True):
            deleted["rag"] += 1

    monkeypatch.setitem(env.modules, "rag", _Rag())
    monkeypatch.setattr(db, "delete_kg_document", lambda doc_id, login: deleted.__setitem__("graph", 1))

    with env.test_request_context(f"/api/documents/{doc.id}/index", method="POST", json={"enabled": False}):
        session["login"] = LOGIN
        session["language"] = "ru"
        set_document_indexing(doc.id)

    assert deleted == {"rag": 1, "graph": 1}
    assert db.get_document(doc.id, LOGIN)["index_status"] == db.INDEX_STATUS_NOT_INDEXED


def test_the_switch_is_refused_for_an_upload(env, queued, monkeypatch):
    from app.routes.documents import set_document_indexing

    db.save_document(LOGIN, "upload-doc", "u.txt", 1, ".txt", f"{LOGIN}/upload-doc.txt")
    db.update_document_index_status("upload-doc", db.INDEX_STATUS_INDEXED)

    with env.test_request_context("/api/documents/upload-doc/index", method="POST", json={"enabled": False}):
        session["login"] = LOGIN
        session["language"] = "ru"
        response = set_document_indexing("upload-doc")
    status = response[1] if isinstance(response, tuple) else 200

    assert status == 400
