# tests/test_routes.py
"""Integration tests for main routes."""
import json
import pytest


@pytest.mark.integration
class TestAuthRoutes:
    """Test authentication routes."""

    def test_login_page(self, client):
        """Test login page loads."""
        response = client.get('/login')
        assert response.status_code == 200

    def test_login_success(self, client, test_app):
        """Test successful login."""
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('logintest'):
                create_user('logintest', 'pass123', 'Login Test')

        response = client.post('/login', data={
            'login': 'logintest',
            'password': 'pass123'
        }, follow_redirects=True)

        assert response.status_code == 200

    def test_login_failure(self, client):
        """Test failed login."""
        response = client.post('/login', data={
            'login': 'nonexistent',
            'password': 'wrongpass'
        })

        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'Invalid login or password' in html or 'error' in html.lower()

    def test_logout(self, client, test_app):
        """Test logout."""
        # Login first
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('logouttest'):
                create_user('logouttest', 'pass123', 'Logout Test')

        client.post('/login', data={
            'login': 'logouttest',
            'password': 'pass123'
        })

        # Logout
        response = client.get('/logout', follow_redirects=True)
        assert response.status_code == 200


@pytest.mark.integration
class TestChatRoutes:
    """Test chat routes."""

    def test_chat_route_requires_auth(self, client):
        """Test that chat route requires authentication."""
        response = client.get('/chat')
        assert response.status_code == 302  # Redirect to login

    def test_chat_route_authenticated(self, client, test_app):
        """Test chat route with authentication."""
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('chattest'):
                create_user('chattest', 'pass123', 'Chat Test')

        client.post('/login', data={
            'login': 'chattest',
            'password': 'pass123'
        })

        response = client.get('/chat')
        assert response.status_code == 200

    def test_send_message_queued(self, client, test_app, mock_ollama_client):
        """Test that send_message queues the request."""
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('msgtest'):
                create_user('msgtest', 'pass123', 'Message Test')

        client.post('/login', data={
            'login': 'msgtest',
            'password': 'pass123'
        })

        # Create a session first
        response = client.post('/api/sessions/new')
        response.get_json()['id']  # session_id - used to create the session

        # Send message
        response = client.post('/api/send_message',
            data=json.dumps({'message': 'Hello'}),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = response.get_json()
        assert 'request_id' in data or 'status' in data
