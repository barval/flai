# tests/test_db.py
"""Integration tests for database functions."""
import pytest
import uuid


def generate_unique_name(prefix='test'):
    """Generate unique name for test isolation."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.mark.integration
class TestDatabase:
    """Test database operations."""

    def test_create_session(self, test_app):
        """Test creating a session."""
        username = generate_unique_name()
        with test_app.app_context():
            from app.db import create_session, get_user_sessions

            # Create session
            session_id = create_session(username, title='Test Session')

            # Verify session was created
            sessions = get_user_sessions(username)
            assert len(sessions) >= 1
            assert any(s['id'] == session_id for s in sessions)

    def test_save_and_get_messages(self, test_app):
        """Test saving and retrieving messages."""
        username = generate_unique_name()
        with test_app.app_context():
            from app.db import create_session, save_message, get_session_messages

            # Create session
            session_id = create_session(username, title='Message Test')

            # Save messages
            save_message(session_id, 'user', 'Hello')
            save_message(session_id, 'assistant', 'Hi there')

            # Get messages
            messages = get_session_messages(session_id)
            assert len(messages) == 2
            assert messages[0]['role'] == 'user'
            assert messages[1]['role'] == 'assistant'

    def test_save_message_with_file(self, test_app):
        """Test saving message with file attachment."""
        username = generate_unique_name()
        with test_app.app_context():
            from app.db import create_session, save_message, get_session_messages

            # Create session
            session_id = create_session(username, title='File Test')

            # Save message with file
            save_message(
                session_id,
                'user',
                'Check this image',
                file_data='base64data',
                file_type='image/jpeg',
                file_name='test.jpg',
                file_path='testuser/test.jpg'
            )

            # Get messages
            messages = get_session_messages(session_id)
            assert len(messages) == 1
            assert messages[0]['file_name'] == 'test.jpg'
            assert messages[0]['file_type'] == 'image/jpeg'

    def test_delete_session_and_messages(self, test_app):
        """Test deleting session and its messages."""
        username = generate_unique_name()
        with test_app.app_context():
            from app.db import create_session, save_message, delete_session_and_messages, get_session_messages

            # Create session with messages
            session_id = create_session(username, title='Delete Test')
            save_message(session_id, 'user', 'Message 1')
            save_message(session_id, 'assistant', 'Message 2')

            # Delete session
            result = delete_session_and_messages(session_id, username)
            assert result is True

            # Verify messages were deleted
            messages = get_session_messages(session_id)
            assert len(messages) == 0
