# tests/test_rag_filters.py
"""Search scope: filters resolve to document ids, and an empty scope stays empty."""

from datetime import UTC, datetime, timedelta

import pytest

from app.rag_filters import resolve_scope

pytestmark = pytest.mark.requires_db

FOLDER = "folder-1"


def _seed() -> dict[str, str]:
    from app.database import get_db

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM documents WHERE user_id = 'scopeuser'")
        now = datetime.now(UTC).replace(tzinfo=None)
        rows = {
            "d-old": ("old.pdf", None, now - timedelta(days=40)),
            "d-folder-1": ("letter.pdf", FOLDER, now - timedelta(days=5)),
            "d-folder-2": ("notes.txt", "folder-2", now - timedelta(days=1)),
            "d-recent": ("scan.pdf", None, now - timedelta(hours=2)),
        }
        for doc_id, (filename, folder, uploaded) in rows.items():
            cursor.execute(
                "INSERT INTO documents (id, user_id, filename, file_ext, file_size, file_path, uploaded_at, folder_id) "
                "VALUES (%s, 'scopeuser', %s, %s, 10, %s, %s, %s)",
                (
                    doc_id,
                    filename,
                    "." + filename.rsplit(".", 1)[-1],
                    f"scopeuser/{doc_id}",
                    uploaded,
                    folder,
                ),
            )
    return {doc_id: doc_id for doc_id in rows}


def test_no_scope_means_no_filter(test_app):
    with test_app.app_context():
        assert resolve_scope("scopeuser", None) is None
        assert resolve_scope("scopeuser", {}) is None


def test_folder_scope_selects_that_folder(test_app):
    with test_app.app_context():
        _seed()

        assert resolve_scope("scopeuser", {"folder": FOLDER}) == ["d-folder-1"]
        assert resolve_scope("scopeuser", {"folder": ""}) == ["d-old", "d-recent"]


def test_date_range_is_inclusive_on_both_ends(test_app):
    with test_app.app_context():
        _seed()
        today = datetime.now(UTC).date()

        recent = resolve_scope("scopeuser", {"from": str(today - timedelta(days=2)), "to": str(today)})

        assert sorted(recent) == ["d-folder-2", "d-recent"]


def test_extension_and_folder_combine_with_and(test_app):
    with test_app.app_context():
        _seed()

        found = resolve_scope("scopeuser", {"folder": "", "ext": ["pdf"]})

        assert sorted(found) == ["d-old", "d-recent"]


def test_a_scope_that_matches_nothing_returns_an_empty_list(test_app):
    with test_app.app_context():
        _seed()

        assert resolve_scope("scopeuser", {"folder": "missing-folder"}) == []


@pytest.mark.unit
def test_an_empty_scope_stops_the_search(monkeypatch):
    """An empty scope must not silently fall back to the whole corpus."""
    from modules.rag import RagModule

    module = RagModule()
    monkeypatch.setattr(module, "check_availability", lambda: True)

    def boom(_query):
        raise AssertionError("the embedding must not run for an empty scope")

    monkeypatch.setattr(module, "_get_embedding", boom)

    assert module.search("user", "query", doc_ids=[]) == ([], [])
