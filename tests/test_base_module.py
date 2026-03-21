# tests/test_base_module.py
import pytest
from modules.base import BaseModule
from unittest.mock import Mock, patch, MagicMock


@pytest.fixture
def base_module(app):
    module = BaseModule(app)
    # Override call_ollama to avoid real network calls
    module.call_ollama = Mock(return_value="[-REASONING-] test query")
    return module


def test_parse_router_response_no_marker(base_module):
    """Test parsing when no special marker present."""
    response = "Some plain text"
    result = base_module._parse_router_response(response, "original", "time")
    assert result['action'] == 'none'
    assert result['query'] == "Some plain text"
    assert not result['needs_reasoning']


def test_parse_router_response_image_marker(base_module):
    response = "Some text [-IMAGE-] draw cat"
    result = base_module._parse_router_response(response, "draw cat", "time")
    assert result['action'] == 'image'
    assert result['query'] == "draw cat"
    assert not result['needs_reasoning']


def test_parse_router_response_reasoning_marker(base_module):
    response = "[-REASONING-] compute 2+2"
    result = base_module._parse_router_response(response, "2+2", "time")
    assert result['action'] == 'reasoning'
    assert result['query'] == "compute 2+2"
    assert result['needs_reasoning'] is True


def test_parse_router_response_camera_marker(base_module):
    response = "[-CAMERA-] show kitchen"
    result = base_module._parse_router_response(response, "show kitchen", "time")
    assert result['action'] == 'camera'
    assert result['query'] == "show kitchen"


def test_parse_router_response_rag_marker(base_module):
    response = "[-RAG-] find in my documents about AI"
    result = base_module._parse_router_response(response, "find in my documents about AI", "time")
    assert result['action'] == 'rag'
    assert result['query'] == "find in my documents about AI"


def test_parse_router_response_none(base_module):
    result = base_module._parse_router_response(None, "query", "time")
    assert result['error'] is not None


@patch('modules.base.get_model_config')
def test_get_model_config_returns_config(mock_get_config, base_module):
    mock_get_config.return_value = {'model_name': 'test', 'context_length': 4096}
    config = base_module._get_model_config('chat')
    assert config['model_name'] == 'test'