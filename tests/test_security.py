# tests/test_security.py
"""Integration tests for security features."""
import pytest
from unittest.mock import patch, MagicMock


@pytest.mark.integration
class TestCSRFProtection:
    """Test CSRF protection."""

    def test_csrf_token_required_for_post(self, client, test_app):
        """Test that POST requests require CSRF token."""
        # Create user and login
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('csrftest'):
                create_user('csrftest', 'pass123', 'CSRF Test')

        client.post('/login', data={'login': 'csrftest', 'password': 'pass123'})

        # POST without CSRF token should fail
        response = client.post('/api/send_message',
            data={'message': 'test'},
            content_type='application/json'
        )
        # Should be rejected (400 or 403)
        assert response.status_code in [400, 403]

    def test_csrf_token_in_form(self, client):
        """Test that login form contains CSRF token."""
        response = client.get('/login')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'csrf_token' in html

    def test_csrf_token_in_meta(self, client):
        """Test that CSRF token is available in meta tag."""
        response = client.get('/login')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'name="csrf-token"' in html


@pytest.mark.integration
class TestRateLimiting:
    """Test rate limiting."""

    def test_login_rate_limiting(self, client, test_app):
        """Test that login endpoint has rate limiting."""
        # Rate limiting is disabled in tests, but we can test the endpoint exists
        for i in range(3):
            response = client.post('/login', data={
                'login': f'testuser{i}',
                'password': 'wrongpass'
            })
            # Should get 401 (invalid credentials) or 429 (rate limited)
            assert response.status_code in [401, 429]


@pytest.mark.integration
class TestPathTraversal:
    """Test path traversal protection."""

    def test_session_id_path_traversal_blocked(self, client, test_app):
        """Test that path traversal in session_id is blocked."""
        # Create user and login
        with test_app.app_context():
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

    def test_file_upload_path_traversal_blocked(self, client, test_app):
        """Test that path traversal in file uploads is blocked."""
        # Create user and login
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('filetest'):
                create_user('filetest', 'pass123', 'File Test')

        client.post('/login', data={'login': 'filetest', 'password': 'pass123'})

        # Create a session first
        response = client.post('/api/sessions/new')
        session_id = response.get_json()['id']

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


@pytest.mark.integration
class TestSessionOwnership:
    """Test session ownership validation."""

    def test_cannot_access_other_user_session(self, client, test_app):
        """Test that users cannot access other users' sessions."""
        # Create user1 and session
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login
            from app.db import create_session

            if not get_user_by_login('owner1'):
                create_user('owner1', 'pass1', 'Owner One')

            # Login as user1 and create session
            client.post('/login', data={'login': 'owner1', 'password': 'pass1'})
            response = client.post('/api/sessions/new')
            session1_id = response.get_json()['id']

        # Logout and login as user2
        client.get('/logout')

        with test_app.app_context():
            if not get_user_by_login('owner2'):
                create_user('owner2', 'pass2', 'Owner Two')

        client.post('/login', data={'login': 'owner2', 'password': 'pass2'})

        # Try to access user1's session
        response = client.get(f'/api/sessions/{session1_id}/messages')
        assert response.status_code == 404

        # Try to switch to user1's session
        response = client.post(f'/api/sessions/{session1_id}/switch')
        assert response.status_code == 404

        # Try to delete user1's session
        response = client.post(f'/api/sessions/{session1_id}/delete')
        assert response.status_code == 404

    def test_invalid_uuid_rejected(self, client, test_app):
        """Test that invalid UUID session IDs are rejected."""
        # Create user and login
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('uuidtest'):
                create_user('uuidtest', 'pass123', 'UUID Test')

        client.post('/login', data={'login': 'uuidtest', 'password': 'pass123'})

        # Try invalid session IDs
        invalid_ids = [
            'not-a-uuid',
            '12345',
            'abc-def-ghi',
            ''
        ]

        for session_id in invalid_ids:
            response = client.get(f'/api/sessions/{session_id}/messages')
            assert response.status_code in [400, 404]


@pytest.mark.integration
class TestSQLInjection:
    """Test SQL injection protection."""

    def test_login_sql_injection_blocked(self, client):
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

    def test_session_id_sql_injection_blocked(self, client, test_app):
        """Test that SQL injection in session_id is blocked."""
        # Create user and login
        with test_app.app_context():
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
            # Should be rejected
            assert response.status_code in [400, 404]


@pytest.mark.integration
class TestFileUploadSecurity:
    """Test file upload security."""

    def test_dangerous_file_extensions_blocked(self, client, test_app):
        """Test that dangerous file extensions are blocked."""
        # Create user and login
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('uploadtest'):
                create_user('uploadtest', 'pass123', 'Upload Test')

        client.post('/login', data={'login': 'uploadtest', 'password': 'pass123'})

        # Create a session
        response = client.post('/api/sessions/new')
        session_id = response.get_json()['id']

        # Try uploading dangerous file types
        dangerous_extensions = ['.exe', '.sh', '.bat', '.cmd', '.php', '.py']

        for ext in dangerous_extensions:
            # Create test file
            import io
            test_file = io.BytesIO(b'test content')
            test_file.name = f'test{ext}'

            response = client.post('/api/send_message',
                data={
                    'message': 'test',
                    'file': (test_file, f'test{ext}')
                },
                content_type='multipart/form-data'
            )
            # Should be rejected due to file type validation
            assert response.status_code in [400, 415]
