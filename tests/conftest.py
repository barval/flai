# tests/conftest.py
"""Pytest fixtures and configuration."""
import pytest
import os
import tempfile
from flask import Flask
from flask import current_app
from unittest.mock import MagicMock, patch
from app import create_app
from app.db import init_db, CHAT_DB_PATH
from app.userdb import init_user_db


@pytest.fixture(scope='module')
def test_app():
    """Create Flask app with test configuration."""
    # Create temporary database files
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, 'test_chats.db')
    user_db_path = os.path.join(temp_dir, 'test_users.db')

    # Mock Redis to avoid connection errors
    with patch('redis.from_url') as mock_redis:
        mock_redis.return_value = MagicMock()
        
        flask_app = create_app()
        flask_app.config.update({
            'TESTING': True,
            'CHAT_DB_PATH': db_path,
            'USER_DB_PATH': user_db_path,
            'WTF_CSRF_ENABLED': False,
            'RATELIMIT_ENABLED': False,  # Disable rate limiting for tests
            'SECRET_KEY': 'test-secret',
            'UPLOAD_FOLDER': temp_dir,
            'DOCUMENTS_FOLDER': temp_dir,
            'REDIS_URL': 'redis://localhost:6379/1',  # use separate DB for tests
            'OLLAMA_URL': 'http://localhost:11434',   # may be mocked
        })

        # Initialize databases
        with flask_app.app_context():
            init_db()
            init_user_db()

        yield flask_app

        # Cleanup
        try:
            os.remove(db_path)
            os.remove(user_db_path)
            os.rmdir(temp_dir)
        except:
            pass  # Ignore cleanup errors in tests


@pytest.fixture
def client(test_app):
    """Flask test client."""
    return test_app.test_client()


@pytest.fixture
def runner(test_app):
    """CLI runner."""
    return test_app.test_cli_runner()


# Alias for pytest-flask plugin compatibility
@pytest.fixture(scope='module')
def app(test_app):
    """Alias for test_app to support pytest-flask plugin."""
    return test_app