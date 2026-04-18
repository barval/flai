# app/routes/admin.py
import json
import logging
import os
import requests
from functools import wraps
from flask import Blueprint, render_template, session, jsonify, request, current_app
from flask_babel import gettext as _
from app.userdb import (
    list_users, create_user, update_user, delete_user,
    get_user_by_login, update_password
)
from app.db import (
    get_user_file_count, get_user_document_count
)
from app.database import get_db
from app.validators import validate_user_input, validate_model_config_update, ValidationError

bp = Blueprint('admin', __name__, url_prefix='/admin')
logger = logging.getLogger(__name__)


def get_file_size_bytes(path: str) -> int:
    """Get file size in bytes."""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def get_folder_size_bytes(folder_path: str) -> int:
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
            return jsonify({'error': _('Forbidden')}), 403
        return f(*args, **kwargs)
    return decorated


@bp.route('/')
@admin_required
def admin_panel():
    """Render admin panel with database sizes."""
    rooms = {}
    if 'cam' in current_app.modules and current_app.modules['cam'].available:
        rooms = current_app.modules['cam'].get_all_rooms()

    # PostgreSQL size is tracked on the server, not accessible from app container
    user_db_size = 0
    uploads_folder = current_app.config.get('UPLOAD_FOLDER', 'data/uploads')
    files_db_size = get_folder_size_bytes(uploads_folder)
    documents_folder = current_app.config.get('DOCUMENTS_FOLDER', 'data/documents')
    documents_db_size = get_folder_size_bytes(documents_folder)

    # Get chunk configuration from RAG module
    chunk_size = 500
    chunk_overlap = 50
    chunk_strategy = 'fixed'
    if 'rag' in current_app.modules and current_app.modules['rag']:
        rag = current_app.modules['rag']
        chunk_size = rag.chunk_size
        chunk_overlap = rag.chunk_overlap
        chunk_strategy = rag.chunk_strategy

    return render_template('admin.html',
                          rooms=rooms,
                          chat_db_size=0,
                          user_db_size=user_db_size,
                          files_db_size=files_db_size,
                          documents_db_size=documents_db_size,
                          chunk_size=chunk_size,
                          chunk_overlap=chunk_overlap,
                          chunk_strategy=chunk_strategy)


@bp.route('/api/users', methods=['GET'])
@admin_required
def get_users():
    """Get list of all users with stats.
    Optimized to avoid N+1 queries by using a single JOIN query.
    """
    try:
        users = list_users(exclude_admin=True)
        result = []

        # Build a single optimized query with all stats using JOINs
        with get_db() as conn:
            for u in users:
                # Single query with subqueries for all stats - no N+1
                c = conn.cursor()
                c.execute('''
                    SELECT
                        COUNT(DISTINCT cs.id) as sessions,
                        COUNT(m.id) as messages,
                        (SELECT COUNT(*) FROM documents
                         WHERE user_id = %s AND file_ext IN ('.pdf', '.doc', '.docx', '.txt')) as documents_count,
                        (SELECT COUNT(DISTINCT m2.file_path)
                         FROM messages m2
                         JOIN chat_sessions cs2 ON m2.session_id = cs2.id
                         WHERE cs2.user_id = %s AND m2.file_path IS NOT NULL AND m2.file_path != '') as files_count
                    FROM chat_sessions cs
                    LEFT JOIN messages m ON cs.id = m.session_id
                    WHERE cs.user_id = %s
                ''', (u['login'], u['login'], u['login']))
                stats = c.fetchone()
                
                u_dict = dict(u)
                u_dict['sessions_count'] = stats['sessions'] if stats else 0
                u_dict['messages_count'] = stats['messages'] if stats else 0
                u_dict['files_count'] = stats['files_count'] if stats else 0
                u_dict['documents_count'] = stats['documents_count'] if stats else 0
                
                if u_dict['camera_permissions']:
                    try:
                        u_dict['camera_permissions'] = json.loads(u_dict['camera_permissions'])
                    except json.JSONDecodeError:
                        u_dict['camera_permissions'] = []
                else:
                    u_dict['camera_permissions'] = []
                result.append(u_dict)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in get_users: {str(e)}", exc_info=True)
        return jsonify({'error': _('Internal server error')}), 500


@bp.route('/api/users', methods=['POST'])
@admin_required
def add_user():
    """Create a new user."""
    try:
        data = request.get_json()
        try:
            data = validate_user_input(data)
        except ValidationError as e:
            return jsonify({'error': str(e)}), 400

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
    except ValidationError:
        raise
    except Exception as e:
        logger.error(f"Error in add_user: {str(e)}", exc_info=True)
        return jsonify({'error': _('Internal server error')}), 500


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
        return jsonify({'error': _('Internal server error')}), 500


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
        return jsonify({'error': _('Internal server error')}), 500


@bp.route('/api/users/<login>', methods=['DELETE'])
@admin_required
def delete_user_account(login):
    """Delete a user account."""
    try:
        delete_user(login)
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f"Error in delete_user_account for {login}: {str(e)}", exc_info=True)
        return jsonify({'error': _('Internal server error')}), 500


@bp.route('/api/stats')
@admin_required
def get_stats():
    """Return current sizes of databases and folders in bytes."""
    try:
        # PostgreSQL size is tracked on the server, not accessible from app container
        chat_db_size = 0
        user_db_size = 0
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
        return jsonify({'error': _('Internal server error')}), 500


# ==================== ENDPOINTS FOR MODEL MANAGEMENT ====================

@bp.route('/api/llamacpp/check', methods=['GET'])
@admin_required
def llamacpp_check():
    """Check if llama-server is reachable at given URL via /v1/models."""
    service_url = request.args.get('url')
    if not service_url:
        return jsonify({'available': False, 'error': _('Missing url')}), 400
    try:
        response = requests.get(f"{service_url.rstrip('/')}/v1/models", timeout=5)
        if response.status_code == 200:
            return jsonify({'available': True})
        else:
            return jsonify({'available': False, 'error': _('HTTP error {status}').format(status=response.status_code)})
    except Exception as e:
        return jsonify({'available': False, 'error': str(e)})


@bp.route('/api/llamacpp/models', methods=['GET'])
@admin_required
def llamacpp_models():
    """Return list of available models from llama-server via /v1/models."""
    service_url = request.args.get('url')
    if not service_url:
        return jsonify({'error': _('Missing "url" parameter')}), 400
    try:
        resp = requests.get(f"{service_url.rstrip('/')}/v1/models", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            # OpenAI format: {"data": [{"id": "model1", ...}, ...]}
            models = [m['id'] for m in data.get('data', [])]
            return jsonify(models)
        else:
            return jsonify({'error': _('llama-server returned {status}').format(status=resp.status_code)}), 500
    except Exception as e:
        current_app.logger.error(f"Error fetching llama.cpp models from {service_url}: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/llamacpp/model/<path:name>', methods=['GET'])
@admin_required
def llamacpp_model_info(name):
    """Return information about a specific model from llama-server.
    The llama.cpp router doesn't support /v1/models/{name}, so we parse
    what we can from the model list response and the filename.
    """
    service_url = request.args.get('url')
    if not service_url:
        return jsonify({'error': _('Missing "url" parameter')}), 400
    try:
        resp = requests.get(f"{service_url.rstrip('/')}/v1/models", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            model_data = None
            for m in data.get('data', []):
                if m.get('id') == name:
                    model_data = m
                    break

            # Parse quantization from filename
            from app.utils import extract_quantization; quantization = extract_quantization(name)

            # Determine if it's likely an embedding model
            is_embedding = 'embed' in name.lower() or 'bge' in name.lower()
            # Determine if it's likely a vision model
            is_vision = 'vl' in name.lower() or 'vision' in name.lower()

            # Known model metadata (architecture, parameters, context, embedding)
            # This is a fallback when router doesn't return detailed metadata
            KNOWN_MODELS = {
                'Qwen3-4B-Instruct-2507-Q4_K_M': {
                    'arch': 'qwen3', 'params': '~4B', 'ctx': 32768, 'emb': 2560
                },
                'gemma-4-26B-A4B-it-MXFP4_MOE': {
                    'arch': 'gemma', 'params': '~26B (MoE)', 'ctx': 32768, 'emb': 4608
                },
                'gpt-oss-20b-mxfp4': {
                    'arch': 'gpt-oss', 'params': '~20B', 'ctx': 32768, 'emb': 5120
                },
                'Qwen3VL-8B-Instruct-Q4_K_M': {
                    'arch': 'qwen3-vl', 'params': '~8B', 'ctx': 32768, 'emb': 4096
                },
                'bge-m3-Q8_0': {
                    'arch': 'bge', 'params': '~567M', 'ctx': 8192, 'emb': 1024
                },
            }

            known = KNOWN_MODELS.get(name, {})

            # Determine architecture family from name (override if not known)
            if not known.get('arch'):
                name_lower = name.lower()
                if 'qwen3' in name_lower and 'vl' in name_lower:
                    arch = 'qwen3-vl'
                elif 'qwen3' in name_lower:
                    arch = 'qwen3'
                elif 'qwen2.5' in name_lower or 'qwen2' in name_lower:
                    arch = 'qwen2.5'
                elif 'gemma' in name_lower:
                    arch = 'gemma'
                elif 'gpt-oss' in name_lower:
                    arch = 'gpt-oss'
                elif 'bge' in name_lower:
                    arch = 'bge'
                elif 'llama' in name_lower:
                    arch = 'llama'
                elif 'mistral' in name_lower:
                    arch = 'mistral'
                else:
                    arch = 'N/A'
            else:
                arch = known['arch']

            # Determine parameter count (use known or parse from filename)
            if known.get('params'):
                params = known['params']
            else:
                params = 'N/A'
                for hint, label in [
                    ('70b', '~70B'), ('27b', '~27B'), ('20b', '~20B'),
                    ('26b', '~26B (MoE)'), ('a4b', '~26B (MoE)'),
                    ('14b', '~14B'), ('9b', '~9B'), ('8b', '~8B'),
                    ('7b', '~7B'), ('4b', '~4B'), ('3b', '~3B'),
                    ('1b', '~1B')
                ]:
                    if hint in name.lower():
                        params = label
                        break

            # Context length and embedding (from known or N/A)
            ctx_length = known.get('ctx', 'N/A')
            emb_length = known.get('emb', 'N/A')

            status = 'unknown'
            if model_data:
                status = model_data.get('status', {}).get('value', 'unknown')

            return jsonify({
                'id': name,
                'architecture': arch,
                'parameters': params,
                'quantization': quantization,
                'context_length': ctx_length,
                'embedding_length': emb_length,
                'status': status,
                'type': 'embedding' if is_embedding else ('vision' if is_vision else 'text'),
            })
        else:
            return jsonify({'error': _('llama-server returned {status}').format(status=resp.status_code)}), 500
    except Exception as e:
        current_app.logger.error(f"Error fetching llama.cpp model info for {name}: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/model_configs', methods=['GET'])
@admin_required
def get_model_configs():
    """Return all model configurations from the database."""
    from app.model_config import reload_all_model_configs
    configs = reload_all_model_configs()
    return jsonify(configs)


@bp.route('/api/model_configs/<module>', methods=['PUT'])
@admin_required
def update_model_config(module):
    """Update configuration for a specific module."""
    from app.model_config import invalidate_model_config_cache, get_model_config
    from app.database import get_db

    data = request.get_json()
    try:
        updates = validate_model_config_update(data, module)
    except ValidationError as e:
        return jsonify({'error': str(e)}), 400

    # Get old model_name BEFORE update (for embedding change detection)
    old_model = None
    if module == 'embedding':
        old_config = get_model_config('embedding')
        old_model = old_config.get('model_name') if old_config else None
        current_app.logger.info(f"Embedding old_model from config: '{old_model}'")

    with get_db() as conn:
        c = conn.cursor()
        set_clause = ', '.join([f"{k} = %s" for k in updates.keys()])
        values = list(updates.values()) + [module]
        c.execute(f'''
            UPDATE model_configs
            SET {set_clause}, updated_at = CURRENT_TIMESTAMP
            WHERE module = %s
        ''', values)
        conn.commit()

    # Invalidate cache for updated module
    invalidate_model_config_cache(module)

    result = {'status': 'ok'}
    if module == 'embedding':
        new_model = updates.get('model_name')
        current_app.logger.info(f"Embedding new_model: '{new_model}', old_model: '{old_model}'")
        # Only trigger reindex if the model actually CHANGED
        if new_model and old_model is not None and new_model != old_model:
            current_app.logger.info(f"Embedding model changed from '{old_model}' to '{new_model}', starting reindex all")
            current_app.request_queue.add_reindex_all_task(lang='ru')
            result['model_name'] = new_model
            result['reindex_triggered'] = True
        elif new_model == old_model:
            current_app.logger.info(f"Embedding model '{new_model}' saved but unchanged — skipping reindex")
            result['model_name'] = new_model
            result['reindex_triggered'] = False
        else:
            result['model_name'] = new_model or old_model
            result['reindex_triggered'] = False
            current_app.logger.info(f"Embedding model save: new='{new_model}', old='{old_model}' — no reindex")
    else:
        result['model_name'] = updates.get('model_name')

    return jsonify(result)


@bp.route('/api/admin/reindex-all', methods=['POST'])
@admin_required
def api_admin_reindex_all():
    """Manually trigger reindex of all documents."""
    current_app.logger.info(f"Reindex API called, is_admin={session.get('is_admin')}")
    try:
        if not hasattr(current_app, 'request_queue') or not current_app.request_queue:
            return jsonify({'ok': False, 'error': 'Request queue not available'}), 500
        lang = request.json.get('lang', 'ru') if request.is_json else 'ru'
        current_app.request_queue.add_reindex_all_task(lang=lang)
        current_app.logger.info("Manual reindex all documents triggered via admin")
        return jsonify({'ok': True, 'message': 'Reindex started'})
    except Exception as e:
        current_app.logger.error(f"Error triggering reindex: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@bp.route('/api/admin/chunks', methods=['PUT'])
@admin_required
def api_save_chunks_config():
    """Save chunk configuration and trigger reindex if changed."""
    from app.database import get_db
    
    data = request.get_json()
    new_chunk_size = data.get('chunk_size', 500)
    new_chunk_overlap = data.get('chunk_overlap', 50)
    new_chunk_strategy = data.get('chunk_strategy', 'fixed')

    # Get original config
    rag = current_app.modules.get('rag')
    if not rag:
        return jsonify({'ok': False, 'error': 'RAG module not available'}), 500

    old_chunk_size = rag.chunk_size
    old_chunk_overlap = rag.chunk_overlap
    old_chunk_strategy = rag.chunk_strategy

    # Check if anything changed
    config_changed = (new_chunk_size != old_chunk_size or 
                   new_chunk_overlap != old_chunk_overlap or 
                   new_chunk_strategy != old_chunk_strategy)

    if config_changed:
        # Save to config table
        with get_db() as conn:
            c = conn.cursor()
            c.execute('''
                INSERT INTO model_configs (module, chunk_size, chunk_overlap, chunk_strategy, updated_at)
                VALUES ('chunks', %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (module) DO UPDATE SET
                    chunk_size = EXCLUDED.chunk_size,
                    chunk_overlap = EXCLUDED.chunk_overlap,
                    chunk_strategy = EXCLUDED.chunk_strategy,
                    updated_at = CURRENT_TIMESTAMP
            ''', (new_chunk_size, new_chunk_overlap, new_chunk_strategy))
            conn.commit()

        # Update RAG module values
        rag.chunk_size = new_chunk_size
        rag.chunk_overlap = new_chunk_overlap
        rag.chunk_strategy = new_chunk_strategy

        current_app.logger.info(f"Chunk config changed: size={old_chunk_size}->{new_chunk_size}, overlap={old_chunk_overlap}->{new_chunk_overlap}, strategy={old_chunk_strategy}->{new_chunk_strategy}")

        # Trigger reindex
        if hasattr(current_app, 'request_queue') and current_app.request_queue:
            current_app.request_queue.add_reindex_all_task(lang='ru')
            current_app.logger.info("Reindex triggered due to chunk config change")
            return jsonify({'ok': True, 'reindex_triggered': True})
        else:
            return jsonify({'ok': True, 'reindex_triggered': False, 'error': 'Queue not available'})
    else:
        current_app.logger.info("Chunk config unchanged")
        return jsonify({'ok': True, 'reindex_triggered': False})