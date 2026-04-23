# app/routes/backups.py
"""Backup and restore routes for admin panel (PostgreSQL only).

Two backup types:
  1. 'users'     — users table only
  2. 'full'      — users + chats + messages + documents + model_configs +
                   session_visits + user_sessions + user_storage + files
"""
import os
import json
import glob
import shutil
import tarfile
import tempfile
import logging
import subprocess
from datetime import datetime
from functools import wraps

from flask import Blueprint, jsonify, request, current_app, send_file, abort
from flask_babel import gettext as _

from app.database import get_db, DATABASE_URL

bp = Blueprint('backups', __name__, url_prefix='/admin/api/backups')
logger = logging.getLogger(__name__)

# Tables included in 'users' backup
USERS_TABLES = ['users']

# Tables included in 'full' backup
FULL_TABLES = [
    'users', 'user_sessions', 'chat_sessions', 'messages',
    'session_visits', 'documents', 'model_configs', 'user_storage'
]

# Directories included in 'full' backup
FULL_DIRS = ['data/documents', 'data/uploads']


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        from flask import session
        if not session.get('is_admin'):
            return jsonify({'error': 'Forbidden'}), 403
        return f(*args, **kwargs)
    return decorated


def _get_project_root():
    """Get project root directory."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ensure_backup_dir():
    """Ensure backup directory exists."""
    base = os.path.join(_get_project_root(), 'data', 'db_backups')
    os.makedirs(base, exist_ok=True)
    return base


# ============================================================
# LIST backups
# ============================================================
@bp.route('/', methods=['GET'])
@admin_required
def list_backups():
    """Return list of available backup files."""
    backup_dir = _ensure_backup_dir()
    files = []

    for fpath in sorted(glob.glob(os.path.join(backup_dir, '*.tar.gz')), reverse=True):
        fname = os.path.basename(fpath)
        try:
            size = os.path.getsize(fpath)
            mtime = os.path.getmtime(fpath)
            # Determine type from filename
            if fname.startswith('users_'):
                btype = 'users'
            elif fname.startswith('full_'):
                btype = 'full'
            else:
                btype = 'unknown'

            # Read metadata from archive if available
            meta = _read_archive_metadata(fpath)

            files.append({
                'filename': fname,
                'type': btype,
                'size': size,
                'created_at': datetime.fromtimestamp(mtime).isoformat(),
                'metadata': meta
            })
        except Exception as e:
            logger.warning(f"Error reading backup file {fname}: {e}")

    return jsonify(files)


# ============================================================
# CREATE backup
# ============================================================
@bp.route('/create', methods=['POST'])
@admin_required
def create_backup():
    """Create a new backup archive."""
    data = request.get_json(silent=True) or {}
    backup_type = data.get('type', 'full')  # 'users' or 'full'

    if backup_type not in ('users', 'full'):
        return jsonify({'error': 'Invalid backup type'}), 400

    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    filename = f"{backup_type}_{timestamp}.tar.gz"
    backup_dir = _ensure_backup_dir()
    archive_path = os.path.join(backup_dir, filename)

    try:
        with tarfile.open(archive_path, 'w:gz') as tar:
            # 1. SQL dump using pg_dump
            tables = USERS_TABLES if backup_type == 'users' else FULL_TABLES
            dump = _export_pg_dump(tables)

            with tempfile.NamedTemporaryFile(mode='w', suffix='.sql', delete=False, encoding='utf-8') as tmp:
                tmp.write(dump)
                tmp_path = tmp.name

            tar.add(tmp_path, arcname='db_dump.sql')
            os.unlink(tmp_path)

            # 2. For 'full' backup: include documents and uploads directories
            if backup_type == 'full':
                project_root = _get_project_root()
                for dir_name in FULL_DIRS:
                    full_dir = os.path.join(project_root, dir_name)
                    if os.path.exists(full_dir):
                        tar.add(full_dir, arcname=os.path.basename(full_dir))

            # 3. Metadata
            meta = {
                'type': backup_type,
                'created_at': datetime.now().isoformat(),
                'database_type': 'postgresql',
                'tables': tables,
                'version': '8.0'
            }
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as tmp:
                json.dump(meta, tmp, indent=2, ensure_ascii=False)
                meta_path = tmp.name

            tar.add(meta_path, arcname='metadata.json')
            os.unlink(meta_path)

        logger.info(f"Backup created: {filename} ({backup_type})")
        return jsonify({
            'status': 'ok',
            'filename': filename,
            'type': backup_type,
            'size': os.path.getsize(archive_path)
        })

    except Exception as e:
        logger.error(f"Backup creation failed: {e}", exc_info=True)
        if os.path.exists(archive_path):
            os.unlink(archive_path)
        return jsonify({'error': str(e)}), 500


# ============================================================
# RESTORE backup
# ============================================================
@bp.route('/restore', methods=['POST'])
@admin_required
def restore_backup():
    """Restore from a backup archive."""
    data = request.get_json(silent=True) or {}
    filename = data.get('filename')
    if not filename:
        return jsonify({'error': 'filename is required'}), 400

    backup_dir = _ensure_backup_dir()
    archive_path = os.path.join(backup_dir, filename)

    if not os.path.exists(archive_path):
        return jsonify({'error': 'Backup file not found'}), 404

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            with tarfile.open(archive_path, 'r:gz') as tar:
                tar.extractall(tmpdir)

            # Read metadata
            meta_path = os.path.join(tmpdir, 'metadata.json')
            if os.path.exists(meta_path):
                with open(meta_path) as f:
                    meta = json.load(f)
            else:
                meta = {'type': 'unknown'}

            backup_type = meta.get('type', 'unknown')

            # 1. Restore SQL dump
            dump_path = os.path.join(tmpdir, 'db_dump.sql')
            if os.path.exists(dump_path):
                _import_sql(dump_path, backup_type)

            # 2. For 'full' backup: restore files
            if backup_type == 'full':
                project_root = _get_project_root()
                for dir_name in FULL_DIRS:
                    src = os.path.join(tmpdir, os.path.basename(dir_name))
                    dst = os.path.join(project_root, dir_name)
                    if os.path.exists(src):
                        # Remove existing files and copy from backup
                        if os.path.exists(dst):
                            shutil.rmtree(dst)
                        shutil.copytree(src, dst)
                        logger.info(f"Restored directory: {dir_name}")

        logger.info(f"Backup restored: {filename}")
        return jsonify({
            'status': 'ok',
            'filename': filename,
            'type': backup_type
        })

    except Exception as e:
        logger.error(f"Backup restore failed: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ============================================================
# DELETE backup
# ============================================================
@bp.route('/<path:filename>', methods=['DELETE'])
@admin_required
def delete_backup(filename):
    """Delete a backup file."""
    backup_dir = _ensure_backup_dir()
    fpath = os.path.join(backup_dir, filename)

    # Security: prevent path traversal
    if not os.path.abspath(fpath).startswith(os.path.abspath(backup_dir)):
        abort(403)

    if not os.path.exists(fpath):
        return jsonify({'error': 'File not found'}), 404

    os.unlink(fpath)
    return jsonify({'status': 'ok'})


# ============================================================
# DOWNLOAD backup
# ============================================================
@bp.route('/<path:filename>/download', methods=['GET'])
@admin_required
def download_backup(filename):
    """Download a backup file."""
    backup_dir = _ensure_backup_dir()
    fpath = os.path.join(backup_dir, filename)

    # Security: prevent path traversal
    if not os.path.abspath(fpath).startswith(os.path.abspath(backup_dir)):
        abort(403)

    if not os.path.exists(fpath):
        abort(404)

    return send_file(fpath, as_attachment=True, download_name=filename)


# ============================================================
# Helpers
# ============================================================

def _export_pg_dump(tables):
    """Export specified tables as SQL INSERT statements from PostgreSQL."""
    lines = []
    lines.append('-- FLAI Backup')
    lines.append(f'-- Date: {datetime.now().isoformat()}')
    lines.append('-- Source: PostgreSQL')
    lines.append('')

    with get_db() as conn:
        c = conn.cursor()
        for table in tables:
            try:
                c.execute(f"SELECT * FROM {table}")
                rows = c.fetchall()
                if rows:
                    col_names = [desc[0] for desc in c.description]
                    lines.append(f"DELETE FROM {table};")
                    for row in rows:
                        values = []
                        for i, val in enumerate(row):
                            if val is None:
                                values.append('NULL')
                            elif isinstance(val, bool):
                                values.append('TRUE' if val else 'FALSE')
                            elif isinstance(val, (int, float)):
                                values.append(str(val))
                            elif isinstance(val, datetime):
                                values.append(f"'{val.isoformat()}'")
                            else:
                                escaped = str(val).replace("'", "''")
                                values.append(f"'{escaped}'")
                        cols_str = ', '.join(col_names)
                        vals_str = ', '.join(values)
                        lines.append(f"INSERT INTO {table} ({cols_str}) VALUES ({vals_str});")
                    lines.append('')
            except Exception as e:
                lines.append(f'-- Error exporting table {table}: {e}')
                lines.append('')

    return '\n'.join(lines)


def _import_sql(dump_path, backup_type):
    """Import SQL dump into PostgreSQL."""
    with open(dump_path, 'r') as f:
        sql = f.read()

    with get_db() as conn:
        c = conn.cursor()
        # Execute statements one by one
        for statement in sql.split(';'):
            statement = statement.strip()
            if statement and not statement.startswith('--'):
                try:
                    c.execute(statement)
                except Exception as e:
                    logger.warning(f"SQL error during restore: {e}")

    # Reset sequences
    with get_db() as conn:
        c = conn.cursor()
        for table in ['users', 'messages']:
            try:
                c.execute(f"SELECT setval('{table}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {table}))")
            except Exception:
                pass


def _read_archive_metadata(archive_path):
    """Read metadata.json from a tar.gz archive."""
    try:
        with tarfile.open(archive_path, 'r:gz') as tar:
            member = tar.getmember('metadata.json')
            f = tar.extractfile(member)
            if f:
                return json.loads(f.read().decode('utf-8'))
    except Exception:
        pass
    return None
