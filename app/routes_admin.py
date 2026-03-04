import json
import logging
from flask import Blueprint, render_template, session, jsonify, request, current_app
from functools import wraps
from app.userdb import (
    list_users, create_user, update_user, delete_user,
    get_user_by_login, update_password
)
from app.db import get_db as get_chat_db

bp = Blueprint('admin', __name__, url_prefix='/admin')
logger = logging.getLogger(__name__)

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('is_admin'):
            return jsonify({'error': 'Forbidden'}), 403
        return f(*args, **kwargs)
    return decorated

@bp.route('/')
@admin_required
def admin_panel():
    rooms = {}
    if 'cam' in current_app.modules and current_app.modules['cam'].available:
        rooms = current_app.modules['cam'].get_all_rooms()
    return render_template('admin.html', rooms=rooms)

@bp.route('/api/users', methods=['GET'])
@admin_required
def get_users():
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
            return jsonify({'error': 'Не все поля заполнены'}), 400

        if get_user_by_login(login):
            return jsonify({'error': 'Логин уже существует'}), 400

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
    try:
        data = request.get_json()
        new_password = data.get('new_password')
        if not new_password:
            return jsonify({'error': 'Новый пароль не указан'}), 400
        update_password(login, new_password)
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f"Error in change_password for {login}: {str(e)}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500

@bp.route('/api/users/<login>', methods=['DELETE'])
@admin_required
def delete_user_account(login):
    try:
        delete_user(login)
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f"Error in delete_user_account for {login}: {str(e)}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500