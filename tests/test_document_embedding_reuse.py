# tests/test_document_embedding_reuse.py
"""Identical document bytes must not be embedded twice."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest


def _module():
    """A RagModule with the Qdrant client and logging stubbed out."""
    from modules.rag import RagModule

    module = RagModule.__new__(RagModule)
    module.logger = Mock()
    module.qdrant_client = Mock()
    module.collection_name_prefix = "user_"
    module._ensure_collection = Mock()
    module._store_chunks = Mock()
    return module


def _point(index, text, doc_id="source"):
    return SimpleNamespace(
        id=f"point-{index}",
        vector=[0.1, 0.2, 0.3],
        payload={"doc_id": doc_id, "user_id": "alice", "chunk_index": index, "text": text,
                 "filename": "old.txt", "file_ext": ".txt"},
    )


DOC_INFO = {"id": "target", "filename": "new.txt", "file_ext": ".txt", "blob_sha256": "abc123"}
SOURCE = {"id": "source", "user_id": "alice", "filename": "old.txt", "embedding_model": "bge-m3"}


@pytest.mark.unit
class TestEmbeddingReuse:
    def test_copies_vectors_under_the_new_document(self):
        module = _module()
        points = [_point(0, "first"), _point(1, "second"), _point(2, "third")]
        module._scroll_document_points = Mock(return_value=points)

        with (
            patch("modules.rag.find_indexed_document_by_blob", return_value=SOURCE),
            patch("modules.rag.get_model_config", return_value={"model_name": "bge-m3"}),
        ):
            result = module._reuse_indexed_content("bob", "target", DOC_INFO)

        assert result == (True, "Indexed 3 chunks (reused embeddings)")
        module._ensure_collection.assert_called_once_with("bob")
        upsert = module.qdrant_client.upsert
        assert upsert.call_count == 1
        kwargs = upsert.call_args.kwargs
        assert kwargs["collection_name"] == "user_bob"
        copies = kwargs["points"]
        assert len(copies) == 3
        assert [c.payload["doc_id"] for c in copies] == ["target"] * 3
        assert [c.payload["user_id"] for c in copies] == ["bob"] * 3
        assert [c.payload["text"] for c in copies] == ["first", "second", "third"]
        assert [c.payload["filename"] for c in copies] == ["new.txt"] * 3
        # Deterministic ids come from the new document, never from the source.
        assert len({c.id for c in copies}) == 3
        assert "point-0" not in {c.id for c in copies}
        module._store_chunks.assert_called_once_with(
            "bob", "target", filename="new.txt", chunks=["first", "second", "third"]
        )
        # The source is never embedded or deleted.
        assert not module.qdrant_client.delete.called

    def test_no_content_hash_indexes_normally(self):
        module = _module()
        assert module._reuse_indexed_content("bob", "target", {"filename": "new.txt"}) is None

    def test_no_twin_indexes_normally(self):
        module = _module()
        with patch("modules.rag.find_indexed_document_by_blob", return_value=None):
            assert module._reuse_indexed_content("bob", "target", DOC_INFO) is None

    def test_different_embedding_model_is_not_reused(self):
        module = _module()
        module._scroll_document_points = Mock()
        with (
            patch("modules.rag.find_indexed_document_by_blob", return_value=SOURCE),
            patch("modules.rag.get_model_config", return_value={"model_name": "other-model"}),
        ):
            assert module._reuse_indexed_content("bob", "target", DOC_INFO) is None
        module._scroll_document_points.assert_not_called()

    def test_twin_without_vectors_indexes_normally(self):
        module = _module()
        module._scroll_document_points = Mock(return_value=[])
        with (
            patch("modules.rag.find_indexed_document_by_blob", return_value=SOURCE),
            patch("modules.rag.get_model_config", return_value={"model_name": "bge-m3"}),
        ):
            assert module._reuse_indexed_content("bob", "target", DOC_INFO) is None

    def test_unreadable_source_indexes_normally(self):
        module = _module()
        module._scroll_document_points = Mock(side_effect=RuntimeError("qdrant down"))
        with (
            patch("modules.rag.find_indexed_document_by_blob", return_value=SOURCE),
            patch("modules.rag.get_model_config", return_value={"model_name": "bge-m3"}),
        ):
            assert module._reuse_indexed_content("bob", "target", DOC_INFO) is None

    def test_failed_upsert_indexes_normally(self):
        module = _module()
        module._scroll_document_points = Mock(return_value=[_point(0, "first")])
        module.qdrant_client.upsert.side_effect = RuntimeError("collection gone")
        with (
            patch("modules.rag.find_indexed_document_by_blob", return_value=SOURCE),
            patch("modules.rag.get_model_config", return_value={"model_name": "bge-m3"}),
        ):
            assert module._reuse_indexed_content("bob", "target", DOC_INFO) is None
        module._store_chunks.assert_not_called()

    def test_points_are_scrolled_in_pages(self):
        module = _module()
        module.qdrant_client.scroll.side_effect = [
            ([_point(0, "first")], "next"),
            ([_point(1, "second")], None),
        ]
        points = module._scroll_document_points("alice", "source")
        assert [p.payload["text"] for p in points] == ["first", "second"]
        assert module.qdrant_client.scroll.call_count == 2
        assert module.qdrant_client.scroll.call_args.kwargs["collection_name"] == "user_alice"


@pytest.mark.unit
def test_index_document_uses_reuse_before_extracting_text():
    """index_document must not extract or embed a document it can copy."""
    from modules.rag import RagModule

    module = RagModule.__new__(RagModule)
    module.logger = Mock()
    module.qdrant_client = Mock()
    module.collection_name_prefix = "user_"
    module._reuse_indexed_content = Mock(return_value=(True, "Indexed 3 chunks (reused embeddings)"))
    module.check_availability = Mock(return_value=True)
    doc_info = {"filename": "new.txt", "file_ext": ".txt", "blob_sha256": "abc123"}

    with (
        patch("modules.rag.get_document", return_value=doc_info),
        patch("modules.rag.extract_text_from_file") as extract,
        patch("modules.rag.chunk_text") as chunk,
    ):
        result = module.index_document("bob", "target", "/documents/new.txt")

    assert result == (True, "Indexed 3 chunks (reused embeddings)")
    module._reuse_indexed_content.assert_called_once_with("bob", "target", doc_info)
    extract.assert_not_called()
    chunk.assert_not_called()
