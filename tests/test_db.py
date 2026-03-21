# tests/test_db.py
import pytest
from app.db import (
    create_session, save_message, get_session_messages,
    get_user_sessions, delete_session_and_messages, CHAT_DB_PATH
)
import sqlite3
import json


def test_create_session(app):
    """Test creating a new session."""
    session_id = create_session('testuser', title='Test Session')
    assert session_id is not None
    # Verify in DB
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT id, title, user_id FROM chat_sessions WHERE id = ?', (session_id,))
        row = c.fetchone()
        assert row is not None
        assert row[1] == 'Test Session'
        assert row[2] == 'testuser'


def test_save_and_get_messages(app):
    """Test saving a message and retrieving it."""
    session_id = create_session('testuser')
    msg_id = save_message(session_id, 'user', 'Hello!', model_name='test')
    assert msg_id is not None

    messages = get_session_messages(session_id)
    assert len(messages) == 1
    assert messages[0]['role'] == 'user'
    assert messages[0]['content'] == 'Hello!'
    assert messages[0]['model_name'] == 'test'


def test_save_message_with_file(app):
    """Test saving a message with file data (should create file_path)."""
    session_id = create_session('testuser')
    file_data = 'dGVzdA=='  # base64 for "test"
    file_type = 'text/plain'
    file_name = 'test.txt'
    msg_id = save_message(session_id, 'assistant', 'See file', file_data=file_data,
                          file_type=file_type, file_name=file_name)
    assert msg_id is not None

    messages = get_session_messages(session_id)
    assert len(messages) == 1
    msg = messages[0]
    assert msg['file_path'] is not None
    # file_path should be relative path like session_id/unique_name
    assert msg['file_path'].startswith(session_id)
    assert msg['file_name'] == file_name


def test_delete_session_and_messages(app):
    """Test deletion of session and its messages."""
    session_id = create_session('testuser')
    save_message(session_id, 'user', 'Hello')
    # Ensure session exists
    sessions = get_user_sessions('testuser')
    assert len(sessions) == 1

    success = delete_session_and_messages(session_id, 'testuser')
    assert success is True

    sessions = get_user_sessions('testuser')
    assert len(sessions) == 0

    messages = get_session_messages(session_id)
    assert len(messages) == 0