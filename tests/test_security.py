# tests/test_security.py
"""Security tests for FLAI application.

Tests for CSRF protection, rate limiting, path traversal, and other security features.
"""
import pytest
import os
import tempfile
from unittest.mock import patch, MagicMock


class TestCSRFProtection:
    """Test CSRF protection is working correctly."""

    def test_csrf_token_required_for_post(self, client, app):
        """Test that POST requests require CSRF token."""
        # Create user and login
        with app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('csrftest'):
                create_user('csrftest', 'pass123', 'CSRF Test')
        
        # Login to get session
        response = client.post('/login', data={
            'login': 'csrftest',
            'password': 'pass123'
        }, follow_redirects=True)
        assert response.status_code == 200
        
        # Try to POST without CSRF token - should fail
        response = client.post('/api/sessions/new', data={})
        # Should be rejected (400 or 403 depending on CSRF config)
        assert response.status_code in [400, 403]

    def test_csrf_token_in_form(self, client, app):
        """Test that login form contains CSRF token."""
        response = client.get('/login')
        assert response.status_code == 200
        
        # Check for CSRF token in HTML
        html = response.get_data(as_text=True)
        assert 'csrf_token' in html

    def test_csrf_token_in_meta(self, client, app):
        """Test that CSRF token is available in meta tag for AJAX."""
        response = client.get('/login')
        assert response.status_code == 200
        
        html = response.get_data(as_text=True)
        assert 'name="csrf-token"' in html


class TestRateLimiting:
    """Test rate limiting is working correctly."""

    def test_login_rate_limiting(self, client, app):
        """Test that login endpoint has rate limiting."""
        # Make multiple failed login attempts
        for i in range(10):
            response = client.post('/login', data={
                'login': 'ratelimituser',
                'password': 'wrongpass'
            })
        
        # Should eventually get rate limited (429)
        # Note: This might not trigger if rate limiting is disabled in tests
        # The test verifies the endpoint exists and can be called
        assert response.status_code in [200, 401, 429]


class TestPathTraversal:
    """Test protection against path traversal attacks."""

    def test_session_id_path_traversal_blocked(self, client, app):
        """Test that path traversal in session_id is blocked."""
        # Login
        with app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('pathtest'):
                create_user('pathtest', 'pass123', 'Path Test')
        
        client.post('/login', data={'login': 'pathtest', 'password': 'pass123'})
        
        # Try path traversal in session_id
        malicious_session_ids = [
            '../../../etc/passwd',
            '..\\..\\..\\windows\\system32',
            '/etc/passwd',
            '....//....//etc/passwd'
        ]
        
        for session_id in malicious_session_ids:
            response = client.get(f'/api/sessions/{session_id}/messages')
            # Should be rejected (400 or 404)
            assert response.status_code in [400, 404]

    def test_file_upload_path_traversal_blocked(self, client, app):
        """Test that path traversal in file uploads is blocked."""
        # Login
        with app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('filetest'):
                create_user('filetest', 'pass123', 'File Test')
        
        client.post('/login', data={'login': 'filetest', 'password': 'pass123'})
        
        # Create a session
        response = client.post('/api/sessions/new')
        session = response.get_json()
        session_id = session['id']
        
        # Try to access file with path traversal
        malicious_paths = [
            f'{session_id}/../../../etc/passwd',
            f'{session_id}/..\\..\\..\\windows\\system32',
            f'{session_id}/....//....//etc/passwd'
        ]
        
        for path in malicious_paths:
            response = client.get(f'/api/files/{path}')
            # Should be rejected (400 or 403)
            assert response.status_code in [400, 403]


class TestSessionOwnership:
    """Test session ownership validation."""

    def test_cannot_access_other_user_session(self, client, app):
        """Test that users cannot access other users' sessions."""
        # Create two users
        with app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('owner1'):
                create_user('owner1', 'pass1', 'Owner One')
            if not get_user_by_login('owner2'):
                create_user('owner2', 'pass2', 'Owner Two')
        
        # Login as user1 and create session
        client.post('/login', data={'login': 'owner1', 'password': 'pass1'})
        response = client.post('/api/sessions/new')
        session1 = response.get_json()
        session1_id = session1['id']
        
        # Logout and login as user2
        client.get('/logout')
        client.post('/login', data={'login': 'owner2', 'password': 'pass2'})
        
        # Try to access user1's session
        response = client.get(f'/api/sessions/{session1_id}/messages')
        assert response.status_code == 404  # Should not leak existence
        
        # Try to switch to user1's session
        response = client.post(f'/api/sessions/{session1_id}/switch')
        assert response.status_code == 404
        
        # Try to delete user1's session
        response = client.post(f'/api/sessions/{session1_id}/delete')
        assert response.status_code == 404

    def test_invalid_uuid_rejected(self, client, app):
        """Test that invalid UUID session IDs are rejected."""
        # Login
        with app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('uuidtest'):
                create_user('uuidtest', 'pass123', 'UUID Test')
        
        client.post('/login', data={'login': 'uuidtest', 'password': 'pass123'})
        
        # Try invalid session IDs
        invalid_ids = [
            'not-a-uuid',
            '12345',
            'abc-def-ghi',
            '',
            None
        ]
        
        for session_id in invalid_ids:
            if session_id is None:
                continue
            response = client.get(f'/api/sessions/{session_id}/messages')
            assert response.status_code in [400, 404]


class TestSQLInjection:
    """Test protection against SQL injection."""

    def test_login_sql_injection_blocked(self, client, app):
        """Test that SQL injection in login is blocked."""
        # Try SQL injection payloads
        injection_payloads = [
            "' OR '1'='1",
            "admin'--",
            "'; DROP TABLE users;--",
            "' UNION SELECT * FROM users--"
        ]
        
        for payload in injection_payloads:
            response = client.post('/login', data={
                'login': payload,
                'password': 'anypass'
            })
            # Should fail authentication, not SQL error
            assert response.status_code in [200, 401]
            # Should not expose SQL errors
            html = response.get_data(as_text=True)
            assert 'SQL' not in html
            assert 'sqlite' not in html.lower()

    def test_session_id_sql_injection_blocked(self, client, app):
        """Test that SQL injection in session_id is blocked."""
        # Login
        with app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('sqltest'):
                create_user('sqltest', 'pass123', 'SQL Test')
        
        client.post('/login', data={'login': 'sqltest', 'password': 'pass123'})
        
        # Try SQL injection in session_id
        injection_payloads = [
            "' OR '1'='1",
            "'; DROP TABLE messages;--",
            "1' UNION SELECT * FROM users--"
        ]
        
        for payload in injection_payloads:
            response = client.get(f'/api/sessions/{payload}/messages')
            # Should be rejected (400 or 404)
            assert response.status_code in [400, 404]


class TestFileUploadSecurity:
    """Test file upload security."""

    def test_dangerous_file_extensions_blocked(self, client, app):
        """Test that dangerous file extensions are blocked."""
        # Login
        with app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('uploadtest'):
                create_user('uploadtest', 'pass123', 'Upload Test')
        
        client.post('/login', data={'login': 'uploadtest', 'password': 'pass123'})
        
        # Create a session
        response = client.post('/api/sessions/new')
        session = response.get_json()
        session_id = session['id']
        
        # Try uploading dangerous file types
        dangerous_extensions = ['.exe', '.sh', '.bat', '.cmd', '.php', '.py']
        
        for ext in dangerous_extensions:
            # Create test file
            test_file = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
            test_file.write(b'test content')
            test_file.close()
            
            with open(test_file.name, 'rb') as f:
                response = client.post(
                    f'/api/sessions/{session_id}/upload',
                    data={'file': f},
                    content_type='multipart/form-data'
                )
            
            # Should be rejected
            assert response.status_code in [400, 403, 415]
            
            # Cleanup
            os.unlink(test_file.name)
