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
from app.model_config import get_model_config
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
    rag_top_k = 20
    max_top_k = 100
    rag_threshold_default = 0.3
    rag_threshold_reasoning = 0.3
    if 'rag' in current_app.modules and current_app.modules['rag']:
        rag = current_app.modules['rag']
        chunk_size = rag.chunk_size
        chunk_overlap = rag.chunk_overlap
        chunk_strategy = rag.chunk_strategy
        rag_top_k = rag.top_k

    # Calculate max_top_k: 30% of reasoning model context / chunk_size (in tokens)
    # chunk_size is in characters, need to convert to tokens (TOKEN_CHARS ~3 chars/token)
    reasoning_config = get_model_config('reasoning')
    if reasoning_config:
        ctx_length = reasoning_config.get('context_length', 8192)
        max_context_tokens = int(ctx_length * 0.30)
        token_chars = current_app.config.get('TOKEN_CHARS', 3)
        chunk_size_tokens = chunk_size / token_chars
        max_top_k = max(1, int(max_context_tokens / chunk_size_tokens))

    # Get RAG thresholds from config
    rag_threshold_default = current_app.config.get('RAG_RELEVANCE_THRESHOLD_DEFAULT', 0.3)
    rag_threshold_reasoning = current_app.config.get('RAG_RELEVANCE_THRESHOLD_REASONING', 0.3)

    return render_template('admin.html',
                          rooms=rooms,
                          chat_db_size=0,
                          user_db_size=user_db_size,
                          files_db_size=files_db_size,
                          documents_db_size=documents_db_size,
                          chunk_size=chunk_size,
                          chunk_overlap=chunk_overlap,
                          chunk_strategy=chunk_strategy,
                          rag_top_k=rag_top_k,
                          max_top_k=max_top_k,
                          rag_threshold_default=rag_threshold_default,
                          rag_threshold_reasoning=rag_threshold_reasoning)


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


@bp.route('/api/hardware')
@admin_required
def get_hardware():
    """Return hardware information for memory estimation.

    Gets GPU info from llamacpp container logs or via nvidia-smi.
    """
    try:
        import subprocess
        import re

        hw = {
            'gpu_name': None,
            'cuda_detected': False,
            'total_vram_mb': 0,
            'available_vram_mb': 0,
            'total_ram_mb': 0,
            'available_ram_mb': 0,
        }

        # Get RAM info from resource manager
        from app.resource_manager import get_resource_manager
        rm = get_resource_manager()
        rm_hw = rm.get_status()
        hw['total_ram_mb'] = rm_hw.get('total_ram_mb', 0)
        hw['available_ram_mb'] = rm_hw.get('available_ram_mb', 0)

        # Always try to parse from llamacpp logs first (most reliable)
        try:
            result = subprocess.run(
                ['docker', 'logs', 'flai-llamacpp'],
                timeout=5, capture_output=True, text=True
            )
            output = result.stdout + result.stderr
            # Parse GPU name: "Device 0: NVIDIA GeForce RTX 5060 Ti, ..."
            match = re.search(r'Device\s+0:\s+(.+?),', output)
            if match:
                hw['gpu_name'] = match.group(1).strip()
            # Parse VRAM: "Total VRAM: 15844 MiB"
            match = re.search(r'Total VRAM:\s*(\d+)\s*MiB', output)
            if match:
                hw['cuda_detected'] = True
                hw['total_vram_mb'] = int(match.group(1))
                hw['available_vram_mb'] = hw['total_vram_mb']
                logger.info(f"GPU from logs: {hw.get('gpu_name', 'GPU')}, {hw['total_vram_mb']}MB")
        except Exception as e:
            logger.warning(f"logs parse error: {e}")

        # Method 2: Try docker exec with nvidia-smi for more accurate available VRAM
        if hw['cuda_detected']:
            try:
                result = subprocess.run(
                    ['docker', 'exec', 'flai-llamacpp', 'nvidia-smi',
                     '--query-gpu=name,memory.total,memory.free',
                     '--format=csv,noheader,nounits'],
                    timeout=5, capture_output=True, text=True
                )
                if result.returncode == 0 and result.stdout.strip():
                    parts = result.stdout.strip().split(',')
                    if len(parts) >= 3:
                        # Update with more accurate info
                        hw['gpu_name'] = parts[0].strip()
                        hw['total_vram_mb'] = int(parts[1].strip())
                        hw['available_vram_mb'] = int(parts[2].strip())
                        logger.info(f"GPU from nvidia-smi: {hw['gpu_name']}, {hw['total_vram_mb']}MB free: {hw['available_vram_mb']}MB")
            except Exception as e:
                logger.warning(f"nvidia-smi exception: {e}")

        return jsonify(hw)
    except Exception as e:
        logger.error(f"Error in get_hardware: {str(e)}", exc_info=True)
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
            # Filter: only actual model files (with .gguf extension or proper model names)
            all_items = [m['id'] for m in data.get('data', [])]
            # Filter out section names and non-model entries
            exclude_keys = {'chat', 'embedding', 'multimodal', 'reasoning', 'chatgguf', 'embeddinggguf', 'multimodalgguf', 'reasoninggguf'}
            models = [m for m in all_items if m.lower() not in exclude_keys and ('.gguf' in m.lower() or any(c.isdigit() for c in m))]
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
    Reads context length directly from GGUF file metadata.
    Falls back to KNOWN_MODELS if GGUF reading fails.
    """
    service_url = request.args.get('url')
    use_gguf = request.args.get('gguf', 'true').lower() == 'true'

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

            from app.utils import extract_quantization; quantization = extract_quantization(name)

            is_embedding = 'embed' in name.lower() or 'bge' in name.lower()
            is_vision = 'vl' in name.lower() or 'vision' in name.lower()

            KNOWN_MODELS = {
                'Qwen3-4B-Instruct-2507-Q4_K_M': {
                    'arch': 'qwen3', 'params': '~4B', 'ctx': 32768, 'emb': 2560
                },
                'gemma-4-26B-A4B-it-MXFP4_MOE': {
                    'arch': 'gemma', 'params': '~26B (MoE)', 'ctx': 32768, 'emb': 4608
                },
                'gpt-oss-20b-Q4_K_M': {
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

            if known.get('params'):
                params = known['params']
            else:
                params = 'N/A'
                for hint, label in [
                    ('70b', '~70B'), ('32b', '~32B'), ('27b', '~27B'), ('20b', '~20B'),
                    ('26b', '~26B (MoE)'), ('a4b', '~26B (MoE)'),
                    ('14b', '~14B'), ('12b', '~12B'), ('9b', '~9B'), ('8b', '~8B'),
                    ('7b', '~7B'), ('4b', '~4B'), ('3b', '~3B'),
                    ('1b', '~1B')
                ]:
                    if hint in name.lower():
                        params = label
                        break

            ctx_length = None
            emb_length = None
            ctx_source = 'unknown'
            emb_source = 'unknown'

            if use_gguf:
                gguf_info = _get_gguf_metadata(name, service_url)
                if gguf_info.get('context_length'):
                    ctx_length = gguf_info['context_length']
                    ctx_source = 'gguf'
                if gguf_info.get('embedding_length'):
                    emb_length = gguf_info['embedding_length']
                    emb_source = 'gguf'

            if not ctx_length:
                if known.get('ctx'):
                    ctx_length = known['ctx']
                    ctx_source = 'known'
                else:
                    ctx_length = 'N/A'
                    ctx_source = 'none'

            if not emb_length:
                if known.get('emb'):
                    emb_length = known['emb']
                    emb_source = 'known'
                else:
                    emb_length = 'N/A'
                    emb_source = 'none'

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
                'context_source': ctx_source,
                'embedding_source': emb_source,
                'status': status,
                'type': 'embedding' if is_embedding else ('vision' if is_vision else 'text'),
                'block_count': gguf_info.get('block_count'),
                'file_size_mb': gguf_info.get('file_size_mb'),
            })
        else:
            return jsonify({'error': _('llama-server returned {status}').format(status=resp.status_code)}), 500
    except Exception as e:
        current_app.logger.error(f"Error fetching llama.cpp model info for {name}: {e}")
        return jsonify({'error': str(e)}), 500


def _get_gguf_metadata(model_name: str, service_url: str) -> dict:
    """Get cached GGUF metadata for a model.

    Args:
        model_name: Name of the model file
        service_url: URL of llama.cpp service (unused, kept for compatibility)

    Returns:
        Dict with context_length, embedding_length, etc.
    """
    info = {}

    try:
        from app.utils import get_gguf_models_cached

        models_dir = '/models'
        gguf_cache = get_gguf_models_cached(models_dir)

        model_key = model_name
        if model_key.endswith('.gguf'):
            model_key = model_key[:-5]

        if model_key in gguf_cache:
            cached = gguf_cache[model_key]
            if cached.get('context_length'):
                info['context_length'] = cached['context_length']
            if cached.get('embedding_length'):
                info['embedding_length'] = cached['embedding_length']
            if cached.get('architecture'):
                info['architecture'] = cached['architecture']
            if cached.get('block_count'):
                info['block_count'] = cached['block_count']
            if cached.get('file_size_mb'):
                info['file_size_mb'] = cached['file_size_mb']
            if cached.get('size_label'):
                info['parameters'] = cached['size_label']
            current_app.logger.debug(f"GGUF metadata from cache: {info}")
        else:
            current_app.logger.debug(f"Model not in GGUF cache: {model_key}")

    except ImportError:
        current_app.logger.debug("gguf library not installed")
    except Exception as e:
        current_app.logger.warning(f"Error reading GGUF metadata: {e}")

    return info


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
    from app.model_config import invalidate_model_config_cache
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

    if module == 'reasoning':
        from app.model_config import get_model_config
        reasoning_config = get_model_config('reasoning')
        if reasoning_config:
            ctx_length = reasoning_config.get('context_length', 8192)
            chunk_config = get_model_config('chunks')
            chunk_size = chunk_config.get('chunk_size', 500) if chunk_config else 500
            token_chars = current_app.config.get('TOKEN_CHARS', 3)
            max_context_tokens = int(ctx_length * 0.30)
            chunk_size_tokens = chunk_size / token_chars
            result['max_top_k'] = max(1, int(max_context_tokens / chunk_size_tokens))

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
    try:
        from app.database import get_db
        from app.model_config import get_model_config

        data = request.get_json()
        new_chunk_size = data.get('chunk_size', 500)
        new_chunk_overlap = data.get('chunk_overlap', 50)
        new_chunk_strategy = data.get('chunk_strategy', 'fixed')
        new_rag_top_k = data.get('rag_top_k', 20)
        new_threshold_default = data.get('rag_threshold_default', 0.3)
        new_threshold_reasoning = data.get('rag_threshold_reasoning', 0.3)

        # Get original config
        rag = current_app.modules.get('rag')
        if not rag:
            return jsonify({'ok': False, 'error': 'RAG module not available'}), 500

        old_chunk_size = rag.chunk_size
        old_chunk_overlap = rag.chunk_overlap
        old_chunk_strategy = rag.chunk_strategy
        old_rag_top_k = rag.top_k

        # Get old thresholds from config
        old_threshold_default = current_app.config.get('RAG_RELEVANCE_THRESHOLD_DEFAULT', 0.3)
        old_threshold_reasoning = current_app.config.get('RAG_RELEVANCE_THRESHOLD_REASONING', 0.3)

    # Check if anything changed
        config_changed = (new_chunk_size != old_chunk_size or
                       new_chunk_overlap != old_chunk_overlap or
                       new_chunk_strategy != old_chunk_strategy or
                       new_rag_top_k != old_rag_top_k or
                       new_threshold_default != old_threshold_default or
                       new_threshold_reasoning != old_threshold_reasoning)

        if config_changed:
            # Save chunk config to config table
            with get_db() as conn:
                c = conn.cursor()
                # Add top_k column if not exists
                c.execute('''
                    DO $$
                    BEGIN
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                      WHERE table_name = 'model_configs' AND column_name = 'top_k') THEN
                            ALTER TABLE model_configs ADD COLUMN top_k INTEGER;
                        END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                      WHERE table_name = 'model_configs' AND column_name = 'rag_threshold_default') THEN
                            ALTER TABLE model_configs ADD COLUMN rag_threshold_default FLOAT DEFAULT 0.3;
                        END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                      WHERE table_name = 'model_configs' AND column_name = 'rag_threshold_reasoning') THEN
                            ALTER TABLE model_configs ADD COLUMN rag_threshold_reasoning FLOAT DEFAULT 0.2;
                        END IF;
                    END
                    $$
                ''')
                c.execute('''
                    INSERT INTO model_configs (module, chunk_size, chunk_overlap, chunk_strategy, top_k, rag_threshold_default, rag_threshold_reasoning, updated_at)
                    VALUES ('chunks', %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (module) DO UPDATE SET
                        chunk_size = EXCLUDED.chunk_size,
                        chunk_overlap = EXCLUDED.chunk_overlap,
                        chunk_strategy = EXCLUDED.chunk_strategy,
                        top_k = EXCLUDED.top_k,
                        rag_threshold_default = EXCLUDED.rag_threshold_default,
                        rag_threshold_reasoning = EXCLUDED.rag_threshold_reasoning,
                        updated_at = CURRENT_TIMESTAMP
                ''', (new_chunk_size, new_chunk_overlap, new_chunk_strategy, new_rag_top_k, new_threshold_default, new_threshold_reasoning))
                conn.commit()

            # Update RAG module values
            rag.chunk_size = new_chunk_size
            rag.chunk_overlap = new_chunk_overlap
            rag.chunk_strategy = new_chunk_strategy
            rag.top_k = new_rag_top_k

            # Update thresholds in app config
            current_app.config['RAG_RELEVANCE_THRESHOLD_DEFAULT'] = new_threshold_default
            current_app.config['RAG_RELEVANCE_THRESHOLD_REASONING'] = new_threshold_reasoning

            current_app.logger.info(f"Chunk config changed: size={old_chunk_size}->{new_chunk_size}, overlap={old_chunk_overlap}->{new_chunk_overlap}, strategy={old_chunk_strategy}->{new_chunk_strategy}, top_k={old_rag_top_k}->{new_rag_top_k}, threshold_default={old_threshold_default}->{new_threshold_default}, threshold_reasoning={old_threshold_reasoning}->{new_threshold_reasoning}")

            # Trigger reindex only if chunking params changed
            reindex_triggered = False
            if new_chunk_size != old_chunk_size or new_chunk_strategy != old_chunk_strategy:
                if hasattr(current_app, 'request_queue') and current_app.request_queue:
                    current_app.request_queue.add_reindex_all_task(lang='ru')
                    current_app.logger.info("Reindex triggered due to chunk config change")
                    reindex_triggered = True

            return jsonify({'ok': True, 'reindex_triggered': reindex_triggered})
        else:
            current_app.logger.info("Chunk config unchanged")
            return jsonify({'ok': True, 'reindex_triggered': False})
    except Exception as e:
        current_app.logger.error(f"Error saving chunks config: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500