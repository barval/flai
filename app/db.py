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
    """Close database connection."""
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
                file_path TEXT,
                model_name TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Create indexes for better performance
        c.execute('CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_messages_session_timestamp ON messages(session_id, timestamp)')
        conn.commit()

def migrate_db_add_response_fields(app):
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
            if 'file_path' not in columns:
                c.execute('ALTER TABLE messages ADD COLUMN file_path TEXT')
            conn.commit()
    except Exception as e:
        app.logger.error(f"Database migration error (response fields): {str(e)}")

def migrate_db_add_session_visits(app):
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
        app.logger.error(f"session_visits migration error: {str(e)}")

def migrate_db_add_indexes(app):
    """Add indexes to messages table for faster session switching."""
    try:
        with sqlite3.connect(CHAT_DB_PATH) as conn:
            c = conn.cursor()
            c.execute('CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_messages_session_timestamp ON messages(session_id, timestamp)')
            conn.commit()
            app.logger.info("Indexes on messages table created/verified.")
    except Exception as e:
        app.logger.error(f"Index migration error: {str(e)}")

def get_user_sessions(user_id):
    """Get all sessions for a user."""
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
            # Get total message count for the session
            c.execute('SELECT COUNT(*) FROM messages WHERE session_id = ?', (s['id'],))
            s['message_count'] = c.fetchone()[0]
        return sessions

def get_session_messages(session_id, since=None):
    """Get messages for a session."""
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        if since:
            # Convert ISO timestamp to DB format if necessary
            if 'T' in since:
                since = since.replace('T', ' ')[:19]
            c.execute('''
                SELECT id, role, content, file_data, file_type, file_name, file_path,
                timestamp, model_name, response_time, mm_time, gen_time,
                mm_model, gen_model
                FROM messages
                WHERE session_id = ? AND timestamp > ?
                ORDER BY timestamp ASC
            ''', (session_id, since))
        else:
            c.execute('''
                SELECT id, role, content, file_data, file_type, file_name, file_path,
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
    """Update session title based on first message."""
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
                 file_path=None, model_name=None, response_time=None, mm_time=None, gen_time=None,
                 mm_model=None, gen_model=None):
    """
    Save a message to the database.
    If file_path is provided, it is stored directly.
    If file_data is provided and file_path is None, the file is saved to disk
    using app.config['UPLOAD_FOLDER'] and the path is stored.
    """
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        current_time = get_current_time_for_db()
        # If file_data is given but no file_path, save the file to disk
        if file_data and not file_path:
            from .utils import save_uploaded_file
            from flask import current_app
            file_path = save_uploaded_file(
                file_data=file_data,
                filename=file_name,
                session_id=session_id,
                upload_folder=current_app.config['UPLOAD_FOLDER']
            )
            # After saving, we can set file_data to None to avoid storing base64
            # (but we keep it for backward compatibility with old messages)
            # In the future we can migrate old data.
        if response_time and isinstance(response_time, dict):
            response_time = json.dumps(response_time, ensure_ascii=False)
        elif response_time is not None and not isinstance(response_time, str):
            response_time = str(response_time)
        c.execute('''
            INSERT INTO messages (
                session_id, role, content, file_data, file_type, file_name, file_path,
                model_name, timestamp, response_time, mm_time, gen_time,
                mm_model, gen_model
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (session_id, role, content, file_data, file_type, file_name, file_path,
              model_name, current_time, response_time, mm_time, gen_time,
              mm_model, gen_model))
        message_id = c.lastrowid
        c.execute('''
            UPDATE chat_sessions
            SET updated_at = ?
            WHERE id = ?
        ''', (current_time, session_id))
        conn.commit()
        return message_id

def get_last_session(user_id):
    """Get user's last session."""
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT last_session_id FROM user_sessions WHERE user_id = ?', (user_id,))
        row = c.fetchone()
        return row[0] if row else None

def set_last_session(user_id, session_id):
    """Set user's last session."""
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO user_sessions (user_id, last_session_id)
            VALUES (?, ?)
        ''', (user_id, session_id))
        conn.commit()

def delete_session_and_messages(session_id, user_id, upload_folder=None):
    """
    Delete a session, its messages, and associated files from disk.
    If upload_folder is provided, it is used to locate the files (for relative file_path).
    """
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT user_id FROM chat_sessions WHERE id = ?', (session_id,))
        row = c.fetchone()
        if not row or row[0] != user_id:
            return False
        # Delete associated files from disk
        c.execute('SELECT file_path FROM messages WHERE session_id = ? AND file_path IS NOT NULL', (session_id,))
        rows = c.fetchall()
        for row in rows:
            file_path = row[0]
            if file_path:
                # If we have upload_folder and the stored path is relative, build absolute path
                if upload_folder and not os.path.isabs(file_path):
                    full_path = os.path.join(upload_folder, file_path)
                else:
                    full_path = file_path
                if os.path.exists(full_path):
                    try:
                        os.remove(full_path)
                    except Exception as e:
                        # Log error but continue
                        pass
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
    """Update session last visit timestamp."""
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

def get_user_file_count(user_id):
    """
    Count all files associated with a user.
    Files are stored in data/uploads/{session_id}/{filename}
    Sessions belong to users, so we count files from all user's sessions.
    """
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        # Get all session IDs for this user that have file_path in messages
        c.execute('''
            SELECT DISTINCT m.file_path
            FROM messages m
            JOIN chat_sessions cs ON m.session_id = cs.id
            WHERE cs.user_id = ? AND m.file_path IS NOT NULL AND m.file_path != ''
        ''', (user_id,))
        rows = c.fetchall()
        return len(rows)

# ----- Helper function to extract text from user message JSON -----
def _extract_text_from_user_content(content):
    """
    Extract only the text parts from a user message that may contain JSON
    with file data. If content is not JSON, return it as is.
    """
    if not content or not content.startswith('['):
        return content
    try:
        parts = json.loads(content)
        texts = []
        for part in parts:
            if isinstance(part, dict) and part.get('type') == 'text':
                text = part.get('text', '')
                if text:
                    texts.append(text)
        return '\n'.join(texts).strip()
    except (json.JSONDecodeError, TypeError, AttributeError):
        # In case of parsing error, return original content
        return content

# ----- Function for retrieving text-only conversation history -----
def get_session_text_history(session_id, max_tokens, max_messages=None):
    """
    Retrieve text-only messages from a session, ordered by time ascending,
    limited by max_tokens and optionally max_messages.
    Only messages with role 'user' or 'assistant' are included.
    File data (images, audio) are ignored; only the text content is used.
    The function returns a list of dicts with keys: 'role', 'content', 'timestamp'.
    """
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''
            SELECT role, content, timestamp
            FROM messages
            WHERE session_id = ? AND role IN ('user', 'assistant')
            ORDER BY timestamp ASC
        ''', (session_id,))
        rows = c.fetchall()
        # Convert to list of dicts, cleaning user content
        all_messages = []
        for r in rows:
            role = r['role']
            content = r['content']
            if role == 'user':
                content = _extract_text_from_user_content(content)
            all_messages.append({'role': role, 'content': content, 'timestamp': r['timestamp']})
        # Rough token estimation using configurable characters per token
        from flask import current_app
        token_chars = current_app.config.get('TOKEN_CHARS', 3) if current_app else 3
        def estimate_tokens(text):
            return len(text) // token_chars + 1
        # Work from the end backwards, accumulating until we hit the limit
        selected = []
        total_tokens = 0
        for msg in reversed(all_messages):
            tokens = estimate_tokens(msg['content'])
            if total_tokens + tokens > max_tokens:
                break
            if max_messages is not None and len(selected) >= max_messages:
                break
            selected.insert(0, msg)  # prepend to keep chronological order
            total_tokens += tokens
        return selected