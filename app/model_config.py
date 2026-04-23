# app/model_config.py
import time
import logging
from app.database import get_db

logger = logging.getLogger(__name__)

# Cache with TTL: {module: {'data': dict, 'time': float}}
_MODEL_CONFIG_CACHE = {}
_CACHE_TTL = 60  # seconds


def get_model_config(module):
    """Retrieve model configuration for a module from the database."""
    now = time.time()
    entry = _MODEL_CONFIG_CACHE.get(module)

    if entry and (now - entry['time']) < _CACHE_TTL:
        return entry['data']

    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM model_configs WHERE module = %s', (module,))
            row = c.fetchone()
            if row:
                result = dict(row)
                _MODEL_CONFIG_CACHE[module] = {'data': result, 'time': now}
                return result
            return None
    except Exception as e:
        logger.error(f"Error reading model config from DB: {e}")
        return None


def invalidate_model_config_cache(module=None):
    """Invalidate the model config cache."""
    global _MODEL_CONFIG_CACHE
    if module:
        _MODEL_CONFIG_CACHE.pop(module, None)
    else:
        _MODEL_CONFIG_CACHE.clear()


def reload_all_model_configs():
    """Reload all model configurations from database into cache."""
    global _MODEL_CONFIG_CACHE
    _MODEL_CONFIG_CACHE.clear()
    now = time.time()

    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM model_configs')
            for row in c.fetchall():
                _MODEL_CONFIG_CACHE[row['module']] = {'data': dict(row), 'time': now}
            return {k: v['data'] for k, v in _MODEL_CONFIG_CACHE.items()}
    except Exception as e:
        logger.error(f"Error reloading model configs from DB: {e}")
        return {}
