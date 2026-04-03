# app/db.py
# Database functions - handles sessions, messages, and translations
import sqlite3
import os
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from flask import current_app, g
from flask_babel import gettext as _

DATA_DIR = 'data'
CHAT_DB_PATH = os.path.join(DATA_DIR, 'chats.db')

# Index status constants
INDEX_STATUS_PENDING = 'pending'
INDEX_STATUS_INDEXING = 'indexing'
INDEX_STATUS_INDEXED = 'indexed'
INDEX_STATUS_FAILED = 'failed'


def get_db() -> sqlite3.Connection:
    """Return a database connection (for use in routes)."""
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(CHAT_DB_PATH)
        db.row_factory = sqlite3.Row
    return db


def close_db(e: Any = None) -> None:
    """Close database connection."""
    db = g.pop('_database', None)
    if db is not None:
        db.close()


def init_db():
    """Initialize the database (create tables) and enable WAL mode."""
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
        # Documents table for uploaded documents
        c.execute('''
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            filename TEXT,
            file_size INTEGER,
            file_ext TEXT,
            file_path TEXT,
            index_status TEXT,
            indexed_at DATETIME,
            uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        # Create indexes for better performance
        c.execute('CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_messages_session_timestamp ON messages(session_id, timestamp)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id)')
        # Additional indexes for user sessions and documents
        c.execute('CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_id ON chat_sessions(user_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_session_visits_session_id ON session_visits(session_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_documents_index_status ON documents(index_status)')
        # Enable Write-Ahead Logging for better concurrency
        c.execute("PRAGMA journal_mode=WAL")
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


def migrate_db_add_index_status(app):
    """Add index_status and indexed_at columns to documents table."""
    try:
        with sqlite3.connect(CHAT_DB_PATH) as conn:
            c = conn.cursor()
            c.execute("PRAGMA table_info(documents)")
            columns = [col[1] for col in c.fetchall()]
            if 'index_status' not in columns:
                c.execute("ALTER TABLE documents ADD COLUMN index_status TEXT")
                app.logger.info("Added column index_status to documents table")
            if 'indexed_at' not in columns:
                c.execute("ALTER TABLE documents ADD COLUMN indexed_at DATETIME")
                app.logger.info("Added column indexed_at to documents table")
            # New column for tracking start of indexing
            if 'indexing_started_at' not in columns:
                c.execute("ALTER TABLE documents ADD COLUMN indexing_started_at DATETIME")
                app.logger.info("Added column indexing_started_at to documents table")
            conn.commit()
    except Exception as e:
        app.logger.error(f"Index status migration error: {str(e)}")


def migrate_add_embedding_model(app):
    """Add embedding_model column to documents table."""
    try:
        with sqlite3.connect(CHAT_DB_PATH) as conn:
            c = conn.cursor()
            c.execute("PRAGMA table_info(documents)")
            columns = [col[1] for col in c.fetchall()]
            if 'embedding_model' not in columns:
                c.execute("ALTER TABLE documents ADD COLUMN embedding_model TEXT")
                app.logger.info("Added column embedding_model to documents table")
            conn.commit()
    except Exception as e:
        app.logger.error(f"Embedding model migration error: {str(e)}")


def migrate_add_model_configs(app):
    """Create model_configs table and populate with defaults from .env."""
    try:
        with sqlite3.connect(CHAT_DB_PATH) as conn:
            c = conn.cursor()
            c.execute('''
            CREATE TABLE IF NOT EXISTS model_configs (
                module TEXT PRIMARY KEY,
                model_name TEXT,
                context_length INTEGER,
                temperature REAL,
                top_p REAL,
                timeout INTEGER,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            # Insert default rows if not present (values from current app.config)
            default_modules = [
                ('chat', app.config.get('LLM_CHAT_MODEL', ''),
                 app.config.get('LLM_CHAT_MODEL_CONTEXT_WINDOW', 4096),
                 app.config.get('LLM_CHAT_TEMPERATURE', 0.1),
                 app.config.get('LLM_CHAT_TOP_P', 0.1),
                 app.config.get('LLM_CHAT_TIMEOUT', 300)),
                ('reasoning', app.config.get('LLM_REASONING_MODEL', ''),
                 app.config.get('LLM_REASONING_MODEL_CONTEXT_WINDOW', 4096),
                 app.config.get('LLM_REASONING_TEMPERATURE', 0.7),
                 app.config.get('LLM_REASONING_TOP_P', 0.9),
                 app.config.get('LLM_REASONING_TIMEOUT', 300)),
                ('multimodal', app.config.get('LLM_MULTIMODAL_MODEL', ''),
                 app.config.get('LLM_MULTIMODAL_MODEL_CONTEXT_WINDOW', 4096),
                 app.config.get('LLM_MULTIMODAL_TEMPERATURE', 0.7),
                 app.config.get('LLM_MULTIMODAL_TOP_P', 0.9),
                 app.config.get('LLM_MULTIMODAL_TIMEOUT', 300)),
                ('embedding', app.config.get('EMBEDDING_MODEL', 'bge-m3:latest'),
                 0, 0.0, 0.0, 0)
            ]
            for mod in default_modules:
                c.execute('''
                INSERT OR IGNORE INTO model_configs
                (module, model_name, context_length, temperature, top_p, timeout)
                VALUES (?, ?, ?, ?, ?, ?)
                ''', mod)
            conn.commit()
            app.logger.info("model_configs table created/verified.")
    except Exception as e:
        app.logger.error(f"Model config migration error: {str(e)}")


def migrate_add_ollama_url(app):
    """Add ollama_url column to model_configs table."""
    try:
        with sqlite3.connect(CHAT_DB_PATH) as conn:
            c = conn.cursor()
            c.execute("PRAGMA table_info(model_configs)")
            columns = [col[1] for col in c.fetchall()]
            if 'ollama_url' not in columns:
                c.execute("ALTER TABLE model_configs ADD COLUMN ollama_url TEXT")
                app.logger.info("Added column ollama_url to model_configs table")
            # Set default value for existing rows (assume default Ollama)
            c.execute("UPDATE model_configs SET ollama_url = 'http://ollama:11434' WHERE ollama_url IS NULL")
            conn.commit()
    except Exception as e:
        app.logger.error(f"Migration add ollama_url error: {str(e)}")


def get_user_sessions(user_id: str) -> List[Dict[str, Any]]:
    """Get all sessions for a user.
    Optimized to avoid N+1 queries by using JOINs and subqueries.
    """
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''
        SELECT 
            cs.id, 
            cs.title, 
            cs.model_name, 
            cs.created_at, 
            cs.updated_at,
            COALESCE(MAX(sv.last_visit), '1970-01-01 00:00:00') as last_visit,
            (SELECT COUNT(*) FROM messages 
             WHERE session_id = cs.id AND role = 'assistant' 
             AND timestamp > COALESCE(MAX(sv.last_visit), '1970-01-01 00:00:00')) as unread_count,
            (SELECT COUNT(*) FROM messages WHERE session_id = cs.id) as message_count
        FROM chat_sessions cs
        LEFT JOIN session_visits sv ON cs.id = sv.session_id AND sv.user_id = ?
        WHERE cs.user_id = ?
        GROUP BY cs.id, cs.title, cs.model_name, cs.created_at, cs.updated_at
        ORDER BY cs.updated_at DESC
        ''', (user_id, user_id))
        
        sessions = []
        for row in c.fetchall():
            session_dict = dict(row)
            session_dict['has_unread'] = session_dict['unread_count'] > 0
            sessions.append(session_dict)
        return sessions


def get_session_messages(
    session_id: str,
    since: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """Get messages for a session with pagination.

    Args:
        session_id: Session identifier
        since: Get messages after this timestamp (ISO format)
        limit: Maximum number of messages to return (default 100)
        offset: Number of messages to skip (default 0)

    Returns:
        List of message dictionaries
    """
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        if since:
            if 'T' in since:
                since = since.replace('T', ' ')[:19]
            c.execute('''
            SELECT id, role, content, file_data, file_type, file_name, file_path,
                   timestamp, model_name, response_time, mm_time, gen_time,
                   mm_model, gen_model
            FROM messages
            WHERE session_id = ? AND timestamp > ?
            ORDER BY timestamp ASC
            LIMIT ? OFFSET ?
            ''', (session_id, since, limit, offset))
        else:
            c.execute('''
            SELECT id, role, content, file_data, file_type, file_name, file_path,
                   timestamp, model_name, response_time, mm_time, gen_time,
                   mm_model, gen_model
            FROM messages
            WHERE session_id = ?
            ORDER BY timestamp ASC
            LIMIT ? OFFSET ?
            ''', (session_id, limit, offset))
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
    """Create new session with translated title."""
    session_id = str(uuid.uuid4())
    current_time = get_current_time_for_db()
    if title is None:
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
    """Save a message to the database."""
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        current_time = get_current_time_for_db()
        if file_data and not file_path:
            from .utils import save_uploaded_file
            from flask import current_app
            file_path = save_uploaded_file(
                file_data=file_data,
                filename=file_name,
                session_id=session_id,
                upload_folder=current_app.config['UPLOAD_FOLDER']
            )
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
    """Delete a session, its messages, and associated files from disk."""
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT user_id FROM chat_sessions WHERE id = ?', (session_id,))
        row = c.fetchone()
        if not row or row[0] != user_id:
            return False
        c.execute('SELECT file_path FROM messages WHERE session_id = ? AND file_path IS NOT NULL', (session_id,))
        rows = c.fetchall()
        for row in rows:
            file_path = row[0]
            if file_path:
                if upload_folder and not os.path.isabs(file_path):
                    full_path = os.path.join(upload_folder, file_path)
                else:
                    full_path = file_path
                if os.path.exists(full_path):
                    try:
                        os.remove(full_path)
                    except Exception as e:
                        # Log error but continue with deletion of other files
                        import logging
                        logging.getLogger(__name__).warning(f"Failed to delete file {full_path}: {e}")
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
    """Count all files associated with a user."""
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
        SELECT DISTINCT m.file_path
        FROM messages m
        JOIN chat_sessions cs ON m.session_id = cs.id
        WHERE cs.user_id = ? AND m.file_path IS NOT NULL AND m.file_path != ''
        ''', (user_id,))
        rows = c.fetchall()
    return len(rows)


def get_user_document_count(user_id):
    """Count all documents uploaded by a user."""
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM documents WHERE user_id = ?', (user_id,))
        return c.fetchone()[0]


def get_user_documents(user_id):
    """Get all documents for a user, including index status, processing time and embedding model."""
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''
        SELECT id, filename, file_size, file_ext, file_path, uploaded_at,
               index_status, indexed_at, indexing_started_at, embedding_model
        FROM documents
        WHERE user_id = ?
        ORDER BY uploaded_at DESC
        ''', (user_id,))
        documents = [dict(row) for row in c.fetchall()]
        # Use configured timezone for "now" calculation, or UTC as fallback
        tz = current_app.config.get('TIMEZONE')
        if tz:
            now = datetime.now(tz).replace(tzinfo=None)
        else:
            now = datetime.utcnow()
        for doc in documents:
            uploaded_dt = None
            indexed_dt = None
            indexing_started_dt = None
            if doc.get('uploaded_at'):
                try:
                    uploaded_dt = datetime.strptime(doc['uploaded_at'], '%Y-%m-%d %H:%M:%S')
                except:
                    pass
            if doc.get('indexed_at'):
                try:
                    indexed_dt = datetime.strptime(doc['indexed_at'], '%Y-%m-%d %H:%M:%S')
                except:
                    pass
            if doc.get('indexing_started_at'):
                try:
                    indexing_started_dt = datetime.strptime(doc['indexing_started_at'], '%Y-%m-%d %H:%M:%S')
                except:
                    pass
            processing_time = None
            status = doc.get('index_status')
            if status == INDEX_STATUS_INDEXED and indexed_dt and indexing_started_dt:
                delta = indexed_dt - indexing_started_dt
                processing_time = delta.total_seconds() / 60.0
            elif status == INDEX_STATUS_INDEXING and indexing_started_dt:
                delta = now - indexing_started_dt
                processing_time = delta.total_seconds() / 60.0
            elif status == INDEX_STATUS_INDEXED and indexed_dt and uploaded_dt:
                delta = indexed_dt - uploaded_dt
                processing_time = delta.total_seconds() / 60.0
            elif status == INDEX_STATUS_INDEXING and uploaded_dt:
                delta = now - uploaded_dt
                processing_time = delta.total_seconds() / 60.0
            # Ensure positive values (fallback for edge cases)
            if processing_time is not None and processing_time < 0:
                processing_time = abs(processing_time)
            doc['processing_time'] = processing_time
            if uploaded_dt:
                if current_app.config.get('TIMEZONE'):
                    uploaded_dt = current_app.config['TIMEZONE'].localize(uploaded_dt)
                doc['uploaded_at'] = uploaded_dt.isoformat()
            if indexed_dt:
                if current_app.config.get('TIMEZONE'):
                    indexed_dt = current_app.config['TIMEZONE'].localize(indexed_dt)
                doc['indexed_at'] = indexed_dt.isoformat()
            if indexing_started_dt:
                if current_app.config.get('TIMEZONE'):
                    indexing_started_dt = current_app.config['TIMEZONE'].localize(indexing_started_dt)
                doc['indexing_started_at'] = indexing_started_dt.isoformat()
        return documents


def save_document(user_id, doc_id, filename, file_size, file_ext, file_path):
    """Save document metadata to database. Index status is NULL initially."""
    current_time = get_current_time_for_db()
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
        INSERT INTO documents (id, user_id, filename, file_size, file_ext, file_path, uploaded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (doc_id, user_id, filename, file_size, file_ext, file_path, current_time))
        conn.commit()
    return doc_id


def get_document(doc_id, user_id):
    """Get document metadata."""
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''
        SELECT id, filename, file_size, file_ext, file_path, uploaded_at,
               index_status, indexed_at, indexing_started_at, embedding_model
        FROM documents
        WHERE id = ? AND user_id = ?
        ''', (doc_id, user_id))
        row = c.fetchone()
    return dict(row) if row else None


def update_document_index_status(doc_id, status, indexed_at=None, indexing_started_at=None, embedding_model=None):
    """Update the index status, optionally indexed_at, indexing_started_at and embedding_model for a document."""
    # Whitelist of allowed column names to prevent SQL injection
    ALLOWED_COLUMNS = {
        'index_status': 'index_status',
        'indexed_at': 'indexed_at',
        'indexing_started_at': 'indexing_started_at',
        'embedding_model': 'embedding_model'
    }

    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        updates = []
        params = []

        # Dictionary of values to update
        values_to_update = {
            'index_status': status,
            'indexed_at': indexed_at,
            'indexing_started_at': indexing_started_at,
            'embedding_model': embedding_model
        }

        for field, value in values_to_update.items():
            if value is not None:
                # Security: verify column name is in whitelist (defensive programming)
                if field not in ALLOWED_COLUMNS:
                    raise ValueError(f"Invalid field name: {field}")
                column_name = ALLOWED_COLUMNS[field]
                updates.append(f"{column_name} = ?")
                params.append(value)

        if not updates:
            return

        params.append(doc_id)
        c.execute(f'UPDATE documents SET {", ".join(updates)} WHERE id = ?', params)
        conn.commit()


def delete_document(doc_id, user_id):
    """Delete document metadata from database."""
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('DELETE FROM documents WHERE id = ? AND user_id = ?', (doc_id, user_id))
        conn.commit()


def get_documents_total_size(user_id=None):
    """Get total size of all documents (optionally filtered by user)."""
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        if user_id:
            c.execute('SELECT SUM(file_size) FROM documents WHERE user_id = ?', (user_id,))
        else:
            c.execute('SELECT SUM(file_size) FROM documents')
        result = c.fetchone()[0]
        return result if result else 0


def _extract_text_from_user_content(content):
    """Extract only the text parts from a user message."""
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
        return content


def get_session_text_history(session_id, max_tokens, max_messages=None):
    """
    Retrieve text-only messages from a session with SQL-level limits.
    
    Args:
        session_id: Session identifier
        max_tokens: Maximum tokens to retrieve
        max_messages: Maximum number of messages (SQL LIMIT)
    
    Returns:
        List of message dictionaries
    """
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        # Use SQL LIMIT to prevent loading excessive data
        limit = max_messages * 2 if max_messages else 100
        c.execute('''
        SELECT role, content, timestamp
        FROM messages
        WHERE session_id = ? AND role IN ('user', 'assistant')
        ORDER BY timestamp DESC
        LIMIT ?
        ''', (session_id, limit))
        
        rows = c.fetchall()
        all_messages = []
        for r in rows:
            role = r['role']
            content = r['content']
            if role == 'user':
                content = _extract_text_from_user_content(content)
            all_messages.append({'role': role, 'content': content, 'timestamp': r['timestamp']})
        
        # Reverse to get chronological order
        all_messages.reverse()
        
        from flask import current_app
        token_chars = current_app.config.get('TOKEN_CHARS', 3) if current_app else 3
        
        def estimate_tokens(text):
            return len(text) // token_chars + 1
        
        selected = []
        total_tokens = 0
        
        for msg in reversed(all_messages):
            tokens = estimate_tokens(msg['content'])
            if total_tokens + tokens > max_tokens:
                break
            if max_messages is not None and len(selected) >= max_messages:
                break
            selected.insert(0, msg)
            total_tokens += tokens
        
        return selected