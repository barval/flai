# tests/test_admin_routes.py
"""Tests for admin routes."""
import pytest
from unittest.mock import patch, MagicMock
import json


class TestAdminAuth:
    """Test admin authentication and access control."""

    def test_admin_panel_requires_auth(self, client):
        """Test that admin panel requires authentication."""
        response = client.get('/admin/')
        assert response.status_code == 302  # Redirect to login
        assert '/login' in response.location

    def test_admin_api_requires_auth(self, client):
        """Test that admin API requires authentication."""
        response = client.get('/admin/api/users')
        assert response.status_code == 401

    def test_admin_panel_requires_admin(self, client, app):
        """Test that admin panel requires admin privileges."""
        # Create non-admin user and login
        with app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('regularuser'):
                create_user('regularuser', 'pass123', 'Regular User', is_admin=False)
        
        client.post('/login', data={'login': 'regularuser', 'password': 'pass123'})
        
        response = client.get('/admin/')
        assert response.status_code == 403

    def test_admin_api_requires_admin(self, client, app):
        """Test that admin API requires admin privileges."""
        with app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('regularuser2'):
                create_user('regularuser2', 'pass123', 'Regular User 2', is_admin=False)
        
        client.post('/login', data={'login': 'regularuser2', 'password': 'pass123'})
        
        response = client.get('/admin/api/users')
        assert response.status_code == 403


class TestAdminUsers:
    """Test admin user management endpoints."""

    @pytest.fixture
    def admin_client(self, client, app):
        """Create admin client."""
        with app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('admin'):
                create_user('admin', 'adminpass', 'Admin User', is_admin=True)
        
        client.post('/login', data={'login': 'admin', 'password': 'adminpass'})
        return client

    def test_get_users(self, admin_client, app):
        """Test getting list of users."""
        with app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('testuser'):
                create_user('testuser', 'pass123', 'Test User')
        
        response = admin_client.get('/admin/api/users')
        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)
        # Should have at least testuser (admin excluded)
        assert len(data) >= 1

    def test_add_user(self, admin_client):
        """Test adding a new user."""
        response = admin_client.post('/admin/api/users',
            data=json.dumps({
                'login': 'newuser',
                'password': 'newpass123',
                'name': 'New User',
                'service_class': 2,
                'is_active': True
            }),
            content_type='application/json'
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'

    def test_add_user_missing_fields(self, admin_client):
        """Test adding user with missing fields."""
        response = admin_client.post('/admin/api/users',
            data=json.dumps({
                'login': 'incompleteuser'
                # Missing password and name
            }),
            content_type='application/json'
        )
        assert response.status_code == 400

    def test_add_duplicate_user(self, admin_client, app):
        """Test adding duplicate user."""
        with app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('existinguser'):
                create_user('existinguser', 'pass123', 'Existing User')
        
        response = admin_client.post('/admin/api/users',
            data=json.dumps({
                'login': 'existinguser',
                'password': 'newpass',
                'name': 'Duplicate User'
            }),
            content_type='application/json'
        )
        assert response.status_code == 400

    def test_update_user(self, admin_client, app):
        """Test updating user data."""
        with app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('updatetest'):
                create_user('updatetest', 'pass123', 'Update Test')
        
        response = admin_client.put('/admin/api/users/updatetest',
            data=json.dumps({
                'name': 'Updated Name',
                'service_class': 1
            }),
            content_type='application/json'
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'

    def test_change_password(self, admin_client, app):
        """Test changing user password."""
        with app.app_context():
            from app.userdb import create_user, get_user_by_login, check_password_hash
            if not get_user_by_login('pwdtest'):
                create_user('pwdtest', 'oldpass', 'Password Test')
        
        response = admin_client.put('/admin/api/users/pwdtest/password',
            data=json.dumps({'new_password': 'newpass123'}),
            content_type='application/json'
        )
        assert response.status_code == 200
        
        # Verify password was changed
        with app.app_context():
            from app.userdb import get_user_by_login, check_password_hash
            user = get_user_by_login('pwdtest')
            assert check_password_hash(user['password_hash'], 'newpass123')

    def test_change_password_empty(self, admin_client):
        """Test changing password with empty value."""
        response = admin_client.put('/admin/api/users/admin/password',
            data=json.dumps({'new_password': ''}),
            content_type='application/json'
        )
        assert response.status_code == 400

    def test_delete_user(self, admin_client, app):
        """Test deleting user."""
        with app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('deletetest'):
                create_user('deletetest', 'pass123', 'Delete Test')
        
        response = admin_client.delete('/admin/api/users/deletetest')
        assert response.status_code == 200
        
        # Verify user was deleted
        with app.app_context():
            from app.userdb import get_user_by_login
            assert get_user_by_login('deletetest') is None


class TestAdminStats:
    """Test admin statistics endpoints."""

    @pytest.fixture
    def admin_client(self, client, app):
        """Create admin client."""
        with app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('admin'):
                create_user('admin', 'adminpass', 'Admin User', is_admin=True)
        
        client.post('/login', data={'login': 'admin', 'password': 'adminpass'})
        return client

    def test_get_stats(self, admin_client):
        """Test getting system statistics."""
        response = admin_client.get('/admin/api/stats')
        assert response.status_code == 200
        data = response.get_json()
        assert 'chat_db_size' in data
        assert 'user_db_size' in data
        assert 'files_db_size' in data
        assert 'documents_db_size' in data


class TestAdminModelManagement:
    """Test admin model management endpoints."""

    @pytest.fixture
    def admin_client(self, client, app):
        """Create admin client."""
        with app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('admin'):
                create_user('admin', 'adminpass', 'Admin User', is_admin=True)
        
        client.post('/login', data={'login': 'admin', 'password': 'adminpass'})
        return client

    def test_ollama_check_missing_url(self, admin_client):
        """Test Ollama check without URL parameter."""
        response = admin_client.get('/admin/api/ollama/check')
        assert response.status_code == 400

    def test_ollama_models_missing_url(self, admin_client):
        """Test Ollama models without URL parameter."""
        response = admin_client.get('/admin/api/ollama/models')
        assert response.status_code == 400

    @patch('app.routes.admin.requests.get')
    def test_ollama_check_success(self, mock_get, admin_client):
        """Test Ollama check with successful response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response
        
        response = admin_client.get('/admin/api/ollama/check?url=http://test:11434')
        assert response.status_code == 200
        data = response.get_json()
        assert data['available'] is True

    @patch('app.routes.admin.requests.get')
    def test_ollama_check_failure(self, mock_get, admin_client):
        """Test Ollama check with failed response."""
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_get.return_value = mock_response
        
        response = admin_client.get('/admin/api/ollama/check?url=http://test:11434')
        assert response.status_code == 200
        data = response.get_json()
        assert data['available'] is False
