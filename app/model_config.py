# app/model_config.py
import sqlite3
import logging
from functools import lru_cache
from flask import current_app
from app.db import CHAT_DB_PATH

logger = logging.getLogger(__name__)
_MODEL_CONFIG_CACHE = {}


def get_model_config(module):
    """
    Retrieve model configuration for a specific module directly from the database.
    Returns a dictionary with keys: model_name, context_length, temperature, top_p, timeout.
    Returns None if module not found or on error.
    Uses in-memory caching to avoid repeated database queries.
    """
    # Check cache first
    if module in _MODEL_CONFIG_CACHE:
        return _MODEL_CONFIG_CACHE[module]
    
    try:
        with sqlite3.connect(CHAT_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute('SELECT * FROM model_configs WHERE module = ?', (module,))
            row = c.fetchone()
            
            if row:
                result = dict(row)
                _MODEL_CONFIG_CACHE[module] = result
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
    
    try:
        with sqlite3.connect(CHAT_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute('SELECT * FROM model_configs')
            rows = c.fetchall()
            
            for row in rows:
                _MODEL_CONFIG_CACHE[row['module']] = dict(row)
            
            return _MODEL_CONFIG_CACHE
    except Exception as e:
        logger.error(f"Error reloading model configs from DB: {e}")
        return {}