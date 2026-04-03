# tests/test_rag_module.py
"""Tests for RAG module (Qdrant vector search)."""
import pytest
from unittest.mock import Mock, patch, MagicMock


@pytest.mark.unit
class TestRagModule:
    """Test cases for RagModule class."""

    @pytest.fixture
    def mock_app(self):
        """Create mock Flask app."""
        app = Mock()
        app.config = {
            'QDRANT_URL': 'http://test-qdrant:6333',
            'QDRANT_API_KEY': 'test-api-key',
            'RAG_CHUNK_SIZE': 500,
            'RAG_CHUNK_OVERLAP': 50,
            'RAG_TOP_K': 15,
            'RAG_RELEVANCE_THRESHOLD_DEFAULT': 0.3,
            'RAG_RELEVANCE_THRESHOLD_REASONING': 0.7
        }
        app.logger = Mock()
        return app

    def test_init_with_available_qdrant(self, mock_app):
        """Test module initialization when Qdrant is available."""
        from modules.rag import RagModule

        with patch('modules.rag.QdrantClient') as mock_client:
            mock_client.return_value.get_collections.return_value = MagicMock(collections=[])

            module = RagModule(mock_app)

            assert module.available is True
            assert module.chunk_size == 500
            assert module.chunk_overlap == 50
            assert module.top_k == 15

    def test_init_with_unavailable_qdrant(self, mock_app):
        """Test module initialization when Qdrant is unavailable."""
        from modules.rag import RagModule

        with patch('modules.rag.QdrantClient') as mock_client:
            mock_client.side_effect = Exception("Connection error")

            module = RagModule(mock_app)

            assert module.available is False

    def test_get_collection_name_returns_formatted_name(self, mock_app):
        """Test get_collection_name returns properly formatted collection name."""
        from modules.rag import RagModule

        with patch('modules.rag.QdrantClient') as mock_client:
            mock_client.return_value.get_collections.return_value = MagicMock(collections=[])

            module = RagModule(mock_app)

            collection_name = module._get_collection_name('testuser')

            assert collection_name is not None
            assert 'testuser' in collection_name.lower()

    def test_build_context_prompt(self, mock_app):
        """Test _build_context_prompt creates proper prompt."""
        from modules.rag import RagModule

        with patch('modules.rag.QdrantClient') as mock_client:
            mock_client.return_value.get_collections.return_value = MagicMock(collections=[])

            module = RagModule(mock_app)

            history = [
                {'role': 'user', 'content': 'Hello'},
                {'role': 'assistant', 'content': 'Hi there'}
            ]
            prompt = module._build_context_prompt(history, lang='en')

            assert prompt is not None
            assert len(prompt) > 0
