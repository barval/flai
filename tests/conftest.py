# tests/conftest.py
"""Pytest fixtures and configuration."""
import pytest
import os
import tempfile
from flask import Flask
from app import create_app
from app.db import init_db, CHAT_DB_PATH
from app.userdb import init_user_db


@pytest.fixture
def app():
    """Create Flask app with test configuration."""
    # Create temporary database files
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, 'test_chats.db')
    user_db_path = os.path.join(temp_dir, 'test_users.db')

    app = create_app()
    app.config.update({
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
    init_db()
    init_user_db()

    # Override CHAT_DB_PATH for db functions that use global constant
    # (we rely on app.config for paths, but db.py uses global CHAT_DB_PATH)
    import app.db
    app.db.CHAT_DB_PATH = db_path
    import app.userdb
    app.userdb.USER_DB_PATH = user_db_path

    yield app

    # Cleanup
    try:
        os.remove(db_path)
        os.remove(user_db_path)
        os.rmdir(temp_dir)
    except:
        pass  # Ignore cleanup errors in tests


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture
def runner(app):
    """CLI runner."""
    return app.test_cli_runner()