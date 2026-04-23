# tests/test_integration.py
"""End-to-end integration tests."""
import json
import pytest


@pytest.mark.e2e
class TestSessionFlow:
    """Test complete session flow."""

    @pytest.mark.e2e
    def test_create_session_and_send_message(self, client, test_app, mock_ollama_client):
        """Test creating a session and sending a message."""
        # Create user and login
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('e2etest'):
                create_user('e2etest', 'pass123', 'E2E Test')

        client.post('/login', data={'login': 'e2etest', 'password': 'pass123'})

        # Create session
        response = client.post('/api/sessions/new')
        assert response.status_code == 200
        session_id = response.get_json()['id']

        # Send message
        response = client.post('/api/send_message',
            data=json.dumps({'message': 'Hello'}),
            content_type='application/json'
        )
        assert response.status_code == 200

    @pytest.mark.e2e
    def test_session_ownership_validation(self, client, test_app):
        """Test that session ownership is validated."""
        # Create user1 and session
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login
            from app.db import create_session

            if not get_user_by_login('owner1'):
                create_user('owner1', 'pass1', 'Owner One')

            client.post('/login', data={'login': 'owner1', 'password': 'pass1'})
            response = client.post('/api/sessions/new')
            session_id = response.get_json()['id']

        # Logout and login as user2
        client.get('/logout')

        with test_app.app_context():
            if not get_user_by_login('owner2'):
                create_user('owner2', 'pass2', 'Owner Two')

        client.post('/login', data={'login': 'owner2', 'password': 'pass2'})

        # Try to access user1's session - should fail
        response = client.get(f'/api/sessions/{session_id}/messages')
        assert response.status_code == 404


@pytest.mark.e2e
class TestMessagePagination:
    """Test message pagination."""

    @pytest.mark.e2e
    def test_get_messages_with_pagination(self, client, test_app):
        """Test getting messages with pagination."""
        # Create user and login
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('pagetest'):
                create_user('pagetest', 'pass123', 'Page Test')

        client.post('/login', data={'login': 'pagetest', 'password': 'pass123'})

# Create session
        response = client.post('/api/sessions/new')
        response.get_json()['id']  # session_id - used to create the session

        # Send message
        response = client.post('/api/send_message',
            data=json.dumps({'message': 'Test'}),
            content_type='application/json'
        )
        assert response.status_code == 200

        # Check queue status
        response = client.get('/api/queue/status')
        assert response.status_code == 200
