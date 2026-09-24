# tests/test_db.py
"""Integration tests for database functions."""

import uuid
from contextlib import nullcontext
from datetime import datetime
from unittest.mock import Mock, patch

import pytest


def generate_unique_name(prefix="test"):
    """Generate unique name for test isolation."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.mark.unit
def test_get_user_documents_returns_recognition_and_embedding_models(test_app):
    from app.db import get_user_documents

    row = {
        "id": "doc-1",
        "filename": "scan.pdf",
        "file_size": 1234,
        "file_ext": ".pdf",
        "file_path": "user/scan.pdf",
        "uploaded_at": None,
        "index_status": "indexed",
        "indexed_at": datetime(2026, 9, 23, 10, 0, 5),
        "indexing_started_at": datetime(2026, 9, 23, 10, 0, 0),
        "embedding_model": "bge-m3-Q8_0.gguf",
        "description_model": "Qwen3VL-8B.gguf",
    }
    cursor = Mock()
    cursor.fetchall.return_value = [row]
    connection = Mock()
    connection.cursor.return_value = cursor

    with test_app.app_context(), patch("app.db.get_db", return_value=nullcontext(connection)):
        documents = get_user_documents("user")

    query = cursor.execute.call_args.args[0]
    assert "embedding_model" in query
    assert "description_model" in query
    assert documents[0]["embedding_model"] == "bge-m3-Q8_0.gguf"
    assert documents[0]["description_model"] == "Qwen3VL-8B.gguf"
    assert documents[0]["processing_time"] == 5


@pytest.mark.integration
class TestDatabase:
    """Test database operations."""

    def test_create_session(self, test_app):
        """Test creating a session."""
        username = generate_unique_name()
        with test_app.app_context():
            from app.db import create_session, get_user_sessions

            # Create session
            session_id = create_session(username, title="Test Session")

            # Verify session was created
            sessions = get_user_sessions(username)
            assert len(sessions) >= 1
            assert any(s["id"] == session_id for s in sessions)

    def test_save_and_get_messages(self, test_app):
        """Test saving and retrieving messages."""
        username = generate_unique_name()
        with test_app.app_context():
            from app.db import create_session, get_session_messages, save_message

            # Create session
            session_id = create_session(username, title="Message Test")

            # Save messages
            save_message(session_id, "user", "Hello")
            save_message(session_id, "assistant", "Hi there")

            # Get messages
            messages = get_session_messages(session_id)
            assert len(messages) == 2
            assert messages[0]["role"] == "user"
            assert messages[1]["role"] == "assistant"

    def test_save_message_with_token_counts(self, test_app):
        """Token counters (prompt/completion) survive a save+get roundtrip."""
        username = generate_unique_name()
        with test_app.app_context():
            from app.db import create_session, get_session_messages, save_message

            session_id = create_session(username, title="Token Test")
            save_message(
                session_id,
                "assistant",
                "Answer text",
                model_name="gpt-oss-20b",
                completion_tokens=120,
                prompt_tokens=3401,
                model_type="reasoning",
            )

            messages = get_session_messages(session_id)
            assert len(messages) == 1
            assert messages[0]["prompt_tokens"] == 3401
            assert messages[0]["completion_tokens"] == 120

    def test_save_message_with_file(self, test_app):
        """Test saving message with file attachment."""
        username = generate_unique_name()
        with test_app.app_context():
            from app.db import create_session, get_session_messages, save_message

            # Create session
            session_id = create_session(username, title="File Test")

            # Save message with file
            save_message(
                session_id,
                "user",
                "Check this image",
                file_data="base64data",
                file_type="image/jpeg",
                file_name="test.jpg",
                file_path="testuser/test.jpg",
            )

            # Get messages
            messages = get_session_messages(session_id)
            assert len(messages) == 1
            assert messages[0]["file_name"] == "test.jpg"
            assert messages[0]["file_type"] == "image/jpeg"

    def test_delete_session_and_messages(self, test_app):
        """Test deleting session and its messages."""
        username = generate_unique_name()
        with test_app.app_context():
            from app.db import create_session, delete_session_and_messages, get_session_messages, save_message

            # Create session with messages
            session_id = create_session(username, title="Delete Test")
            save_message(session_id, "user", "Message 1")
            save_message(session_id, "assistant", "Message 2")

            # Delete session
            result = delete_session_and_messages(session_id, username)
            assert result is True

            # Verify messages were deleted
            messages = get_session_messages(session_id)
            assert len(messages) == 0


@pytest.mark.unit
def test_get_user_sessions_token_totals(test_app):
    """Per-session SUMs of prompt/completion tokens appear in the session list.

    The session card renders ``[↑out ↓in]`` from these two fields, so the
    backend must return them summed across every message of the session.
    """
    username = generate_unique_name()
    with test_app.app_context():
        from app.db import create_session, get_user_sessions, save_message

        session_id = create_session(username, title="Token Sums")
        save_message(session_id, "assistant", "first", completion_tokens=120, prompt_tokens=3401, model_type="chat")
        save_message(session_id, "assistant", "second", completion_tokens=30, prompt_tokens=500, model_type="chat")

        sessions = get_user_sessions(username)
        target = next(s for s in sessions if s["id"] == session_id)
        assert target["message_count"] == 2
        assert target["total_prompt_tokens"] == 3901
        assert target["total_completion_tokens"] == 150
