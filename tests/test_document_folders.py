# tests/test_document_folders.py
"""Document folders: DB model, API and UI structure.

Folders are single-level (no nesting): every folder lives in the root of
the Documents panel and groups documents by ``folder_id`` (NULL = root).
"""

import pytest


class TestDocumentFoldersDB:
    """DB model for folders: create/list/rename/delete + folder_id on documents."""

    def test_create_and_list_folders(self, test_app):
        with test_app.app_context():
            from app import db

            db.save_folder("alice", "f1", "Contracts")
            db.save_folder("alice", "f2", "Photos")
            db.save_folder("bob", "f3", "Bob's")

            folders = db.get_user_folders("alice")
            assert [(f["id"], f["name"]) for f in folders] == [("f1", "Contracts"), ("f2", "Photos")]

    def test_get_folder_scoped_to_user(self, test_app):
        with test_app.app_context():
            from app import db

            db.save_folder("alice", "f1", "Contracts")
            assert db.get_folder("f1", "alice") is not None
            assert db.get_folder("f1", "bob") is None
            assert db.get_folder("nope", "alice") is None

    def test_rename_folder(self, test_app):
        with test_app.app_context():
            from app import db

            db.save_folder("alice", "f1", "Contracts")
            assert db.rename_folder("f1", "alice", "Legal") is True
            assert db.rename_folder("f1", "bob", "Other") is False
            folders = db.get_user_folders("alice")
            assert folders[0]["name"] == "Legal"

    def test_delete_folder(self, test_app):
        with test_app.app_context():
            from app import db

            db.save_folder("alice", "f1", "Contracts")
            assert db.delete_folder("f1", "alice") is True
            assert db.delete_folder("f1", "alice") is False
            assert db.get_user_folders("alice") == []

    def test_folder_documents_and_folder_id_in_listing(self, test_app):
        with test_app.app_context():
            from app import db

            db.save_folder("alice", "f1", "Contracts")
            db.save_document("alice", "d1", "a.pdf", 1000, ".pdf", "alice/a.pdf", folder_id="f1")
            db.save_document("alice", "d2", "b.pdf", 2000, ".pdf", "alice/b.pdf", folder_id="f1")
            db.save_document("alice", "d3", "c.txt", 300, ".txt", "alice/c.txt", folder_id=None)

            docs = db.get_folder_documents("alice", "f1")
            assert [(d["id"], d["file_size"]) for d in docs] == [("d1", 1000), ("d2", 2000)]

            listing = db.get_user_documents("alice")
            by_id = {d["id"]: d for d in listing}
            assert by_id["d1"]["folder_id"] == "f1"
            assert by_id["d3"]["folder_id"] is None

    def test_move_document_between_folders_and_to_root(self, test_app):
        with test_app.app_context():
            from app import db

            db.save_folder("alice", "f1", "Contracts")
            db.save_folder("alice", "f2", "Photos")
            db.save_document("alice", "d1", "a.pdf", 1000, ".pdf", "alice/a.pdf", folder_id="f1")

            assert db.set_document_folder("d1", "alice", "f2") is True
            assert db.get_user_documents("alice")[0]["folder_id"] == "f2"

            assert db.set_document_folder("d1", "alice", None) is True
            assert db.get_user_documents("alice")[0]["folder_id"] is None

            assert db.set_document_folder("nope", "alice", "f1") is False


class TestDocumentFoldersAPI:
    """Folders over HTTP: create/rename/delete (with cascade), move, bulk move."""

    @pytest.fixture
    def alice(self, client, test_app):
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login

            if not get_user_by_login("alice"):
                create_user("alice", "pass123", "Alice")
        client.post("/login", data={"login": "alice", "password": "pass123"})
        return client

    def test_create_folder(self, alice):
        r = alice.post("/api/document-folders", json={"name": "Contracts"})
        assert r.status_code == 200
        assert r.get_json()["status"] == "ok"
        assert "id" in r.get_json()

    def test_create_folder_validation(self, alice):
        assert alice.post("/api/document-folders", json={"name": "  "}).status_code == 400
        assert alice.post("/api/document-folders", json={"name": "x" * 65}).status_code == 400
        assert alice.post("/api/document-folders", json={}).status_code == 400

    def test_create_duplicate_folder_name(self, alice):
        assert alice.post("/api/document-folders", json={"name": "Contracts"}).status_code == 200
        assert alice.post("/api/document-folders", json={"name": "Contracts"}).status_code == 409

    def test_rename_folder(self, alice):
        fid = alice.post("/api/document-folders", json={"name": "Old"}).get_json()["id"]
        r = alice.patch(f"/api/document-folders/{fid}", json={"name": "New"})
        assert r.status_code == 200
        data = alice.get("/api/documents").get_json()
        assert [f["name"] for f in data["folders"]] == ["New"]

    def test_rename_folder_not_found(self, alice):
        assert alice.patch("/api/document-folders/missing", json={"name": "X"}).status_code == 404

    def test_delete_empty_folder(self, alice):
        fid = alice.post("/api/document-folders", json={"name": "Empty"}).get_json()["id"]
        r = alice.delete(f"/api/document-folders/{fid}")
        assert r.status_code == 200
        assert r.get_json()["deleted_documents"] == 0

    def test_delete_folder_cascades_documents(self, alice, test_app, tmp_path):
        fid = alice.post("/api/document-folders", json={"name": "Docs"}).get_json()["id"]
        with test_app.app_context():
            from app import db

            db.save_document("alice", "d1", "a.pdf", 120, ".pdf", "alice/a.pdf", folder_id=fid)
            db.save_document("alice", "d2", "b.pdf", 80, ".pdf", "alice/b.pdf", folder_id=fid)

        r = alice.delete(f"/api/document-folders/{fid}")
        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "ok"
        assert body["deleted_documents"] == 2
        assert body["deleted_size"] == 200

        with test_app.app_context():
            from app import db

            assert db.get_user_documents("alice") == []

    def test_move_document_via_folder_patch(self, alice, test_app):
        fid = alice.post("/api/document-folders", json={"name": "Docs"}).get_json()["id"]
        with test_app.app_context():
            from app import db

            db.save_document("alice", "d1", "a.pdf", 5, ".pdf", "alice/a.pdf")

        r = alice.patch("/api/documents/d1/folder", json={"folder_id": fid})
        assert r.status_code == 200
        data = alice.get("/api/documents").get_json()
        assert data["documents"][0]["folder_id"] == fid

        r = alice.patch("/api/documents/d1/folder", json={"folder_id": None})
        assert r.status_code == 200
        assert alice.get("/api/documents").get_json()["documents"][0]["folder_id"] is None

    def test_move_document_rejects_foreign_folder(self, alice):
        r = alice.patch("/api/documents/d1/folder", json={"folder_id": "nope"})
        assert r.status_code == 404

    def test_bulk_move(self, alice, test_app):
        fid = alice.post("/api/document-folders", json={"name": "Docs"}).get_json()["id"]
        with test_app.app_context():
            from app import db

            db.save_document("alice", "d1", "a.pdf", 5, ".pdf", "alice/a.pdf")
            db.save_document("alice", "d2", "b.pdf", 6, ".pdf", "alice/b.pdf")

        r = alice.post("/api/documents/move", json={"doc_ids": ["d1", "d2"], "folder_id": fid})
        assert r.status_code == 200
        assert r.get_json()["status"] == "ok"
        data = alice.get("/api/documents").get_json()
        assert {d["folder_id"] for d in data["documents"]} == {fid}

    def test_bulk_move_to_root(self, alice, test_app):
        fid = alice.post("/api/document-folders", json={"name": "Docs"}).get_json()["id"]
        with test_app.app_context():
            from app import db

            db.save_document("alice", "d1", "a.pdf", 5, ".pdf", "alice/a.pdf", folder_id=fid)

        r = alice.post("/api/documents/move", json={"doc_ids": ["d1"], "folder_id": None})
        assert r.status_code == 200
        assert alice.get("/api/documents").get_json()["documents"][0]["folder_id"] is None

    def test_documents_payload_has_folders_with_counts(self, alice, test_app):
        fid = alice.post("/api/document-folders", json={"name": "Docs"}).get_json()["id"]
        with test_app.app_context():
            from app import db

            db.save_document("alice", "d1", "a.pdf", 120, ".pdf", "alice/a.pdf", folder_id=fid)
            db.save_document("alice", "d2", "b.pdf", 80, ".pdf", "alice/b.pdf", folder_id=fid)
            db.save_document("alice", "d3", "c.txt", 30, ".txt", "alice/c.txt")

        data = alice.get("/api/documents").get_json()
        assert set(data.keys()) == {"folders", "documents"}
        folder = data["folders"][0]
        assert folder["id"] == fid
        assert folder["count"] == 2
        assert folder["size"] == 200
        assert len(data["documents"]) == 3

    def test_upload_into_folder(self, alice, test_app):
        import io
        from unittest.mock import patch

        fid = alice.post("/api/document-folders", json={"name": "Docs"}).get_json()["id"]
        with patch("app.routes.documents.magic.from_buffer", return_value="text/plain"):
            r = alice.post(
                "/api/documents/upload",
                data={"file": (io.BytesIO(b"hello"), "t.txt"), "folder_id": fid},
                content_type="multipart/form-data",
            )
        assert r.status_code == 200
        doc_id = r.get_json()["id"]
        doc = alice.get("/api/documents").get_json()["documents"][0]
        assert doc["id"] == doc_id
        assert doc["folder_id"] == fid

    def test_upload_into_foreign_folder_rejected(self, alice):
        import io
        from unittest.mock import patch

        with patch("app.routes.documents.magic.from_buffer", return_value="text/plain"):
            r = alice.post(
                "/api/documents/upload",
                data={"file": (io.BytesIO(b"hello"), "t.txt"), "folder_id": "nope"},
                content_type="multipart/form-data",
            )
        assert r.status_code == 400
