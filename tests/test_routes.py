# tests/test_routes.py
import pytest
from app.userdb import create_user
from flask import session


def test_login_page(client):
    """Test that login page is accessible."""
    response = client.get('/login')
    assert response.status_code == 200
    assert b'Authorization' in response.data


def test_login_success(client):
    """Test successful login."""
    create_user('testuser', 'testpass', 'Test User', is_admin=False)
    response = client.post('/login', data={
        'login': 'testuser',
        'password': 'testpass'
    }, follow_redirects=True)
    assert response.status_code == 200
    # Should redirect to chat page (or admin if admin)
    assert b'Chat' in response.data or b'Admin' in response.data


def test_login_failure(client):
    """Test failed login."""
    response = client.post('/login', data={
        'login': 'wrong',
        'password': 'wrong'
    })
    assert response.status_code == 200
    assert b'Invalid login or password' in response.data


def test_logout(client, app):
    """Test logout clears session."""
    with client:
        # Login first
        create_user('testuser', 'testpass', 'Test User')
        client.post('/login', data={'login': 'testuser', 'password': 'testpass'})
        assert session.get('login') == 'testuser'

        response = client.get('/logout', follow_redirects=True)
        assert session.get('login') is None
        assert b'Authorization' in response.data


def test_chat_route_requires_auth(client):
    """Test /chat redirects to login if not authenticated."""
    response = client.get('/chat')
    assert response.status_code == 302
    assert '/login' in response.headers['Location']


def test_chat_route_authenticated(client):
    """Test /chat loads for authenticated user."""
    create_user('testuser', 'testpass', 'Test User')
    with client:
        client.post('/login', data={'login': 'testuser', 'password': 'testpass'})
        response = client.get('/chat')
        assert response.status_code == 200
        assert b'Chat' in response.data


def test_send_message_queued(client, monkeypatch):
    """Test sending a message returns queued status."""
    # Mock RedisRequestQueue to avoid actual Redis
    from app.queue import RedisRequestQueue
    class MockQueue:
        def add_request(self, *args, **kwargs):
            return 'request_id', {'position': 1, 'estimated_seconds': 5}
        def get_user_queue_counts(self, user_id):
            return 0, 0
    monkeypatch.setattr('app.queue.RedisRequestQueue', MockQueue)

    create_user('testuser', 'testpass', 'Test User')
    with client:
        client.post('/login', data={'login': 'testuser', 'password': 'testpass'})
        # Create a session first
        from app.db import create_session
        sid = create_session('testuser')
        with client.session_transaction() as sess:
            sess['current_session'] = sid
        response = client.post('/send_message', json={'message': 'Hello'})
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'queued'