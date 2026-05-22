# app/model_config.py
import logging

from app.database import get_db

logger = logging.getLogger(__name__)

# Cache: {module: {'data': dict, '_updated_at': datetime|None}}
# On each call, checks DB updated_at — if changed, re-reads from DB.
# This ensures all gunicorn workers see consistent config after a save.
_MODEL_CONFIG_CACHE: dict = {}


def get_model_config(module):
    """Retrieve model configuration for a module from the database.

    Uses a versioned cache: stores the last seen updated_at from DB.
    On each call, queries only updated_at (lightweight) and re-reads
    full row only when it changes. This keeps all workers in sync after
    a config save in any worker process.
    """
    entry = _MODEL_CONFIG_CACHE.get(module)

    try:
        with get_db() as conn:
            c = conn.cursor()

            # Quick freshness check — single column, primary key lookup
            if entry:
                c.execute("SELECT updated_at FROM model_configs WHERE module = %s", (module,))
                row = c.fetchone()
                if row:
                    cached_updated = entry.get("_updated_at")
                    if cached_updated is not None and row["updated_at"] == cached_updated:
                        return entry["data"]
                else:
                    # Row deleted from DB — remove from cache
                    _MODEL_CONFIG_CACHE.pop(module, None)
                    return None

            # Full read from DB
            c.execute("SELECT * FROM model_configs WHERE module = %s", (module,))
            row = c.fetchone()
            if row:
                result = dict(row)
                _MODEL_CONFIG_CACHE[module] = {
                    "data": result,
                    "_updated_at": result.get("updated_at"),
                }
                return result
            return None
    except Exception as e:
        logger.error(f"Error reading model config from DB: {e}")
        if entry:
            return entry["data"]
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

    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM model_configs")
            for row in c.fetchall():
                _MODEL_CONFIG_CACHE[row["module"]] = {
                    "data": dict(row),
                    "_updated_at": row.get("updated_at"),
                }
            return {k: v["data"] for k, v in _MODEL_CONFIG_CACHE.items()}
    except Exception as e:
        logger.error(f"Error reloading model configs from DB: {e}")
        return {}
