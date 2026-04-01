# tests/test_integration.py
"""Integration tests for FLAI application.

These tests verify that different components work together correctly.
"""
import pytest
import json
from unittest.mock import patch, MagicMock


class TestSessionFlow:
    """Test complete session flow from creation to message exchange."""

    def test_create_session_and_send_message(self, client, app):
        """Test creating a session and sending a message."""
        # First login
        with app.app_context():
            from app.userdb import create_user, get_user_by_login
            # Create test user if not exists
            if not get_user_by_login('testuser'):
                create_user('testuser', 'testpass123', 'Test User')
        
        # Login
        response = client.post('/login', data={
            'login': 'testuser',
            'password': 'testpass123'
        }, follow_redirects=True)
        assert response.status_code == 200
        
        # Get sessions (should create one if none exists)
        response = client.get('/api/sessions')
        assert response.status_code == 200
        sessions = response.get_json()
        assert isinstance(sessions, list)
        
        # Send a message
        response = client.post('/api/send_message', 
            json={'message': 'Hello, this is a test!'},
            content_type='application/json'
        )
        assert response.status_code == 200
        data = response.get_json()
        assert 'request_id' in data or 'status' in data

    def test_session_ownership_validation(self, client, app):
        """Test that users cannot access other users' sessions."""
        # Create two users
        with app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('user1'):
                create_user('user1', 'pass1', 'User One')
            if not get_user_by_login('user2'):
                create_user('user2', 'pass2', 'User Two')
        
        # Login as user1
        client.post('/login', data={'login': 'user1', 'password': 'pass1'})
        
        # Create a session for user1
        response = client.post('/api/sessions/new')
        assert response.status_code == 200
        session1 = response.get_json()
        session1_id = session1['id']
        
        # Logout and login as user2
        client.get('/logout')
        client.post('/login', data={'login': 'user2', 'password': 'pass2'})
        
        # Try to access user1's session - should fail
        response = client.get(f'/api/sessions/{session1_id}/messages')
        assert response.status_code == 404  # Should return 404, not 403 (to not leak info)
        
        # Try to switch to user1's session - should fail
        response = client.post(f'/api/sessions/{session1_id}/switch')
        assert response.status_code == 404


class TestMessagePagination:
    """Test message pagination functionality."""

    def test_get_messages_with_pagination(self, client, app):
        """Test that messages can be retrieved with pagination."""
        # Login
        with app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('pagetest'):
                create_user('pagetest', 'pass123', 'Pagination Test')
        
        client.post('/login', data={'login': 'pagetest', 'password': 'pass123'})
        
        # Create a session
        response = client.post('/api/sessions/new')
        session = response.get_json()
        session_id = session['id']
        
        # Get messages with default pagination
        response = client.get(f'/api/sessions/{session_id}/messages?limit=10&offset=0')
        assert response.status_code == 200
        data = response.get_json()
        
        # Should have pagination fields
        assert 'messages' in data
        assert 'limit' in data
        assert 'offset' in data
        assert data['limit'] == 10
        assert data['offset'] == 0

    def test_get_messages_respects_max_limit(self, client, app):
        """Test that message limit is capped at maximum."""
        # Login
        with app.app_context():
            from app.userdb import create_user
            from app.userdb import get_user_by_login
            if not get_user_by_login('maxlimittest'):
                create_user('maxlimittest', 'pass123', 'Max Limit Test')
        
        client.post('/login', data={'login': 'maxlimittest', 'password': 'pass123'})
        
        # Create a session
        response = client.post('/api/sessions/new')
        session = response.get_json()
        session_id = session['id']
        
        # Try to get more than max limit
        response = client.get(f'/api/sessions/{session_id}/messages?limit=500&offset=0')
        assert response.status_code == 200
        data = response.get_json()
        
        # Should be capped at MESSAGES_MAX_LIMIT (200)
        assert data['limit'] <= 200


class TestSecurityHeaders:
    """Test security headers are set correctly."""

    def test_security_headers_present(self, client):
        """Test that security headers are present in responses."""
        response = client.get('/health')
        assert response.status_code == 200
        
        # Check security headers
        assert response.headers.get('X-Content-Type-Options') == 'nosniff'
        assert response.headers.get('X-Frame-Options') == 'DENY'
        assert response.headers.get('X-XSS-Protection') == '1; mode=block'
        assert 'Content-Security-Policy' in response.headers

    def test_csp_header_content(self, client):
        """Test CSP header has correct directives."""
        response = client.get('/health')
        csp = response.headers.get('Content-Security-Policy', '')
        
        # Should have key directives
        assert "default-src" in csp
        assert "'self'" in csp


class TestQueueAndBackgroundTasks:
    """Test queue functionality and background task processing."""

    def test_queue_task_creation(self, client, app):
        """Test that tasks are properly added to queue."""
        # Login
        with app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('queuetest'):
                create_user('queuetest', 'pass123', 'Queue Test')
        
        client.post('/login', data={'login': 'queuetest', 'password': 'pass123'})
        
        # Create a session
        response = client.post('/api/sessions/new')
        session = response.get_json()
        session_id = session['id']
        
        # Send a message (creates queue task)
        response = client.post('/api/send_message',
            json={'message': 'Test message for queue'},
            content_type='application/json'
        )
        assert response.status_code == 200
        data = response.get_json()
        
        # Should have request_id for queued task
        assert 'request_id' in data or 'status' in data

    @patch('modules.base.OllamaClient')
    def test_queue_task_processing(self, mock_ollama, client, app):
        """Test that queue tasks are processed correctly."""
        # Mock Ollama response
        mock_response = MagicMock()
        mock_response.json.return_value = {'message': {'content': 'Test response'}}
        mock_ollama.return_value.chat.return_value = mock_response
        
        # Login
        with app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('processtest'):
                create_user('processtest', 'pass123', 'Process Test')
        
        client.post('/login', data={'login': 'processtest', 'password': 'pass123'})
        
        # Create a session
        response = client.post('/api/sessions/new')
        session = response.get_json()
        session_id = session['id']
        
        # Send a message
        response = client.post('/api/send_message',
            json={'message': 'Test'},
            content_type='application/json'
        )
        assert response.status_code == 200
