# app/model_config.py
import sqlite3
import time
import logging
from functools import lru_cache
from flask import current_app
from app.db import CHAT_DB_PATH
from app.database import is_postgresql, get_db_connection

logger = logging.getLogger(__name__)

# Cache with TTL: {module: {'data': dict, 'time': float}}
_MODEL_CONFIG_CACHE = {}
_CACHE_TTL = 60  # seconds — auto-refresh config after this time


def get_model_config(module):
    """
    Retrieve model configuration for a specific module from the database.
    Returns a dictionary with keys: model_name, context_length, temperature, top_p, timeout.
    Returns None if module not found or on error.
    Uses TTL-based caching: entries are automatically refreshed after _CACHE_TTL seconds.
    Works with both SQLite and PostgreSQL.
    """
    now = time.time()
    entry = _MODEL_CONFIG_CACHE.get(module)

    # Return cached entry if still valid
    if entry and (now - entry['time']) < _CACHE_TTL:
        return entry['data']

    # Load from database — works with both SQLite and PostgreSQL
    try:
        if is_postgresql():
            conn = get_db_connection()
            try:
                c = conn.cursor()
                c.execute('SELECT * FROM model_configs WHERE module = %s', (module,))
                row = c.fetchone()
                if row:
                    result = dict(row)
                    _MODEL_CONFIG_CACHE[module] = {'data': result, 'time': now}
                    return result
                return None
            finally:
                conn.close()
        else:
            with sqlite3.connect(CHAT_DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                c = conn.cursor()
                c.execute('SELECT * FROM model_configs WHERE module = ?', (module,))
                row = c.fetchone()

                if row:
                    result = dict(row)
                    _MODEL_CONFIG_CACHE[module] = {'data': result, 'time': now}
                    return result
                else:
                    if current_app:
                        current_app.logger.error(f"No configuration found for module '{module}' in DB")
                    return None
    except Exception as e:
        if current_app:
            current_app.logger.error(f"Error reading model config from DB: {e}")
        else:
            logger.error(f"Error reading model config from DB: {e}")
        return None


def invalidate_model_config_cache(module=None):
    """
    Invalidate the model config cache.
    If module is specified, only invalidate that module's cache.
    Otherwise, clear the entire cache.
    """
    global _MODEL_CONFIG_CACHE
    if module:
        _MODEL_CONFIG_CACHE.pop(module, None)
    else:
        _MODEL_CONFIG_CACHE.clear()


def reload_all_model_configs():
    """
    Reload all model configurations from database into cache.
    Returns dict of all configs.
    """
    global _MODEL_CONFIG_CACHE
    _MODEL_CONFIG_CACHE.clear()
    now = time.time()

    try:
        with sqlite3.connect(CHAT_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute('SELECT * FROM model_configs')
            rows = c.fetchall()

            for row in rows:
                _MODEL_CONFIG_CACHE[row['module']] = {'data': dict(row), 'time': now}

            return {k: v['data'] for k, v in _MODEL_CONFIG_CACHE.items()}
    except Exception as e:
        logger.error(f"Error reloading model configs from DB: {e}")
        return {}
