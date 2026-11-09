# tests/test_document_sharing.py
"""Read-only document sharing: ACL, listing, access checks, search scope."""

from pathlib import Path
from unittest.mock import patch

import pytest


def _add_doc(login, doc_id, name="contract.pdf"):
    from app import db

    db.save_document(login, doc_id, name, 1000, ".pdf", f"{login}/{doc_id}.pdf")


@pytest.mark.requires_db
class TestSharingModel:
    def test_share_and_unshare(self, test_app):
        with test_app.app_context():
            from app import db

            _add_doc("alice", "d1")
            db.share_document("d1", "alice", "bob")

            assert [s["shared_with"] for s in db.get_document_shares("d1")] == ["bob"]
            assert db.get_shared_document_ids("bob") == {"d1": "alice"}
            assert db.get_shared_document_ids("carol") == {}

            assert db.unshare_document("d1", "bob") is True
            assert db.unshare_document("d1", "bob") is False
            assert db.get_document_shares("d1") == []

    def test_share_is_idempotent(self, test_app):
        with test_app.app_context():
            from app import db

            _add_doc("alice", "d1")
            db.share_document("d1", "alice", "bob")
            db.share_document("d1", "alice", "bob")
            assert len(db.get_document_shares("d1")) == 1

    def test_recipient_listing_marks_the_owner(self, test_app):
        with test_app.app_context():
            from app import db

            _add_doc("alice", "d1", "own.txt")
            _add_doc("carol", "d2", "shared.txt")
            db.share_document("d2", "carol", "bob")

            own_only = db.get_user_documents("bob")
            assert own_only == []

            with_shared = db.get_user_documents("bob", include_shared=True)
            assert [(d["id"], d["shared_by"]) for d in with_shared] == [("d2", "carol")]

    def test_owner_folder_is_not_applied_to_the_recipient(self, test_app):
        from app import db

        db.save_folder("alice", "f1", "Contracts")
        db.save_document("alice", "d1", "c.pdf", 1000, ".pdf", "alice/d1.pdf", folder_id="f1")
        db.share_document("d1", "alice", "bob")

        shared = db.get_user_documents("bob", include_shared=True)[0]
        assert shared["folder_id"] is None

    def test_owner_listing_carries_who_can_read_it(self, test_app):
        from app import db

        _add_doc("alice", "d1", "own.txt")
        _add_doc("alice", "d2", "private.txt")
        db.share_document("d1", "alice", "carol")
        db.share_document("d1", "alice", "bob")

        plain = {d["id"]: d for d in db.get_user_documents("alice")}
        assert plain["d1"]["shared_count"] == 0, "the extra query is opt-in"

        marked = {d["id"]: d for d in db.get_user_documents("alice", with_share_counts=True)}
        assert marked["d1"]["shared_with"] == ["bob", "carol"]
        assert marked["d1"]["shared_count"] == 2
        assert marked["d2"]["shared_count"] == 0

    def test_deleting_a_document_removes_its_shares(self, test_app):
        with test_app.app_context():
            from app import db

            _add_doc("alice", "d1")
            db.share_document("d1", "alice", "bob")
            db.delete_document("d1", "alice")
            assert db.get_document_shares("d1") == []
            assert db.get_shared_document_ids("bob") == {}

    def test_readable_document_covers_owner_and_recipient(self, test_app):
        with test_app.app_context():
            from app import db

            _add_doc("alice", "d1")
            assert db.get_readable_document("d1", "alice") is not None
            assert db.get_readable_document("d1", "bob") is None

            db.share_document("d1", "alice", "bob")
            shared = db.get_readable_document("d1", "bob")
            assert shared is not None
            assert shared["shared_by"] == "alice"
            assert db.get_readable_document("d1", "carol") is None


@pytest.mark.requires_db
class TestSharingRoutes:
    @pytest.fixture
    def users(self, test_app):
        from app.userdb import create_user, get_user_by_login

        with test_app.app_context():
            for login in ("alice", "bob", "carol"):
                if not get_user_by_login(login):
                    create_user(login, "pass12345", login.title())

            _add_doc("alice", "d1")

    def _client(self, test_app, login):
        client = test_app.test_client()
        client.post("/login", data={"login": login, "password": "pass12345"})
        return client

    def test_owner_shares_and_recipient_sees_it(self, test_app, users):
        alice = self._client(test_app, "alice")

        response = alice.post("/api/documents/d1/share", json={"login": "bob"})
        assert response.status_code == 200
        assert [s["shared_with"] for s in response.get_json()["shares"]] == ["bob"]

        bob = self._client(test_app, "bob")
        listing = bob.get("/api/documents").get_json()
        shared = [d for d in listing["documents"] if d["id"] == "d1"]
        assert len(shared) == 1
        assert shared[0]["shared_by"] == "alice"

    def test_recipient_cannot_share_or_delete(self, test_app, users):
        alice = self._client(test_app, "alice")
        alice.post("/api/documents/d1/share", json={"login": "bob"})

        bob = self._client(test_app, "bob")
        assert bob.post("/api/documents/d1/share", json={"login": "carol"}).status_code == 404
        assert bob.delete("/api/documents/d1").status_code == 404
        assert bob.delete("/api/documents/d1/share/alice").status_code == 404

    def test_rejects_self_unknown_user_and_missing_login(self, test_app, users):
        alice = self._client(test_app, "alice")

        assert alice.post("/api/documents/d1/share", json={"login": "alice"}).status_code == 400
        assert alice.post("/api/documents/d1/share", json={"login": "nobody"}).status_code == 404
        assert alice.post("/api/documents/d1/share", json={}).status_code == 400

    def test_shareable_users_excludes_the_caller_and_the_admin(self, test_app, users):
        alice = self._client(test_app, "alice")
        users = alice.get("/api/documents/shareable-users").get_json()["users"]
        assert "alice" not in users
        assert "bob" in users
        # The admin account has no chats or documents of its own.
        assert "admin" not in users


class TestShareDialog:
    """The picker is one checkbox per user, not a one-at-a-time add."""

    def test_dialog_renders_a_checkbox_list(self):
        source = (Path(__file__).resolve().parents[1] / "app/static/js/chat-documents.js").read_text(encoding="utf-8")
        assert "doc-share-option" in source
        assert "doc-share-check" in source
        assert "type = 'checkbox'" in source
        assert "documents_share_add" not in source
        assert "doc-share-select" not in source

    def test_owner_sees_a_counter_badge(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "app/static/js/chat-documents.js").read_text(encoding="utf-8")
        assert "document-share-count" in source
        assert "documents_shared_with" in source
        template = (root / "app/templates/chat.html").read_text(encoding="utf-8")
        assert "'documents_shared_with'" in template

    def test_admin_is_not_offered(self):
        source = (Path(__file__).resolve().parents[1] / "app/routes/documents.py").read_text(encoding="utf-8")
        assert 'other != "admin"' in source


class TestSearchScope:
    def test_own_collection_plus_each_sharing_owner(self):
        from modules.rag import RagModule

        module = RagModule.__new__(RagModule)
        with patch(
            "modules.rag.get_shared_document_ids",
            return_value={"d2": "carol", "d3": "dave", "d4": "carol"},
        ):
            scopes = module._search_scopes("bob", None)

        assert scopes[0] == ("bob", None)
        assert ("carol", ["d2", "d4"]) in scopes
        assert ("dave", ["d3"]) in scopes

    def test_attachment_scope_is_split_by_owner(self):
        from modules.rag import RagModule

        module = RagModule.__new__(RagModule)
        with patch("modules.rag.get_shared_document_ids", return_value={"d2": "carol"}):
            scopes = module._search_scopes("bob", ["d1", "d2"])

        assert ("bob", ["d1"]) in scopes
        assert ("carol", ["d2"]) in scopes

    def test_scope_with_only_shared_documents(self):
        from modules.rag import RagModule

        module = RagModule.__new__(RagModule)
        with patch("modules.rag.get_shared_document_ids", return_value={"d2": "carol"}):
            scopes = module._search_scopes("bob", ["d2"])

        assert scopes == [("carol", ["d2"])]

    def test_no_sharing_means_one_scope(self):
        from modules.rag import RagModule

        module = RagModule.__new__(RagModule)
        with patch("modules.rag.get_shared_document_ids", return_value={}):
            assert module._search_scopes("bob", None) == [("bob", None)]


class TestRecallHygiene:
    def test_imported_messages_are_not_injected(self):
        from modules.base import BaseModule

        facts = [
            {"content": "raw imported question?", "score": 0.9, "source": "auto_import"},
            {"content": "Prefers dark themes", "score": 0.8, "source": "llm_extract"},
            {"content": "profile block", "kind": "profile", "score": 0.1, "source": "profile_consolidation"},
        ]
        selected = BaseModule.select_recalled_facts(facts, limit=7, min_score=0.5)
        assert [f["content"] for f in selected] == ["profile block", "Prefers dark themes"]

    def test_source_in_metadata_is_honoured(self):
        from modules.base import BaseModule

        facts = [{"content": "raw", "score": 0.9, "metadata": {"source": "auto_import"}}]
        assert BaseModule.select_recalled_facts(facts, limit=7, min_score=0.5) == []

    def test_fact_without_provenance_is_not_injected(self):
        from modules.base import BaseModule

        facts = [{"content": "legacy import", "score": 0.9}]
        assert BaseModule.select_recalled_facts(facts, limit=7, min_score=0.5) == []

    def test_explicitly_remembered_fact_is_injected(self):
        from modules.base import BaseModule

        facts = [{"content": "Call me Ivan", "score": 0.9, "source": "user_request"}]
        assert len(BaseModule.select_recalled_facts(facts, limit=7, min_score=0.5)) == 1


class TestUserName:
    def test_web_turn_carries_the_account_name(self):
        from app.queue import RedisRequestQueue

        with patch("app.userdb.get_display_name", return_value="Иван"):
            assert RedisRequestQueue._user_name_for_task({"user_id": "ivan", "data": {}}) == "Иван"

    def test_api_turn_never_carries_it(self):
        from app.queue import RedisRequestQueue

        with patch("app.userdb.get_display_name", return_value="Колонка") as lookup:
            assert RedisRequestQueue._user_name_for_task({"user_id": "col", "data": {"api_request": True}}) == ""
        lookup.assert_not_called()

    def test_context_line_is_localized(self):
        from modules.base import BaseModule

        assert BaseModule._user_section("ru", "Иван") == "\nИмя пользователя: Иван."
        assert BaseModule._user_section("en", "Ivan") == "\nThe user's name is Ivan."
        assert BaseModule._user_section("ru", "") == ""
