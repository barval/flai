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

    def test_chunk_text_splits_correctly(self, mock_app):
        """Test that chunk_text splits text into overlapping chunks."""
        from modules.rag import RagModule
        
        with patch('modules.rag.QdrantClient') as mock_client:
            mock_client.return_value.get_collections.return_value = MagicMock(collections=[])
            
            module = RagModule(mock_app)
            
            text = "This is a test. " * 50  # 800 characters
            chunks = module.chunk_text(text, chunk_size=10, overlap=2)
            
            # Should have multiple chunks
            assert len(chunks) > 1
            # Each chunk should be roughly chunk_size words
            for chunk in chunks:
                assert len(chunk.split()) <= 15  # Some flexibility

    def test_chunk_text_handles_empty_input(self, mock_app):
        """Test that chunk_text handles empty input."""
        from modules.rag import RagModule
        
        with patch('modules.rag.QdrantClient') as mock_client:
            mock_client.return_value.get_collections.return_value = MagicMock(collections=[])
            
            module = RagModule(mock_app)
            
            chunks = module.chunk_text('', chunk_size=10, overlap=2)
            assert chunks == []
            
            chunks = module.chunk_text(None, chunk_size=10, overlap=2)
            assert chunks == []

    def test_search_returns_results(self, mock_app):
        """Test that search returns results when available."""
        from modules.rag import RagModule
        
        with patch('modules.rag.QdrantClient') as mock_client:
            mock_collection = MagicMock()
            mock_collection.name = 'test_collection'
            mock_client.return_value.get_collections.return_value = MagicMock(
                collections=[mock_collection]
            )
            
            # Mock search results
            mock_scored_point = MagicMock()
            mock_scored_point.score = 0.8
            mock_scored_point.payload = {'text': 'Test result', 'source': 'test.pdf'}
            mock_client.return_value.search.return_value = [mock_scored_point]
            
            module = RagModule(mock_app)
            
            results = module.search('test query', 'test_collection')
            
            assert results is not None
            assert len(results) > 0

    def test_search_with_threshold_filtering(self, mock_app):
        """Test that search filters results by relevance threshold."""
        from modules.rag import RagModule
        
        with patch('modules.rag.QdrantClient') as mock_client:
            mock_collection = MagicMock()
            mock_collection.name = 'test_collection'
            mock_client.return_value.get_collections.return_value = MagicMock(
                collections=[mock_collection]
            )
            
            # Mock search results with varying scores
            mock_point_high = MagicMock()
            mock_point_high.score = 0.9
            mock_point_high.payload = {'text': 'High relevance'}
            
            mock_point_low = MagicMock()
            mock_point_low.score = 0.1
            mock_point_low.payload = {'text': 'Low relevance'}
            
            mock_client.return_value.search.return_value = [
                mock_point_high, mock_point_low
            ]
            
            module = RagModule(mock_app)
            
            # With default threshold (0.3), low relevance should be filtered
            results = module.search('test query', 'test_collection')
            
            # Should have filtered out low relevance results
            assert all(r['score'] >= 0.3 for r in results)

    def test_index_document_splits_and_stores(self, mock_app):
        """Test that index_document splits text and stores embeddings."""
        from modules.rag import RagModule
        
        with patch('modules.rag.QdrantClient') as mock_client:
            mock_collection = MagicMock()
            mock_collection.name = 'test_collection'
            mock_client.return_value.get_collections.return_value = MagicMock(
                collections=[mock_collection]
            )
            mock_client.return_value.upsert.return_value = MagicMock(status='completed')
            
            module = RagModule(mock_app)
            
            # Mock embedding response
            mock_client.return_value.query_points.return_value = MagicMock(
                points=[]
            )
            
            result = module.index_document(
                collection_name='test_collection',
                document_id='doc-123',
                text='Test document content. ' * 20,
                metadata={'source': 'test.pdf'}
            )
            
            assert result is True
