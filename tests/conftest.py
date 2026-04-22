# tests/conftest.py
"""
Pytest fixtures and configuration for FLAI tests.

Provides isolated test environments with mocked external services.
"""
import pytest
import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch, Mock
from flask import Flask

from app import create_app
from app.db import init_db, CHAT_DB_PATH
from app.userdb import init_user_db


def create_mock_redis():
    """Create a mock Redis client."""
    mock_redis = MagicMock()
    mock_redis.blpop.return_value = None
    mock_redis.llen.return_value = 0
    mock_redis.hlen.return_value = 0
    mock_redis.rpush.return_value = 1
    mock_redis.scard.return_value = 0
    mock_redis.sadd.return_value = 1
    mock_redis.srem.return_value = 1
    mock_redis.hset.return_value = 1
    mock_redis.hdel.return_value = 1
    mock_redis.hgetall.return_value = {}
    mock_redis.hget.return_value = None
    mock_redis.lrange.return_value = []
    mock_redis.smembers.return_value = set()
    mock_redis.ping.return_value = True
    mock_redis.expire.return_value = True
    return mock_redis


def create_mock_llamacpp():
    """Create a mock llama-server client (LlamaCppClient)."""
    mock_client = MagicMock()
    mock_client.chat.return_value = 'Test response from llama-server'
    mock_client.call.return_value = 'Test response from llama-server'
    mock_client.check_availability.return_value = True
    mock_client.list_models.return_value = ['model1.gguf', 'model2.gguf']
    mock_client.get_model_info.return_value = {
        'architecture': 'qwen3',
        'parameters': '4B',
        'quantization': 'Q4_K_M',
        'context_length': 32768,
        'embedding_length': 4096
    }
    mock_client.get_embeddings.return_value = [[0.1] * 1024]
    mock_client.available = True
    return mock_client


def create_mock_ollama():
    """Create a mock llama-server client (alias for backward compatibility)."""
    return create_mock_llamacpp()


def create_mock_qdrant():
    """Create a mock Qdrant client."""
    mock_client = MagicMock()
    mock_client.search.return_value = []
    mock_client.get_collections.return_value = MagicMock(collections=[])
    return mock_client


@pytest.fixture(scope='function')
def test_app():
    """
    Create Flask app with test configuration.

    Each test gets its own isolated app instance with temporary databases.
    External services (Redis, llama-server, Qdrant) are mocked.
    """
    # Create temporary directory for test databases
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, 'test_chats.db')
    user_db_path = os.path.join(temp_dir, 'test_users.db')

    # Set environment variables BEFORE create_app() is called
    os.environ['SECRET_KEY'] = 'test-secret-key-for-testing-only'
    os.environ['REDIS_URL'] = 'redis://localhost:6379/0'
    os.environ['LLAMACPP_URL'] = 'http://localhost:8080'
    os.environ['WHISPER_API_URL'] = 'http://localhost:9000/asr'
    os.environ['SD_CPP_URL'] = 'http://localhost:7860'
    os.environ['PIPER_URL'] = 'http://localhost:8888/tts'
    os.environ['QDRANT_URL'] = 'http://localhost:6333'

    # Create mocks for external services
    mock_redis = create_mock_redis()
    mock_llamacpp = create_mock_llamacpp()
    mock_qdrant = create_mock_qdrant()

    # Patch external services before creating app
    with patch('redis.from_url', return_value=mock_redis):
        with patch('app.llamacpp_client.LlamaCppClient', return_value=mock_llamacpp):
            with patch('modules.rag.QdrantClient', return_value=mock_qdrant):
                flask_app = create_app()

                # Configure test app
                flask_app.config.update({
                    'TESTING': True,
                    'CHAT_DB_PATH': db_path,
                    'USER_DB_PATH': user_db_path,
                    'WTF_CSRF_ENABLED': False,
                    'RATELIMIT_ENABLED': False,
                    'UPLOAD_FOLDER': os.path.join(temp_dir, 'uploads'),
                    'DOCUMENTS_FOLDER': os.path.join(temp_dir, 'documents'),
                })
                
                # Create upload and documents folders
                os.makedirs(flask_app.config['UPLOAD_FOLDER'], exist_ok=True)
                os.makedirs(flask_app.config['DOCUMENTS_FOLDER'], exist_ok=True)
                
                # Initialize databases within app context
                with flask_app.app_context():
                    init_db()
                    init_user_db()
                
                yield flask_app
    
    # Cleanup: remove temporary directory
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except:
        pass


@pytest.fixture
def client(test_app):
    """
    Flask test client for making HTTP requests.
    
    Usage:
        def test_example(client):
            response = client.get('/health')
            assert response.status_code == 200
    """
    return test_app.test_client()


@pytest.fixture
def runner(test_app):
    """
    CLI runner for testing Flask commands.
    
    Usage:
        def test_cli_command(runner):
            result = runner.invoke(some_command)
            assert result.exit_code == 0
    """
    return test_app.test_cli_runner()


@pytest.fixture
def mock_redis_client():
    """
    Provide access to the mock Redis client for assertions.
    
    Usage:
        def test_redis_operations(client, mock_redis_client):
            client.post('/api/endpoint')
            mock_redis_client.rpush.assert_called()
    """
    with patch('redis.from_url') as mock_redis:
        mock_redis.return_value = create_mock_redis()
        yield mock_redis.return_value


@pytest.fixture
def mock_llamacpp_client():
    """
    Provide access to the mock LlamaCppClient for configuration.

    Usage:
        def test_llamacpp_call(client, mock_llamacpp_client):
            mock_llamacpp_client.chat.return_value = 'Custom response'
            response = client.post('/api/send_message', json={'message': 'Hello'})
    """
    with patch('app.llamacpp_client.LlamaCppClient') as mock_llamacpp:
        mock_llamacpp.return_value = create_mock_llamacpp()
        yield mock_llamacpp.return_value


@pytest.fixture
def mock_ollama_client():
    """
    Alias for mock_llamacpp_client for backward compatibility.
    """
    return mock_llamacpp_client()


@pytest.fixture
def mock_qdrant_client():
    """
    Provide access to the mock Qdrant client for configuration.
    
    Usage:
        def test_rag_search(client, mock_qdrant_client):
            mock_qdrant_client.search.return_value = [mock_result]
            response = client.get('/api/documents/search?q=test')
    """
    with patch('modules.rag.QdrantClient') as mock_qdrant:
        mock_qdrant.return_value = create_mock_qdrant()
        yield mock_qdrant.return_value


# Alias for pytest-flask plugin compatibility
# The plugin expects a fixture named 'app'
@pytest.fixture(scope='function')
def app(test_app):
    """Alias for test_app to support pytest-flask plugin."""
    return test_app
