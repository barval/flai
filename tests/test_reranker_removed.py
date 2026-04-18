# tests/test_reranker.py
"""Tests for llama.cpp reranking integration in RAG module."""
import pytest
from unittest.mock import Mock, patch, MagicMock


@pytest.mark.unit
class TestLlamaCppRerank:
    """Test cases for LlamaCppClient.rerank() method."""

    def test_rerank_no_config(self):
        """Test rerank returns None when no model config."""
        from app.llamacpp_client import LlamaCppClient

        with patch('app.llamacpp_client.get_model_config', return_value=None):
            client = LlamaCppClient()
            result = client.rerank("test query", ["doc1", "doc2"])
            assert result is None

    def test_rerank_no_model_name(self):
        """Test rerank returns None when model name not configured."""
        from app.llamacpp_client import LlamaCppClient

        with patch('app.llamacpp_client.get_model_config', return_value={'model_name': None}):
            client = LlamaCppClient()
            result = client.rerank("test query", ["doc1", "doc2"])
            assert result is None

    def test_rerank_successful(self):
        """Test successful reranking via llama.cpp."""
        from app.llamacpp_client import LlamaCppClient

        with patch('app.llamacpp_client.get_model_config', return_value={
            'model_name': 'bge-reranker-v2-m3-Q4_K_M',
            'service_url': 'http://flai-llamacpp:8033',
            'timeout': 120,
        }):
            with patch('app.llamacpp_client.requests.post') as mock_post:
                mock_post.return_value = Mock(
                    status_code=200,
                    json=Mock(return_value={
                        'results': [
                            {'index': 2, 'relevance_score': 8.5},
                            {'index': 0, 'relevance_score': 5.2},
                            {'index': 1, 'relevance_score': 1.1},
                        ]
                    })
                )

                client = LlamaCppClient()
                result = client.rerank("test query", ["doc1", "doc2", "doc3"])

                assert result is not None
                assert len(result) == 3
                assert result[0]['relevance_score'] == 8.5
                assert result[0]['index'] == 2

    def test_rerank_with_top_n(self):
        """Test rerank with top_n parameter."""
        from app.llamacpp_client import LlamaCppClient

        with patch('app.llamacpp_client.get_model_config', return_value={
            'model_name': 'bge-reranker-v2-m3-Q4_K_M',
            'service_url': 'http://flai-llamacpp:8033',
            'timeout': 120,
        }):
            with patch('app.llamacpp_client.requests.post') as mock_post:
                mock_post.return_value = Mock(
                    status_code=200,
                    json=Mock(return_value={
                        'results': [
                            {'index': 0, 'relevance_score': 9.0},
                            {'index': 1, 'relevance_score': 7.5},
                        ]
                    })
                )

                client = LlamaCppClient()
                result = client.rerank("test query", ["doc1", "doc2", "doc3"], top_n=2)

                assert result is not None
                assert len(result) == 2
                # Check payload included top_n
                call_args = mock_post.call_args
                assert call_args[1]['json']['top_n'] == 2

    def test_rerank_api_error(self):
        """Test rerank returns None on API error."""
        from app.llamacpp_client import LlamaCppClient

        with patch('app.llamacpp_client.get_model_config', return_value={
            'model_name': 'bge-reranker-v2-m3-Q4_K_M',
            'service_url': 'http://flai-llamacpp:8033',
            'timeout': 120,
        }):
            with patch('app.llamacpp_client.requests.post') as mock_post:
                mock_post.return_value = Mock(status_code=500, text="Internal error")

                client = LlamaCppClient()
                result = client.rerank("test query", ["doc1", "doc2"])
                assert result is None

    def test_rerank_timeout(self):
        """Test rerank returns None on timeout."""
        from app.llamacpp_client import LlamaCppClient

        with patch('app.llamacpp_client.get_model_config', return_value={
            'model_name': 'bge-reranker-v2-m3-Q4_K_M',
            'service_url': 'http://flai-llamacpp:8033',
            'timeout': 120,
        }):
            with patch('app.llamacpp_client.requests.post') as mock_post:
                import requests
                mock_post.side_effect = requests.exceptions.Timeout()

                client = LlamaCppClient()
                result = client.rerank("test query", ["doc1", "doc2"])
                assert result is None

    def test_rerank_empty_results(self):
        """Test rerank returns empty list when no results."""
        from app.llamacpp_client import LlamaCppClient

        with patch('app.llamacpp_client.get_model_config', return_value={
            'model_name': 'bge-reranker-v2-m3-Q4_K_M',
            'service_url': 'http://flai-llamacpp:8033',
            'timeout': 120,
        }):
            with patch('app.llamacpp_client.requests.post') as mock_post:
                mock_post.return_value = Mock(
                    status_code=200,
                    json=Mock(return_value={'results': []})
                )

                client = LlamaCppClient()
                result = client.rerank("test query", ["doc1"])
                assert result == []


@pytest.mark.unit
class TestRagReranking:
    """Test cases for RAG reranking integration via llama.cpp."""

    @pytest.fixture
    def mock_app(self):
        """Create mock Flask app with full RAG config."""
        app = Mock()
        app.config = {
            'QDRANT_URL': 'http://test-qdrant:6333',
            'QDRANT_API_KEY': 'test-api-key',
            'RAG_CHUNK_SIZE': 500,
            'RAG_CHUNK_OVERLAP': 50,
            'RAG_TOP_K': 20,
            'RERANKER_ENABLED': True,
            'RERANK_TOP_K': 10,
        }
        app.logger = Mock()
        return app

    def test_rerank_results_sorted_by_new_score(self, mock_app):
        """Test that reranking sorts results by new scores."""
        from modules.rag import RagModule

        with patch('modules.rag.QdrantClient') as mock_qdrant:
            with patch('app.llamacpp_client.get_model_config', return_value={
                'model_name': 'bge-reranker-v2-m3-Q4_K_M',
                'service_url': 'http://flai-llamacpp:8033',
                'timeout': 120,
            }):
                mock_qdrant.return_value.get_collections.return_value = MagicMock(collections=[])

                module = RagModule(mock_app)
                module.available = True
                module.reranker_enabled = True

                # Mock llama.cpp rerank
                def mock_rerank(query, texts, top_n=None):
                    return [
                        {'index': i, 'relevance_score': 10.0 - i}
                        for i in range(len(texts))
                    ]

                module.llamacpp.rerank = Mock(side_effect=mock_rerank)

                chunks = [
                    {"text": f"chunk {i}", "doc_id": "doc1"}
                    for i in range(5)
                ]
                original_scores = [0.9, 0.8, 0.7, 0.6, 0.5]

                reranked_chunks, reranked_scores = module._rerank_results(
                    "test query", chunks, original_scores
                )

                # Should be sorted by new score descending
                assert len(reranked_scores) == 5
                assert reranked_scores[0] > reranked_scores[1] > reranked_scores[2]

    def test_rerank_limits_to_rerank_top_k(self, mock_app):
        """Test that reranking limits results to RERANK_TOP_K."""
        from modules.rag import RagModule

        mock_app.config['RERANK_TOP_K'] = 3

        with patch('modules.rag.QdrantClient') as mock_qdrant:
            with patch('app.llamacpp_client.get_model_config', return_value={
                'model_name': 'bge-reranker-v2-m3-Q4_K_M',
                'service_url': 'http://flai-llamacpp:8033',
                'timeout': 120,
            }):
                mock_qdrant.return_value.get_collections.return_value = MagicMock(collections=[])

                module = RagModule(mock_app)
                module.available = True
                module.reranker_enabled = True
                module.rerank_top_k = 3

                # Mock llama.cpp rerank returns 10 results
                def mock_rerank(query, texts, top_n=None):
                    return [
                        {'index': i, 'relevance_score': 10.0 - i}
                        for i in range(min(len(texts), top_n or 10))
                    ]

                module.llamacpp.rerank = Mock(side_effect=mock_rerank)

                chunks = [
                    {"text": f"chunk {i}", "doc_id": "doc1"}
                    for i in range(10)
                ]
                original_scores = [0.9] * 10

                reranked_chunks, reranked_scores = module._rerank_results(
                    "test query", chunks, original_scores
                )

                # Should be limited to RERANK_TOP_K=3
                assert len(reranked_chunks) == 3
                assert len(reranked_scores) == 3

    def test_rerank_fallback_on_none(self, mock_app):
        """Test that reranking keeps original order when reranker returns None."""
        from modules.rag import RagModule

        with patch('modules.rag.QdrantClient') as mock_qdrant:
            with patch('app.llamacpp_client.get_model_config', return_value={
                'model_name': 'bge-reranker-v2-m3-Q4_K_M',
                'service_url': 'http://flai-llamacpp:8033',
                'timeout': 120,
            }):
                mock_qdrant.return_value.get_collections.return_value = MagicMock(collections=[])

                module = RagModule(mock_app)
                module.available = True
                module.reranker_enabled = True

                # Mock reranker returns None (error)
                module.llamacpp.rerank = Mock(return_value=None)

                chunks = [{"text": "only chunk", "doc_id": "doc1"}]
                original_scores = [0.85]

                reranked_chunks, reranked_scores = module._rerank_results(
                    "test query", chunks, original_scores
                )

                # Should keep original
                assert reranked_chunks == chunks
                assert reranked_scores == original_scores

    def test_rerank_disabled(self, mock_app):
        """Test that reranking is skipped when disabled."""
        from modules.rag import RagModule

        mock_app.config['RERANKER_ENABLED'] = False

        with patch('modules.rag.QdrantClient') as mock_qdrant:
            with patch('app.llamacpp_client.get_model_config', return_value={
                'model_name': 'bge-reranker-v2-m3-Q4_K_M',
                'service_url': 'http://flai-llamacpp:8033',
                'timeout': 120,
            }):
                mock_qdrant.return_value.get_collections.return_value = MagicMock(collections=[])

                module = RagModule(mock_app)
                module.available = True
                module.reranker_enabled = False

                # Should not call rerank at all
                module.llamacpp.rerank = Mock()

                chunks = [{"text": "chunk", "doc_id": "doc1"}]
                original_scores = [0.85]

                reranked_chunks, reranked_scores = module._rerank_results(
                    "test query", chunks, original_scores
                )

                # rerank should not be called
                module.llamacpp.rerank.assert_not_called()
                # Original order preserved
                assert reranked_chunks == chunks
                assert reranked_scores == original_scores
