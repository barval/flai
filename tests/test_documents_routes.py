# tests/test_documents_routes.py
"""Integration tests for documents routes."""
import pytest
import io
from unittest.mock import patch


@pytest.mark.integration
class TestDocumentsAuth:
    """Test documents authentication."""

    def test_get_documents_requires_auth(self, client):
        """Test that getting documents requires authentication."""
        response = client.get('/api/documents')
        assert response.status_code == 401

    def test_upload_document_requires_auth(self, client):
        """Test that uploading document requires authentication."""
        response = client.post('/api/documents/upload')
        assert response.status_code == 401

    def test_delete_document_requires_auth(self, client):
        """Test that deleting document requires authentication."""
        response = client.delete('/api/documents/test-id')
        assert response.status_code == 401


@pytest.mark.integration
class TestDocumentsCRUD:
    """Test documents CRUD operations."""

    @pytest.fixture
    def authenticated_client(self, client, test_app):
        """Create authenticated client."""
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('doctest'):
                create_user('doctest', 'pass123', 'Doc Test User')

        client.post('/login', data={'login': 'doctest', 'password': 'pass123'})
        return client

    @pytest.mark.integration
    def test_get_documents_empty(self, authenticated_client):
        """Test getting documents when none exist."""
        response = authenticated_client.get('/api/documents')
        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)

    @pytest.mark.integration
    @patch('app.routes.documents.magic.from_buffer')
    def test_upload_document_pdf(self, mock_magic, authenticated_client):
        """Test uploading a PDF document."""
        mock_magic.return_value = 'application/pdf'

        # Create test PDF file
        test_file = io.BytesIO(b'%PDF-1.4 test pdf content')
        test_file.name = 'test.pdf'

        response = authenticated_client.post('/api/documents/upload',
            data={'file': (test_file, 'test.pdf')},
            content_type='multipart/form-data'
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'
        assert 'document_id' in data

    @pytest.mark.integration
    @patch('app.routes.documents.magic.from_buffer')
    def test_upload_document_docx(self, mock_magic, authenticated_client):
        """Test uploading a DOCX document."""
        mock_magic.return_value = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

        test_file = io.BytesIO(b'test docx content')
        test_file.name = 'test.docx'

        response = authenticated_client.post('/api/documents/upload',
            data={'file': (test_file, 'test.docx')},
            content_type='multipart/form-data'
        )
        assert response.status_code == 200

    @pytest.mark.integration
    @patch('app.routes.documents.magic.from_buffer')
    def test_upload_document_txt(self, mock_magic, authenticated_client):
        """Test uploading a TXT document."""
        mock_magic.return_value = 'text/plain'

        test_file = io.BytesIO(b'test txt content')
        test_file.name = 'test.txt'

        response = authenticated_client.post('/api/documents/upload',
            data={'file': (test_file, 'test.txt')},
            content_type='multipart/form-data'
        )
        assert response.status_code == 200

    def test_upload_document_no_file(self, authenticated_client):
        """Test uploading document without file."""
        response = authenticated_client.post('/api/documents/upload',
            data={},
            content_type='multipart/form-data'
        )
        assert response.status_code == 400

    @pytest.mark.integration
    @patch('app.routes.documents.magic.from_buffer')
    def test_upload_document_unsupported_type(self, mock_magic, authenticated_client):
        """Test uploading unsupported file type."""
        mock_magic.return_value = 'application/exe'

        test_file = io.BytesIO(b'exe content')
        test_file.name = 'test.exe'

        response = authenticated_client.post('/api/documents/upload',
            data={'file': (test_file, 'test.exe')},
            content_type='multipart/form-data'
        )
        assert response.status_code == 400

    @pytest.mark.integration
    @patch('app.routes.documents.magic.from_buffer')
    def test_upload_document_wrong_extension(self, mock_magic, authenticated_client):
        """Test uploading file with wrong extension."""
        mock_magic.return_value = 'application/pdf'

        # PDF content but .txt extension
        test_file = io.BytesIO(b'%PDF-1.4 test')
        test_file.name = 'test.txt'

        response = authenticated_client.post('/api/documents/upload',
            data={'file': (test_file, 'test.txt')},
            content_type='multipart/form-data'
        )
        assert response.status_code == 400

    @pytest.mark.integration
    @patch('app.routes.documents.magic.from_buffer')
    def test_delete_document(self, mock_magic, authenticated_client, test_app):
        """Test deleting a document."""
        mock_magic.return_value = 'text/plain'

        # First upload a document
        test_file = io.BytesIO(b'test content')
        test_file.name = 'test.txt'

        upload_response = authenticated_client.post('/api/documents/upload',
            data={'file': (test_file, 'test.txt')},
            content_type='multipart/form-data'
        )
        assert upload_response.status_code == 200
        document_id = upload_response.get_json()['document_id']

        # Then delete it
        delete_response = authenticated_client.delete(f'/api/documents/{document_id}')
        assert delete_response.status_code == 200

    def test_delete_nonexistent_document(self, authenticated_client):
        """Test deleting nonexistent document."""
        response = authenticated_client.delete('/api/documents/nonexistent-id')
        assert response.status_code == 404


@pytest.mark.integration
class TestDocumentValidation:
    """Test document validation logic."""

    @pytest.fixture
    def authenticated_client(self, client, test_app):
        """Create authenticated client."""
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login
            if not get_user_by_login('valtest'):
                create_user('valtest', 'pass123', 'Validation Test')

        client.post('/login', data={'login': 'valtest', 'password': 'pass123'})
        return client

    @pytest.mark.integration
    @patch('app.routes.documents.magic.from_buffer')
    def test_file_size_limit(self, mock_magic, authenticated_client):
        """Test file size limit enforcement."""
        mock_magic.return_value = 'text/plain'

        # Create file larger than limit (assuming 5MB limit)
        large_content = b'x' * (6 * 1024 * 1024)  # 6MB
        test_file = io.BytesIO(large_content)
        test_file.name = 'large.txt'

        response = authenticated_client.post('/api/documents/upload',
            data={'file': (test_file, 'large.txt')},
            content_type='multipart/form-data'
        )
        # Should be rejected due to size
        assert response.status_code in [400, 413]

    def test_filename_sanitization(self, authenticated_client):
        """Test that filenames are sanitized."""
        # Try path traversal in filename
        test_file = io.BytesIO(b'test')
        test_file.name = '../../../etc/passwd'

        response = authenticated_client.post('/api/documents/upload',
            data={'file': (test_file, '../../../etc/passwd')},
            content_type='multipart/form-data'
        )
        # Should be rejected
        assert response.status_code in [400, 403]
