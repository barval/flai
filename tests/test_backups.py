"""Tests for backup and restore routes."""
import os
import json
import io
import tarfile
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from flask import Flask

from app.routes.backups import bp as backups_bp


BACKUP_DIR = tempfile.mkdtemp()


@pytest.fixture
def app():
    """Minimal Flask app with backup blueprint and mocked DB."""
    os.environ['SECRET_KEY'] = 'test-secret-key'
    os.environ['DATABASE_URL'] = 'postgresql://test:test@localhost:5432/test'

    flask_app = Flask(__name__)
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    flask_app.secret_key = 'test-secret-key'
    flask_app.register_blueprint(backups_bp)

    with patch('app.routes.backups._ensure_backup_dir', return_value=BACKUP_DIR):
        yield flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _login_as_admin(client):
    with client.session_transaction() as sess:
        sess['is_admin'] = True


class TestBackupList:
    def test_list_requires_admin(self, client):
        resp = client.get('/admin/api/backups/')
        assert resp.status_code == 403

    def test_list_empty(self, client):
        _login_as_admin(client)
        resp = client.get('/admin/api/backups/')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data == []


class TestBackupCreate:
    @patch('app.routes.backups.subprocess.run')
    def test_create_full_backup(self, mock_run, client):
        _login_as_admin(client)
        mock_run.return_value = MagicMock(
            returncode=0, stdout='-- PostgreSQL dump\nCREATE TABLE users (...);\n',
        )
        resp = client.post(
            '/admin/api/backups/create',
            data=json.dumps({'type': 'full'}),
            content_type='application/json',
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'ok'
        assert data['type'] == 'full'
        assert data['filename'].startswith('full_')
        backup_path = os.path.join(BACKUP_DIR, data['filename'])
        assert os.path.exists(backup_path)
        os.unlink(backup_path)

    @patch('app.routes.backups.subprocess.run')
    def test_create_users_backup(self, mock_run, client):
        _login_as_admin(client)
        mock_run.return_value = MagicMock(
            returncode=0, stdout='-- PostgreSQL dump\nCREATE TABLE users (...);\n',
        )
        resp = client.post(
            '/admin/api/backups/create',
            data=json.dumps({'type': 'users'}),
            content_type='application/json',
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['type'] == 'users'
        backup_path = os.path.join(BACKUP_DIR, data['filename'])
        assert os.path.exists(backup_path)
        os.unlink(backup_path)

    def test_create_invalid_type(self, client):
        _login_as_admin(client)
        resp = client.post(
            '/admin/api/backups/create',
            data=json.dumps({'type': 'invalid'}),
            content_type='application/json',
        )
        assert resp.status_code == 400

    @patch('app.routes.backups.subprocess.run')
    def test_create_pg_dump_failure(self, mock_run, client):
        _login_as_admin(client)
        mock_run.return_value = MagicMock(
            returncode=1, stderr='pg_dump: connection to database failed',
        )
        resp = client.post(
            '/admin/api/backups/create',
            data=json.dumps({'type': 'full'}),
            content_type='application/json',
        )
        assert resp.status_code == 500

    def test_create_requires_auth(self, client):
        resp = client.post(
            '/admin/api/backups/create',
            data=json.dumps({'type': 'full'}),
            content_type='application/json',
        )
        assert resp.status_code == 403


class TestBackupRestore:
    @patch('app.routes.backups.subprocess.run')
    @patch('app.routes.backups.get_db')
    def test_restore_backup(self, mock_get_db, mock_run, client):
        _login_as_admin(client)
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value.__enter__.return_value = mock_conn

        mock_run.return_value = MagicMock(
            returncode=0, stdout='-- PostgreSQL dump\n',
        )
        create_resp = client.post(
            '/admin/api/backups/create',
            data=json.dumps({'type': 'full'}),
            content_type='application/json',
        )
        create_data = create_resp.get_json()
        filename = create_data['filename']
        backup_path = os.path.join(BACKUP_DIR, filename)

        mock_run.return_value = MagicMock(returncode=0, stdout='SET\nCREATE TABLE\n')
        resp = client.post(
            '/admin/api/backups/restore',
            data=json.dumps({'filename': filename}),
            content_type='application/json',
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'ok'
        os.unlink(backup_path)

    def test_restore_missing_filename(self, client):
        _login_as_admin(client)
        resp = client.post(
            '/admin/api/backups/restore',
            data=json.dumps({}),
            content_type='application/json',
        )
        assert resp.status_code == 400

    def test_restore_not_found(self, client):
        _login_as_admin(client)
        resp = client.post(
            '/admin/api/backups/restore',
            data=json.dumps({'filename': 'nonexistent.tar.gz'}),
            content_type='application/json',
        )
        assert resp.status_code == 404

    @patch('app.routes.backups.subprocess.run')
    def test_restore_checksum_mismatch(self, mock_run, client):
        _login_as_admin(client)
        mock_run.return_value = MagicMock(returncode=0, stdout='-- dump\n')
        create_resp = client.post(
            '/admin/api/backups/create',
            data=json.dumps({'type': 'full'}),
            content_type='application/json',
        )
        create_data = create_resp.get_json()
        filename = create_data['filename']
        backup_path = os.path.join(BACKUP_DIR, filename)

        import tarfile, io
        data = io.BytesIO()
        with tarfile.open(backup_path, 'r:gz') as src:
            with tarfile.open(fileobj=data, mode='w:gz') as dst:
                for m in src.getmembers():
                    f = src.extractfile(m)
                    if f:
                        content = f.read()
                        if m.name == 'db_dump.sql' and content:
                            content = b'CORRUPTED' + content[9:]
                        info = tarfile.TarInfo(name=m.name)
                        info.size = len(content)
                        dst.addfile(info, io.BytesIO(content))
        with open(backup_path, 'wb') as f:
            f.write(data.getvalue())

        resp = client.post(
            '/admin/api/backups/restore',
            data=json.dumps({'filename': filename}),
            content_type='application/json',
        )
        assert resp.status_code == 400
        os.unlink(backup_path)


class TestBackupDelete:
    @patch('app.routes.backups.subprocess.run')
    def test_delete_backup(self, mock_run, client):
        _login_as_admin(client)
        mock_run.return_value = MagicMock(returncode=0, stdout='-- dump\n')
        create_resp = client.post(
            '/admin/api/backups/create',
            data=json.dumps({'type': 'full'}),
            content_type='application/json',
        )
        create_data = create_resp.get_json()
        filename = create_data['filename']
        resp = client.delete(f'/admin/api/backups/{filename}')
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'ok'

    def test_delete_not_found(self, client):
        _login_as_admin(client)
        resp = client.delete('/admin/api/backups/nonexistent.tar.gz')
        assert resp.status_code == 404

    def test_delete_path_traversal(self, client):
        _login_as_admin(client)
        resp = client.delete('/admin/api/backups/../../etc/passwd')
        assert resp.status_code == 403

    def test_delete_requires_auth(self, client):
        resp = client.delete('/admin/api/backups/test.tar.gz')
        assert resp.status_code == 403


class TestBackupDownload:
    @patch('app.routes.backups.subprocess.run')
    def test_download_backup(self, mock_run, client):
        _login_as_admin(client)
        mock_run.return_value = MagicMock(returncode=0, stdout='-- dump\n')
        create_resp = client.post(
            '/admin/api/backups/create',
            data=json.dumps({'type': 'full'}),
            content_type='application/json',
        )
        create_data = create_resp.get_json()
        filename = create_data['filename']
        resp = client.get(f'/admin/api/backups/{filename}/download')
        assert resp.status_code == 200
        assert resp.content_type.startswith('application/')

    def test_download_not_found(self, client):
        _login_as_admin(client)
        resp = client.get('/admin/api/backups/nonexistent.tar.gz/download')
        assert resp.status_code == 404

    def test_download_path_traversal(self, client):
        _login_as_admin(client)
        resp = client.get('/admin/api/backups/../../etc/passwd/download')
        assert resp.status_code == 403


class TestBackupMetadata:
    def test_archive_contains_metadata(self):
        with tempfile.NamedTemporaryFile(suffix='.tar.gz', delete=False) as tmp:
            tmp_path = tmp.name
        try:
            meta = {'type': 'full', 'version': '8.0'}
            with tarfile.open(tmp_path, 'w:gz') as tar:
                meta_bytes = json.dumps(meta).encode('utf-8')
                info = tarfile.TarInfo(name='metadata.json')
                info.size = len(meta_bytes)
                tar.addfile(info, fileobj=io.BytesIO(meta_bytes))
            from app.routes.backups import _read_archive_metadata
            result = _read_archive_metadata(tmp_path)
            assert result == meta
        finally:
            os.unlink(tmp_path)

    def test_archive_without_metadata(self):
        with tempfile.NamedTemporaryFile(suffix='.tar.gz', delete=False) as tmp:
            tmp_path = tmp.name
        try:
            with tarfile.open(tmp_path, 'w:gz') as tar:
                info = tarfile.TarInfo(name='data.txt')
                info.size = 4
                tar.addfile(info, fileobj=io.BytesIO(b'test'))
            from app.routes.backups import _read_archive_metadata
            result = _read_archive_metadata(tmp_path)
            assert result is None
        finally:
            os.unlink(tmp_path)


class TestExportHelpers:
    @patch('app.routes.backups.urlparse')
    @patch('app.routes.backups.subprocess.run')
    def test_export_pg_dump_success(self, mock_run, mock_parse):
        from app.routes.backups import _export_pg_dump
        mock_parse.return_value = MagicMock(
            hostname='localhost', port=5432, username='flai',
            password='pass', path='/flai',
        )
        mock_run.return_value = MagicMock(returncode=0, stdout='-- dump content')
        result = _export_pg_dump(['users'])
        assert result == '-- dump content'

    @patch('app.routes.backups.urlparse')
    @patch('app.routes.backups.subprocess.run')
    def test_export_pg_dump_failure(self, mock_run, mock_parse):
        from app.routes.backups import _export_pg_dump
        mock_parse.return_value = MagicMock(
            hostname='localhost', port=5432, username='flai',
            password='pass', path='/flai',
        )
        mock_run.return_value = MagicMock(returncode=1, stderr='connection failed')
        with pytest.raises(RuntimeError, match='connection failed'):
            _export_pg_dump(['users'])
