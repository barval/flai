# app/routes_admin.py
# Admin panel routes - handles user management and statistics

import json
import logging
import os
import sqlite3
import requests
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


# ==================== ENDPOINTS FOR MODEL MANAGEMENT ====================

@bp.route('/api/ollama/models', methods=['GET'])
@admin_required
def ollama_models():
    """Return list of available models from Ollama."""
    ollama_url = current_app.config.get('OLLAMA_URL')
    if not ollama_url:
        return jsonify({'error': 'OLLAMA_URL not configured'}), 500
    try:
        resp = requests.get(f"{ollama_url}/api/tags", timeout=5)
        if resp.status_code == 200:
            models = [m['name'] for m in resp.json().get('models', [])]
            return jsonify(models)
        else:
            return jsonify({'error': f'Ollama returned {resp.status_code}'}), 500
    except Exception as e:
        current_app.logger.error(f"Error fetching Ollama models: {e}")
        return jsonify({'error': str(e)}), 500


# Known embedding architectures (family names)
EMBEDDING_ARCHITECTURES = {
    'bert', 'bge', 'e5', 'snowflake-arctic-embed', 'minilm', 'nomic-embed', 'gte', 'qwen2embed'
}

# Known vision architectures (models that support images)
VISION_ARCHITECTURES = {
    'llava', 'moondream', 'qwen2vl', 'phi3v'
}


@bp.route('/api/ollama/model/<name>', methods=['GET'])
@admin_required
def ollama_model_info(name):
    """Return detailed information about a specific model via /api/show."""
    ollama_url = current_app.config.get('OLLAMA_URL')
    if not ollama_url:
        return jsonify({'error': 'OLLAMA_URL not configured'}), 500
    try:
        resp = requests.post(f"{ollama_url}/api/show", json={"model": name}, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            details = data.get('details', {})
            model_info = data.get('model_info', {})
            template = data.get('template', '')

            # Extract relevant fields
            context_length = None
            embedding_length = None
            is_embedding = False
            is_vision = False
            is_tools = False
            is_reasoning = False

            for k, v in model_info.items():
                if k.endswith('.context_length') or k == 'context_length':
                    context_length = v
                if k.endswith('.embedding_length') or k == 'embedding_length':
                    embedding_length = v
                # Vision indicators: keys containing "vision" or "mmproj"
                if 'vision' in k.lower() and v:
                    is_vision = True
                if 'mmproj' in k.lower() and v:
                    is_vision = True
                # Tools indicators (optional)
                if 'tools' in k.lower() and v:
                    is_tools = True

            architecture = details.get('family', '').lower()
            name_lower = name.lower()

            # --- Vision detection (enhanced) ---
            # 1. By architecture
            if architecture in VISION_ARCHITECTURES:
                is_vision = True
            # 2. By model name containing 'gemma3n'
            #if 'gemma3n' in name_lower:
            #    is_vision = True
            # 3. By families list
            families = details.get('families', [])
            if any('clip' in f.lower() or 'vision' in f.lower() for f in families):
                is_vision = True
            # 4. By template containing image placeholders
            if template and ('{{ .Images }}' in template or '{{ .Image }}' in template):
                is_vision = True

            # --- Reasoning detection (by name) ---
            #reasoning_keywords = ['r1', 'reasoning', 'o1', 'deepseek', 'qwq']
            #if any(kw in name_lower for kw in reasoning_keywords):
            #    is_reasoning = True

            # --- Embedding detection ---
            # If already vision, it's not embedding
            if is_vision:
                is_embedding = False
            else:
                # Heuristics for embedding models
                if embedding_length and int(embedding_length) > 0:
                    # Check architecture or name or empty template
                    if architecture in EMBEDDING_ARCHITECTURES:
                        is_embedding = True
                    elif 'embed' in name_lower:
                        is_embedding = True
                    elif not template or template.strip() == '':
                        is_embedding = True

            params = details.get('parameter_size', '')
            quantization = details.get('quantization_level', '')

            return jsonify({
                'name': name,
                'architecture': details.get('family', ''),
                'parameters': params,
                'quantization': quantization,
                'context_length': context_length,
                'embedding_length': embedding_length,
                'is_embedding': is_embedding,
                'is_vision': is_vision,
                'is_tools': is_tools,
                'is_reasoning': is_reasoning,
                'capabilities': {
                    'embedding': is_embedding,
                    'vision': is_vision,
                    'tools': is_tools,
                    'reasoning': is_reasoning
                }
            })
        else:
            return jsonify({'error': f'Ollama returned {resp.status_code}'}), 500
    except Exception as e:
        current_app.logger.error(f"Error fetching model info for {name}: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/model_configs', methods=['GET'])
@admin_required
def get_model_configs():
    """Return all model configurations from the database."""
    from app.db import get_db
    with get_db() as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT * FROM model_configs')
        rows = c.fetchall()
        configs = {row['module']: dict(row) for row in rows}
    return jsonify(configs)


@bp.route('/api/model_configs/<module>', methods=['PUT'])
@admin_required
def update_model_config(module):
    """Update configuration for a specific module."""
    data = request.get_json()
    allowed_fields = ['model_name', 'context_length', 'temperature', 'top_p', 'timeout']
    updates = {k: v for k, v in data.items() if k in allowed_fields}
    if not updates:
        return jsonify({'error': 'No valid fields'}), 400

    # Server-side validation
    if 'context_length' in updates and updates['context_length'] is not None:
        val = updates['context_length']
        if not isinstance(val, int) or val < 512:
            return jsonify({'error': _('Context length must be at least 512.')}), 400

    if 'temperature' in updates and updates['temperature'] is not None:
        val = updates['temperature']
        if not isinstance(val, (int, float)) or val < 0.0 or val > 2.0:
            return jsonify({'error': _('Temperature must be between 0.0 and 2.0.')}), 400

    if 'top_p' in updates and updates['top_p'] is not None:
        val = updates['top_p']
        if not isinstance(val, (int, float)) or val < 0.0 or val > 1.0:
            return jsonify({'error': _('Top P must be between 0.0 and 1.0.')}), 400

    if 'timeout' in updates and updates['timeout'] is not None:
        val = updates['timeout']
        if not isinstance(val, int) or val < 0 or val > 1200:
            return jsonify({'error': _('Timeout must be between 0 and 1200 seconds.')}), 400

    from app.db import get_db
    with get_db() as conn:
        c = conn.cursor()
        # Get old model name for embedding module
        old_model = None
        if module == 'embedding':
            c.execute('SELECT model_name FROM model_configs WHERE module = ?', (module,))
            row = c.fetchone()
            old_model = row[0] if row else None

        set_clause = ', '.join([f"{k}=?" for k in updates.keys()])
        values = list(updates.values()) + [module]
        c.execute(f'''
            UPDATE model_configs
            SET {set_clause}, updated_at = CURRENT_TIMESTAMP
            WHERE module = ?
        ''', values)
        conn.commit()

    # Reload configs into app.config
    _reload_model_configs(current_app)

    # If embedding model changed, start reindexing all documents
    result = {'status': 'ok'}
    if module == 'embedding':
        new_model = updates.get('model_name')
        if new_model and new_model != old_model:
            current_app.logger.info(f"Embedding model changed from {old_model} to {new_model}, starting reindex all")
            current_app.request_queue.add_reindex_all_task(lang='ru')  # default language
            # Include the new model name in the response so client can update its global variable
            result['model_name'] = new_model
        else:
            current_app.logger.info(f"Embedding model unchanged ({old_model}), no reindex triggered")
            # Still return the current model name (maybe old_model)
            result['model_name'] = old_model or new_model

    return jsonify(result)


def _reload_model_configs(app):
    """Helper to reload model configs from DB into app.config."""
    # Use direct connection without Flask's g to avoid context issues during startup
    import sqlite3
    from app.db import CHAT_DB_PATH
    conn = sqlite3.connect(CHAT_DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM model_configs')
    rows = c.fetchall()
    configs = {row['module']: dict(row) for row in rows}
    conn.close()
    app.config['MODEL_CONFIGS'] = configs