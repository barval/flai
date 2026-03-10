# app/routes_admin.py
# Admin panel routes - handles user management and statistics

import json
import logging
import os
from flask import Blueprint, render_template, session, jsonify, request, current_app
from functools import wraps
from flask_babel import gettext as _
from app.userdb import (
    list_users, create_user, update_user, delete_user,
    get_user_by_login, update_password
)
from app.db import (
    get_db as get_chat_db, CHAT_DB_PATH,
    get_user_file_count, get_user_document_count, get_documents_total_size
)
from app.userdb import USER_DB_PATH

bp = Blueprint('admin', __name__, url_prefix='/admin')
logger = logging.getLogger(__name__)


def get_file_size_bytes(path):
    """Get file size in bytes."""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def get_folder_size_bytes(folder_path):
    """Get total size of all files in a folder recursively."""
    total_size = 0
    if not os.path.exists(folder_path):
        return 0
    for dirpath, dirnames, filenames in os.walk(folder_path):
        for filename in filenames:
            file_path = os.path.join(dirpath, filename)
            try:
                total_size += os.path.getsize(file_path)
            except OSError:
                continue
    return total_size


def admin_required(f):
    """Decorator to require admin privileges."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('is_admin'):
            return jsonify({'error': 'Forbidden'}), 403
        return f(*args, **kwargs)
    return decorated


@bp.route('/')
@admin_required
def admin_panel():
    """Render admin panel with database sizes."""
    rooms = {}
    if 'cam' in current_app.modules and current_app.modules['cam'].available:
        rooms = current_app.modules['cam'].get_all_rooms()
    
    chat_db_size = get_file_size_bytes(CHAT_DB_PATH)
    user_db_size = get_file_size_bytes(USER_DB_PATH)
    uploads_folder = current_app.config.get('UPLOAD_FOLDER', 'data/uploads')
    files_db_size = get_folder_size_bytes(uploads_folder)
    documents_folder = current_app.config.get('DOCUMENTS_FOLDER', 'data/documents')
    documents_db_size = get_folder_size_bytes(documents_folder)
    
    return render_template('admin.html',
                          rooms=rooms,
                          chat_db_size=chat_db_size,
                          user_db_size=user_db_size,
                          files_db_size=files_db_size,
                          documents_db_size=documents_db_size)


@bp.route('/api/users', methods=['GET'])
@admin_required
def get_users():
    """Get list of all users with stats."""
    try:
        users = list_users(exclude_admin=True)
        result = []
        with get_chat_db() as conn:
            for u in users:
                stats = conn.execute('''
                    SELECT COUNT(DISTINCT cs.id) as sessions, COUNT(m.id) as messages
                    FROM chat_sessions cs
                    LEFT JOIN messages m ON cs.id = m.session_id
                    WHERE cs.user_id = ?
                ''', (u['login'],)).fetchone()
                u_dict = dict(u)
                u_dict['sessions_count'] = stats['sessions']
                u_dict['messages_count'] = stats['messages']
                u_dict['files_count'] = get_user_file_count(u['login'])
                u_dict['documents_count'] = get_user_document_count(u['login'])
                if u_dict['camera_permissions']:
                    try:
                        u_dict['camera_permissions'] = json.loads(u_dict['camera_permissions'])
                    except:
                        u_dict['camera_permissions'] = []
                else:
                    u_dict['camera_permissions'] = []
                result.append(u_dict)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in get_users: {str(e)}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500


@bp.route('/api/users', methods=['POST'])
@admin_required
def add_user():
    """Create a new user."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON data'}), 400
        
        login = data.get('login')
        password = data.get('password')
        name = data.get('name')
        service_class = data.get('service_class', 2)
        is_active = data.get('is_active', True)
        camera_permissions = data.get('camera_permissions')
        
        if not login or not password or not name:
            return jsonify({'error': _('Missing fields')}), 400
        if get_user_by_login(login):
            return jsonify({'error': _('Login already exists')}), 400
        
        create_user(
            login=login,
            password=password,
            name=name,
            service_class=service_class,
            is_admin=False,
            camera_permissions=camera_permissions
        )
        if not is_active:
            update_user(login, is_active=False)
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f"Error in add_user: {str(e)}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500


@bp.route('/api/users/<login>', methods=['PUT'])
@admin_required
def update_user_data(login):
    """Update user data."""
    try:
        data = request.get_json()
        name = data.get('name')
        service_class = data.get('service_class')
        is_active = data.get('is_active')
        camera_permissions = data.get('camera_permissions')
        
        update_user(
            login=login,
            name=name,
            service_class=service_class,
            is_active=is_active,
            camera_permissions=camera_permissions
        )
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f"Error in update_user_data for {login}: {str(e)}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500


@bp.route('/api/users/<login>/password', methods=['PUT'])
@admin_required
def change_password(login):
    """Change user password."""
    try:
        data = request.get_json()
        new_password = data.get('new_password')
        if not new_password:
            return jsonify({'error': _('New password not specified')}), 400
        update_password(login, new_password)
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f"Error in change_password for {login}: {str(e)}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500


@bp.route('/api/users/<login>', methods=['DELETE'])
@admin_required
def delete_user_account(login):
    """Delete a user account."""
    try:
        delete_user(login)
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f"Error in delete_user_account for {login}: {str(e)}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500


@bp.route('/api/stats')
@admin_required
def get_stats():
    """Return current sizes of databases and folders in bytes."""
    try:
        chat_db_size = get_file_size_bytes(CHAT_DB_PATH)
        user_db_size = get_file_size_bytes(USER_DB_PATH)
        uploads_folder = current_app.config.get('UPLOAD_FOLDER', 'data/uploads')
        files_db_size = get_folder_size_bytes(uploads_folder)
        documents_folder = current_app.config.get('DOCUMENTS_FOLDER', 'data/documents')
        documents_db_size = get_folder_size_bytes(documents_folder)
        
        return jsonify({
            'chat_db_size': chat_db_size,
            'user_db_size': user_db_size,
            'files_db_size': files_db_size,
            'documents_db_size': documents_db_size
        })
    except Exception as e:
        logger.error(f"Error in get_stats: {str(e)}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500