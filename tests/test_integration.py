# tests/test_integration.py
"""End-to-end integration tests."""
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
        session_id = response.get_json()['id']

        # Get messages with pagination
        response = client.get(f'/api/sessions/{session_id}/messages?limit=10&offset=0')
        assert response.status_code == 200
        data = response.get_json()
        assert 'messages' in data
        assert 'limit' in data
        assert 'offset' in data

    @pytest.mark.e2e
    def test_get_messages_respects_max_limit(self, client, test_app):
        """Test that max limit is respected."""
        # Create user and login
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('maxtest'):
                create_user('maxtest', 'pass123', 'Max Test')

        client.post('/login', data={'login': 'maxtest', 'password': 'pass123'})

        # Create session
        response = client.post('/api/sessions/new')
        session_id = response.get_json()['id']

        # Try to get more than max limit
        response = client.get(f'/api/sessions/{session_id}/messages?limit=500')
        assert response.status_code == 200
        data = response.get_json()
        assert data['limit'] <= 200  # MAX_LIMIT


@pytest.mark.e2e
class TestSecurityHeaders:
    """Test security headers."""

    @pytest.mark.e2e
    def test_security_headers_present(self, client):
        """Test that security headers are present."""
        response = client.get('/health')
        assert response.status_code == 200

        # Check security headers
        assert response.headers.get('X-Content-Type-Options') == 'nosniff'
        assert response.headers.get('X-Frame-Options') == 'DENY'
        assert 'Content-Security-Policy' in response.headers

    @pytest.mark.e2e
    def test_csp_header_content(self, client):
        """Test CSP header content."""
        response = client.get('/health')
        csp = response.headers.get('Content-Security-Policy', '')

        # Check CSP directives
        assert "default-src" in csp
        assert "'self'" in csp


@pytest.mark.e2e
class TestQueueAndBackgroundTasks:
    """Test queue and background task processing."""

    @pytest.mark.e2e
    def test_queue_task_creation(self, client, test_app):
        """Test that queue task is created."""
        # Create user and login
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('queuetest'):
                create_user('queuetest', 'pass123', 'Queue Test')

        client.post('/login', data={'login': 'queuetest', 'password': 'pass123'})

        # Create session
        response = client.post('/api/sessions/new')
        session_id = response.get_json()['id']

        # Send message - should create queue task
        response = client.post('/api/send_message',
            data=json.dumps({'message': 'Test'}),
            content_type='application/json'
        )
        assert response.status_code == 200
        data = response.get_json()
        assert 'request_id' in data

    @pytest.mark.e2e
    def test_queue_task_processing(self, client, test_app, mock_ollama_client):
        """Test that queue task is processed."""
        # Configure mock
        mock_ollama_client.chat.return_value = {
            'message': {'content': 'Test response'}
        }

        # Create user and login
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('processtest'):
                create_user('processtest', 'pass123', 'Process Test')

        client.post('/login', data={'login': 'processtest', 'password': 'pass123'})

        # Create session
        response = client.post('/api/sessions/new')
        session_id = response.get_json()['id']

        # Send message
        response = client.post('/api/send_message',
            data=json.dumps({'message': 'Test'}),
            content_type='application/json'
        )
        assert response.status_code == 200

        # Check queue status
        response = client.get('/api/queue/status')
        assert response.status_code == 200
