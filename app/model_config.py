# app/model_config.py
import sqlite3
from flask import current_app
from app.db import CHAT_DB_PATH

def get_model_config(module):
    """
    Retrieve model configuration for a specific module directly from the database.
    Returns a dictionary with keys: model_name, context_length, temperature, top_p, timeout.
    Returns None if module not found or on error.
    """
    try:
        conn = sqlite3.connect(CHAT_DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT * FROM model_configs WHERE module = ?', (module,))
        row = c.fetchone()
        conn.close()
        if row:
            return dict(row)
        else:
            current_app.logger.error(f"No configuration found for module '{module}' in DB")
            return None
    except Exception as e:
        current_app.logger.error(f"Error reading model config from DB: {e}")
        return None