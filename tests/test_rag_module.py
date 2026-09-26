# tests/test_rag_module.py
"""Tests for RAG module (Qdrant vector search)."""

from unittest.mock import MagicMock, Mock, patch

import pytest


@pytest.mark.unit
class TestRagModule:
    """Test cases for RagModule class."""

    @pytest.fixture
    def mock_app(self):
        """Create mock Flask app."""
        app = Mock()
        app.config = {
            "QDRANT_URL": "http://test-qdrant:6333",
            "QDRANT_API_KEY": "test-api-key",
            "RAG_CHUNK_SIZE": 500,
            "RAG_CHUNK_OVERLAP": 50,
            "RAG_TOP_K": 15,
            "RAG_RELEVANCE_THRESHOLD_DEFAULT": 0.3,
            "RAG_RELEVANCE_THRESHOLD_REASONING": 0.7,
        }
        app.logger = Mock()
        return app

    def test_init_with_available_qdrant(self, mock_app):
        """Test module initialization when Qdrant is available."""
        from modules.rag import RagModule

        with patch("modules.rag.QdrantClient") as mock_client:
            mock_client.return_value.get_collections.return_value = MagicMock(collections=[])

            module = RagModule(mock_app)

            assert module.available is True
            assert module.chunk_size == 500
            assert module.chunk_overlap == 50
            assert module.top_k == 15

    def test_init_with_unavailable_qdrant(self, mock_app):
        """Test module initialization when Qdrant is unavailable."""
        from modules.rag import RagModule

        with patch("modules.rag.QdrantClient") as mock_client:
            mock_client.side_effect = Exception("Connection error")

            module = RagModule(mock_app)

            assert module.available is False

    def test_get_collection_name_returns_formatted_name(self, mock_app):
        """Test get_collection_name returns properly formatted collection name."""
        from modules.rag import RagModule

        with patch("modules.rag.QdrantClient") as mock_client:
            mock_client.return_value.get_collections.return_value = MagicMock(collections=[])

            module = RagModule(mock_app)

            collection_name = module._get_collection_name("testuser")

            assert collection_name is not None
            assert "testuser" in collection_name.lower()

    def test_build_context_prompt(self, mock_app):
        """Test _build_context_prompt creates proper prompt."""
        from modules.rag import RagModule

        with patch("modules.rag.QdrantClient") as mock_client:
            mock_client.return_value.get_collections.return_value = MagicMock(collections=[])

            module = RagModule(mock_app)

            history = [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi there"}]
            prompt = module._build_context_prompt(history, lang="en")

            assert prompt is not None
            assert len(prompt) > 0

    def test_get_embedding_retries_on_transient_failure(self, mock_app):
        """A transient llama-swap failure (e.g. 'group is shutting down') must
        not silently kill the RAG search: _get_embedding retries once before
        giving up."""
        import time as time_module

        from modules.rag import RagModule

        with patch("modules.rag.QdrantClient") as mock_client:
            mock_client.return_value.get_collections.return_value = MagicMock(collections=[])

            module = RagModule(mock_app)
            module.llamacpp = Mock()
            module.llamacpp.get_embeddings.side_effect = [None, [[0.1, 0.2]]]
            module.logger = Mock()
            sleep_calls = []

            with patch.object(time_module, "sleep", side_effect=lambda s: sleep_calls.append(s)):
                emb = module._get_embedding("query text")

            assert emb == [0.1, 0.2]
            assert module.llamacpp.get_embeddings.call_count == 2
            assert sleep_calls == [2]

    def test_get_embedding_returns_none_after_single_failure_when_retry_also_fails(self, mock_app):
        from modules.rag import RagModule

        with patch("modules.rag.QdrantClient") as mock_client:
            mock_client.return_value.get_collections.return_value = MagicMock(collections=[])

            module = RagModule(mock_app)
            module.llamacpp = Mock()
            module.llamacpp.get_embeddings.side_effect = [None, None]
            module.logger = Mock()
            import time as time_module

            with patch.object(time_module, "sleep"):
                emb = module._get_embedding("query text")
            assert emb is None
            assert module.llamacpp.get_embeddings.call_count == 2

    def _make_search_module(self, mock_app):
        from modules.rag import RagModule

        with patch("modules.rag.QdrantClient") as mock_client:
            mock_client.return_value.get_collections.return_value = MagicMock(collections=[])
            module = RagModule(mock_app)
        module.available = True
        module.top_k = 4
        module._get_embedding = Mock(return_value=[0.1])
        module.logger = Mock()
        return module

    @staticmethod
    def _hit(doc_id, filename, text, score):
        hit = Mock()
        hit.payload = {"doc_id": doc_id, "filename": filename, "text": text}
        hit.score = score
        return hit

    def test_search_default_does_not_query_documents(self, mock_app):
        """File-coverage is opt-in: without file_coverage=True search must not
        hit the documents table."""
        module = self._make_search_module(mock_app)
        module.qdrant_client = MagicMock()
        module.qdrant_client.query_points.return_value = MagicMock(points=[self._hit("doc1", "f1", "text", 0.9)])
        with patch("modules.rag.get_user_documents") as mock_docs:
            chunks, scores = module.search("user", "query", top_k=4)
        assert len(chunks) == 1
        mock_docs.assert_not_called()
        assert chunks[0].get("coverage") is None

    def test_search_file_coverage_appends_missing_docs(self, mock_app):
        """A document absent from the semantic top-k gets its best chunk
        appended, marked with coverage=True, when file_coverage is enabled."""
        module = self._make_search_module(mock_app)
        fake_client = MagicMock()
        fake_client.query_points.side_effect = [
            MagicMock(
                points=[
                    self._hit("doc1", "f1", "text a", 0.9),
                    self._hit("doc1", "f1", "text b", 0.8),
                    self._hit("doc2", "f2", "text c", 0.7),
                ]
            ),
            MagicMock(points=[self._hit("doc3", "f3", "text d", 0.4)]),
        ]
        module.qdrant_client = fake_client

        with patch(
            "modules.rag.get_user_documents",
            return_value=[
                {"id": "doc1", "index_status": "indexed"},
                {"id": "doc2", "index_status": "indexed"},
                {"id": "doc3", "index_status": "indexed"},
            ],
        ):
            chunks, scores = module.search("user", "query", top_k=4, file_coverage=True)

        assert [c["doc_id"] for c in chunks] == ["doc1", "doc1", "doc2", "doc3"]
        assert chunks[-1]["coverage"] is True
        assert scores == [0.9, 0.8, 0.7, 0.4]
        assert fake_client.query_points.call_count == 2

    def test_search_file_coverage_skipped_when_too_many_missing(self, mock_app):
        """Coverage is bounded: when more than coverage_max_files documents are
        missing from the top-k, no extra per-document queries are issued."""
        module = self._make_search_module(mock_app)
        fake_client = MagicMock()
        fake_client.query_points.return_value = MagicMock(points=[self._hit("doc1", "f1", "text", 0.9)])
        module.qdrant_client = fake_client

        docs = [{"id": f"doc{i}", "index_status": "indexed"} for i in range(1, 9)]
        with patch("modules.rag.get_user_documents", return_value=docs):
            chunks, scores = module.search("user", "query", top_k=4, file_coverage=True)

        assert len(chunks) == 1
        assert fake_client.query_points.call_count == 1
