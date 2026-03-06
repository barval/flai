# app/db.py
# Database functions - handles sessions, messages, and translations

import sqlite3
import os
import json
import uuid
from datetime import datetime
from flask import current_app, g
from flask_babel import gettext as _

DATA_DIR = 'data'
CHAT_DB_PATH = os.path.join(DATA_DIR, 'chats.db')

def get_db():
    """Return a database connection (for use in routes)."""
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(CHAT_DB_PATH)
        db.row_factory = sqlite3.Row
    return db

def close_db(e=None):
    db = g.pop('_database', None)
    if db is not None:
        db.close()

def init_db():
    """Initialize the database (create tables)."""
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR, exist_ok=True)
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS user_sessions (
                user_id TEXT PRIMARY KEY,
                last_session_id TEXT
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                title TEXT,
                model_name TEXT DEFAULT "auto",
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT,
                content TEXT,
                file_data TEXT,
                file_type TEXT,
                file_name TEXT,
                model_name TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()

def migrate_db_add_response_fields():
    """Add fields to store response times."""
    try:
        with sqlite3.connect(CHAT_DB_PATH) as conn:
            c = conn.cursor()
            c.execute("PRAGMA table_info(messages)")
            columns = [col[1] for col in c.fetchall()]
            if 'response_time' not in columns:
                c.execute('ALTER TABLE messages ADD COLUMN response_time TEXT')
            if 'mm_time' not in columns:
                c.execute('ALTER TABLE messages ADD COLUMN mm_time TEXT')
            if 'gen_time' not in columns:
                c.execute('ALTER TABLE messages ADD COLUMN gen_time TEXT')
            if 'mm_model' not in columns:
                c.execute('ALTER TABLE messages ADD COLUMN mm_model TEXT')
            if 'gen_model' not in columns:
                c.execute('ALTER TABLE messages ADD COLUMN gen_model TEXT')
            conn.commit()
    except Exception as e:
        current_app.logger.error(f"Database migration error: {str(e)}")

def migrate_db_add_session_visits():
    """Add table for tracking last visits."""
    try:
        with sqlite3.connect(CHAT_DB_PATH) as conn:
            c = conn.cursor()
            c.execute('''
                CREATE TABLE IF NOT EXISTS session_visits (
                    user_id TEXT,
                    session_id TEXT,
                    last_visit DATETIME,
                    PRIMARY KEY (user_id, session_id)
                )
            ''')
            conn.commit()
    except Exception as e:
        current_app.logger.error(f"session_visits migration error: {str(e)}")

def get_user_sessions(user_id):
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''
            SELECT id, title, model_name, created_at, updated_at
            FROM chat_sessions
            WHERE user_id = ?
            ORDER BY updated_at DESC
        ''', (user_id,))
        sessions = [dict(row) for row in c.fetchall()]
        for s in sessions:
            c.execute('''
                SELECT last_visit FROM session_visits
                WHERE user_id = ? AND session_id = ?
            ''', (user_id, s['id']))
            row = c.fetchone()
            last_visit = row[0] if row else '1970-01-01 00:00:00'
            c.execute('''
                SELECT COUNT(*) FROM messages
                WHERE session_id = ? AND role = 'assistant' AND timestamp > ?
            ''', (s['id'], last_visit))
            count = c.fetchone()[0]
            s['has_unread'] = count > 0
        return sessions

def get_session_messages(session_id):
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''
            SELECT role, content, file_data, file_type, file_name,
                timestamp, model_name, response_time, mm_time, gen_time,
                mm_model, gen_model
            FROM messages
            WHERE session_id = ?
            ORDER BY timestamp ASC
        ''', (session_id,))
        messages = []
        for row in c.fetchall():
            msg_dict = dict(row)
            if msg_dict.get('response_time'):
                try:
                    msg_dict['response_time'] = json.loads(msg_dict['response_time'])
                except:
                    pass
            if msg_dict.get('timestamp'):
                try:
                    dt = datetime.strptime(msg_dict['timestamp'], '%Y-%m-%d %H:%M:%S')
                    if current_app.config.get('TIMEZONE'):
                        dt = current_app.config['TIMEZONE'].localize(dt)
                    msg_dict['timestamp'] = dt.isoformat()
                except:
                    pass
            messages.append(msg_dict)
        return messages

def create_session(user_id, title=None, lang='ru'):
    """
    Create new session with translated title.
    If title is None, use translated "New session".
    """
    session_id = str(uuid.uuid4())
    current_time = get_current_time_for_db()
    if title is None:
        # Use Flask-Babel gettext with forced locale
        from flask import current_app
        from flask_babel import force_locale
        with current_app.app_context():
            with force_locale(lang):
                title = _("New session")
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            INSERT INTO chat_sessions (id, user_id, title, model_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (session_id, user_id, title, 'auto', current_time, current_time))
        c.execute('''
            INSERT OR REPLACE INTO session_visits (user_id, session_id, last_visit)
            VALUES (?, ?, ?)
        ''', (user_id, session_id, current_time))
        conn.commit()
    return session_id

def update_session_title(session_id, first_message, file_name=None):
    if first_message and first_message.strip():
        title = first_message[:40] + ('...' if len(first_message) > 40 else '')
    elif file_name:
        title = file_name[:40] + ('...' if len(file_name) > 40 else '')
    else:
        title = "New session"
    current_time = get_current_time_for_db()
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            UPDATE chat_sessions
            SET title = ?, updated_at = ?
            WHERE id = ?
        ''', (title, current_time, session_id))
        conn.commit()
    return title

def save_message(session_id, role, content, file_data=None, file_type=None, file_name=None,
    model_name=None, response_time=None, mm_time=None, gen_time=None,
    mm_model=None, gen_model=None):
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        current_time = get_current_time_for_db()
        if response_time and isinstance(response_time, dict):
            response_time = json.dumps(response_time, ensure_ascii=False)
        elif response_time is not None and not isinstance(response_time, str):
            response_time = str(response_time)
        c.execute('''
            INSERT INTO messages (
                session_id, role, content, file_data, file_type, file_name,
                model_name, timestamp, response_time, mm_time, gen_time,
                mm_model, gen_model
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (session_id, role, content, file_data, file_type, file_name,
            model_name, current_time, response_time, mm_time, gen_time,
            mm_model, gen_model))
        c.execute('''
            UPDATE chat_sessions
            SET updated_at = ?
            WHERE id = ?
        ''', (current_time, session_id))
        conn.commit()

def get_last_session(user_id):
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT last_session_id FROM user_sessions WHERE user_id = ?', (user_id,))
        row = c.fetchone()
        return row[0] if row else None

def set_last_session(user_id, session_id):
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO user_sessions (user_id, last_session_id)
            VALUES (?, ?)
        ''', (user_id, session_id))
        conn.commit()

def delete_session_and_messages(session_id, user_id):
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT user_id FROM chat_sessions WHERE id = ?', (session_id,))
        row = c.fetchone()
        if not row or row[0] != user_id:
            return False
        c.execute('DELETE FROM messages WHERE session_id = ?', (session_id,))
        c.execute('DELETE FROM chat_sessions WHERE id = ?', (session_id,))
        c.execute('SELECT COUNT(*) FROM chat_sessions WHERE user_id = ?', (user_id,))
        count = c.fetchone()[0]
        if count == 0:
            c.execute('DELETE FROM user_sessions WHERE user_id = ?', (user_id,))
        else:
            c.execute('SELECT last_session_id FROM user_sessions WHERE user_id = ?', (user_id,))
            row = c.fetchone()
            if row and row[0] == session_id:
                c.execute('SELECT id FROM chat_sessions WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1', (user_id,))
                new_last = c.fetchone()
                if new_last:
                    c.execute('UPDATE user_sessions SET last_session_id = ? WHERE user_id = ?', (new_last[0], user_id))
                else:
                    c.execute('DELETE FROM user_sessions WHERE user_id = ?', (user_id,))
        c.execute('DELETE FROM session_visits WHERE session_id = ?', (session_id,))
        conn.commit()
        return True

def update_session_visit(user_id, session_id):
    current_time = get_current_time_for_db()
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO session_visits (user_id, session_id, last_visit)
            VALUES (?, ?, ?)
        ''', (user_id, session_id, current_time))
        conn.commit()

def get_current_time_for_db():
    """Return the current time in DB format, taking timezone into account."""
    from .utils import get_current_time_in_timezone_for_db
    return get_current_time_in_timezone_for_db()