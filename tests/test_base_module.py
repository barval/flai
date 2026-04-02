# tests/test_base_module.py
"""Unit tests for base module."""
import pytest
from unittest.mock import patch, MagicMock


@pytest.mark.unit
class TestBaseModule:
    """Test base module functionality."""

    @pytest.fixture
    def mock_app(self):
        """Create mock Flask app."""
        app = MagicMock()
        app.config = {
            'OLLAMA_URL': 'http://test:11434',
            'LLM_CHAT_MODEL': 'test-model',
            'LLM_CHAT_TEMPERATURE': 0.7,
            'LLM_CHAT_TOP_P': 0.9,
            'LLM_CHAT_TIMEOUT': 300
        }
        app.logger = MagicMock()
        return app

    @pytest.fixture
    def base_module(self, mock_app):
        """Create base module instance."""
        from modules.base import BaseModule
        with patch('modules.base.OllamaClient'):
            module = BaseModule(mock_app)
            return module

    @pytest.mark.unit
    def test_parse_router_response_no_marker(self, base_module):
        """Test parsing response without markers."""
        response = "This is a normal response"
        action, query = base_module._parse_router_response(response)
        assert action == 'none'
        assert query == response

    @pytest.mark.unit
    def test_parse_router_response_image_marker(self, base_module):
        """Test parsing response with image marker."""
        response = "[-IMAGE-] draw a cat"
        action, query = base_module._parse_router_response(response)
        assert action == 'image'
        assert query == 'draw a cat'

    @pytest.mark.unit
    def test_parse_router_response_reasoning_marker(self, base_module):
        """Test parsing response with reasoning marker."""
        response = "[-REASONING-] solve this problem"
        action, query = base_module._parse_router_response(response)
        assert action == 'reasoning'
        assert query == 'solve this problem'

    @pytest.mark.unit
    def test_parse_router_response_camera_marker(self, base_module):
        """Test parsing response with camera marker."""
        response = "[-CAMERA-] show kitchen"
        action, query = base_module._parse_router_response(response)
        assert action == 'camera'
        assert query == 'show kitchen'

    @pytest.mark.unit
    def test_parse_router_response_rag_marker(self, base_module):
        """Test parsing response with RAG marker."""
        response = "[-RAG-] search documents"
        action, query = base_module._parse_router_response(response)
        assert action == 'rag'
        assert query == 'search documents'

    @pytest.mark.unit
    def test_parse_router_response_none(self, base_module):
        """Test parsing None response."""
        action, query = base_module._parse_router_response(None)
        assert action == 'none'
        assert query == ''

    @pytest.mark.integration
    def test_get_model_config_returns_config(self, test_app):
        """Test getting model configuration."""
        with test_app.app_context():
            from modules.base import BaseModule
            from app.model_config import get_model_config

            module = BaseModule(test_app)
            config = module._get_model_config('chat')

            assert config is not None
            assert 'model_name' in config
