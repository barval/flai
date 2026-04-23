# app/db.py
# Database functions - PostgreSQL only
import json
import uuid
import os
from datetime import datetime
from typing import Any, Dict, List, Optional
from flask import current_app
from flask_babel import gettext as _
from app.database import get_db


def get_current_time_for_db():
    """Return the current time in DB format, taking timezone into account."""
    from .utils import get_current_time_in_timezone_for_db
    return get_current_time_in_timezone_for_db()


def get_user_storage_usage(user_id: str) -> int:
    """Get user's current upload storage usage in bytes. O(1) lookup."""
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT used_bytes FROM user_storage WHERE user_id = %s', (user_id,))
            row = c.fetchone()
            return row['used_bytes'] if row else 0
    except Exception:
        return 0


def update_user_storage(user_id: str, delta_bytes: int) -> None:
    """Update user's storage counter. delta_bytes can be positive (upload) or negative (delete)."""
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute('''
                INSERT INTO user_storage (user_id, used_bytes)
                VALUES (%s, GREATEST(0, %s))
                ON CONFLICT(user_id) DO UPDATE SET used_bytes = GREATEST(0, user_storage.used_bytes + %s)
            ''', (user_id, delta_bytes if delta_bytes > 0 else 0, delta_bytes))
    except Exception:
        pass


def get_user_sessions(user_id: str) -> List[Dict[str, Any]]:
    """Get all sessions for a user."""
    with get_db() as conn:
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
        LEFT JOIN session_visits sv ON cs.id = sv.session_id AND sv.user_id = %s
        WHERE cs.user_id = %s
        GROUP BY cs.id, cs.title, cs.model_name, cs.created_at, cs.updated_at
        ORDER BY cs.updated_at DESC
        ''', (user_id, user_id))

        sessions = []
        for row in c.fetchall():
            session_dict = dict(row)
            session_dict['has_unread'] = session_dict['unread_count'] > 0
            # Format timestamps as ISO
            for key in ('created_at', 'updated_at', 'last_visit'):
                if session_dict.get(key):
                    dt = session_dict[key]
                    if hasattr(dt, 'isoformat'):
                        if current_app.config.get('TIMEZONE') and dt.tzinfo is None:
                            dt = current_app.config['TIMEZONE'].localize(dt)
                        session_dict[key] = dt.isoformat()
            sessions.append(session_dict)
        return sessions


def get_session_messages(
    session_id: str,
    since: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """Get messages for a session with pagination."""
    with get_db() as conn:
        c = conn.cursor()
        if since:
            if 'T' in since:
                since = since.replace('T', ' ')[:19]
            c.execute('''
            SELECT id, role, content, file_type, file_name, file_path,
                   timestamp, model_name, response_time, mm_time, gen_time,
                   mm_model, gen_model
            FROM messages
            WHERE session_id = %s AND timestamp > %s
            ORDER BY timestamp ASC
            LIMIT %s OFFSET %s
            ''', (session_id, since, limit, offset))
        else:
            c.execute('''
            SELECT id, role, content, file_type, file_name, file_path,
                   timestamp, model_name, response_time, mm_time, gen_time,
                   mm_model, gen_model
            FROM messages
            WHERE session_id = %s
            ORDER BY timestamp ASC
            LIMIT %s OFFSET %s
            ''', (session_id, limit, offset))
        messages = []
        for row in c.fetchall():
            msg_dict = dict(row)
            if msg_dict.get('response_time'):
                try:
                    msg_dict['response_time'] = json.loads(msg_dict['response_time'])
                except Exception:
                    pass
            if msg_dict.get('timestamp'):
                dt = msg_dict['timestamp']
                if hasattr(dt, 'isoformat'):
                    if current_app.config.get('TIMEZONE') and dt.tzinfo is None:
                        dt = current_app.config['TIMEZONE'].localize(dt)
                    msg_dict['timestamp'] = dt.isoformat()
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
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
        INSERT INTO chat_sessions (id, user_id, title, model_name, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ''', (session_id, user_id, title, 'auto', current_time, current_time))
        c.execute('''
        INSERT INTO session_visits (user_id, session_id, last_visit)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, session_id) DO UPDATE SET last_visit = %s
        ''', (user_id, session_id, current_time, current_time))
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
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
        UPDATE chat_sessions
        SET title = %s, updated_at = %s
        WHERE id = %s
        ''', (title, current_time, session_id))
    return title


def save_message(session_id, role, content, file_data=None, file_type=None, file_name=None,
                 file_path=None, model_name=None, response_time=None, mm_time=None, gen_time=None,
                 mm_model=None, gen_model=None):
    """Save a message to the database."""
    with get_db() as conn:
        c = conn.cursor()
        current_time = get_current_time_for_db()
        if file_data and not file_path:
            from .utils import save_uploaded_file
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
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (session_id, role, content, file_data, file_type, file_name, file_path,
              model_name, current_time, response_time, mm_time, gen_time,
              mm_model, gen_model))
        message_id = c.lastrowid
        c.execute('''
        UPDATE chat_sessions
        SET updated_at = %s
        WHERE id = %s
        ''', (current_time, session_id))
    return message_id


def get_last_session(user_id):
    """Get user's last session."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT last_session_id FROM user_sessions WHERE user_id = %s', (user_id,))
        row = c.fetchone()
        return row['last_session_id'] if row else None


def set_last_session(user_id, session_id):
    """Set user's last session."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
        INSERT INTO user_sessions (user_id, last_session_id)
        VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE SET last_session_id = %s
        ''', (user_id, session_id, session_id))


def delete_session_and_messages(session_id, user_id, upload_folder=None):
    """Delete a session, its messages, and associated files from disk."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT user_id FROM chat_sessions WHERE id = %s', (session_id,))
        row = c.fetchone()
        if not row or row['user_id'] != user_id:
            return False
        c.execute('SELECT file_path FROM messages WHERE session_id = %s AND file_path IS NOT NULL', (session_id,))
        rows = c.fetchall()
        for row in rows:
            file_path = row['file_path']
            if file_path:
                if upload_folder and not os.path.isabs(file_path):
                    full_path = os.path.join(upload_folder, file_path)
                else:
                    full_path = file_path
                if os.path.exists(full_path):
                    try:
                        os.remove(full_path)
                    except Exception as e:
                        import logging
                        logging.getLogger(__name__).warning(f"Failed to delete file {full_path}: {e}")
        c.execute('DELETE FROM messages WHERE session_id = %s', (session_id,))
        c.execute('DELETE FROM chat_sessions WHERE id = %s', (session_id,))
        c.execute('SELECT COUNT(*) as cnt FROM chat_sessions WHERE user_id = %s', (user_id,))
        count = c.fetchone()['cnt']
        if count == 0:
            c.execute('DELETE FROM user_sessions WHERE user_id = %s', (user_id,))
        else:
            c.execute('SELECT last_session_id FROM user_sessions WHERE user_id = %s', (user_id,))
            row = c.fetchone()
            if row and row['last_session_id'] == session_id:
                c.execute('SELECT id FROM chat_sessions WHERE user_id = %s ORDER BY updated_at DESC LIMIT 1', (user_id,))
                new_last = c.fetchone()
                if new_last:
                    c.execute('UPDATE user_sessions SET last_session_id = %s WHERE user_id = %s', (new_last['id'], user_id))
                else:
                    c.execute('DELETE FROM user_sessions WHERE user_id = %s', (user_id,))
        c.execute('DELETE FROM session_visits WHERE session_id = %s', (session_id,))
    return True


def update_session_visit(user_id, session_id):
    """Update session last visit timestamp."""
    current_time = get_current_time_for_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
        INSERT INTO session_visits (user_id, session_id, last_visit)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, session_id) DO UPDATE SET last_visit = %s
        ''', (user_id, session_id, current_time, current_time))


def get_user_file_count(user_id):
    """Count all files associated with a user."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
        SELECT DISTINCT m.file_path
        FROM messages m
        JOIN chat_sessions cs ON m.session_id = cs.id
        WHERE cs.user_id = %s AND m.file_path IS NOT NULL AND m.file_path != ''
        ''', (user_id,))
        return len(c.fetchall())


def get_user_document_count(user_id):
    """Count all documents uploaded by a user."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT COUNT(*) as cnt FROM documents WHERE user_id = %s', (user_id,))
        return c.fetchone()['cnt']


# Index status constants
INDEX_STATUS_PENDING = 'pending'
INDEX_STATUS_INDEXING = 'indexing'
INDEX_STATUS_INDEXED = 'indexed'
INDEX_STATUS_FAILED = 'failed'


def get_user_documents(user_id):
    """Get all documents for a user, including index status, processing time and embedding model."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
        SELECT id, filename, file_size, file_ext, file_path, uploaded_at,
               index_status, indexed_at, indexing_started_at, embedding_model
        FROM documents
        WHERE user_id = %s
        ORDER BY uploaded_at DESC
        ''', (user_id,))
        documents = [dict(row) for row in c.fetchall()]
        tz = current_app.config.get('TIMEZONE')
        if tz:
            now = datetime.now(tz).replace(tzinfo=None)
        else:
            now = datetime.utcnow()
        for doc in documents:
            indexed_dt = None
            indexing_started_dt = None
            uploaded_dt = None
            if doc.get('uploaded_at'):
                dt = doc['uploaded_at']
                if hasattr(dt, 'replace'):
                    uploaded_dt = dt.replace(tzinfo=None) if dt.tzinfo else dt
                else:
                    try:
                        uploaded_dt = datetime.strptime(str(doc['uploaded_at'])[:19], '%Y-%m-%d %H:%M:%S')
                    except Exception:
                        pass
            if doc.get('indexed_at'):
                dt = doc['indexed_at']
                if hasattr(dt, 'replace'):
                    indexed_dt = dt.replace(tzinfo=None) if dt.tzinfo else dt
                else:
                    try:
                        indexed_dt = datetime.strptime(str(doc['indexed_at'])[:19], '%Y-%m-%d %H:%M:%S')
                    except Exception:
                        pass
            if doc.get('indexing_started_at'):
                dt = doc['indexing_started_at']
                if hasattr(dt, 'replace'):
                    indexing_started_dt = dt.replace(tzinfo=None) if dt.tzinfo else dt
                else:
                    try:
                        indexing_started_dt = datetime.strptime(str(doc['indexing_started_at'])[:19], '%Y-%m-%d %H:%M:%S')
                    except Exception:
                        pass
            processing_time = None
            status = doc.get('index_status')
            if status == INDEX_STATUS_INDEXED and indexed_dt and indexing_started_dt:
                delta = indexed_dt - indexing_started_dt
                processing_time = delta.total_seconds() / 60.0
            elif status == INDEX_STATUS_INDEXING and indexing_started_dt:
                delta = now - indexing_started_dt
                processing_time = delta.total_seconds() / 60.0
            # NOTE: For indexed documents without indexing_started_at, we don't fall back
            # to uploaded_dt because the difference (indexed_at - uploaded_at) can be days/weeks
            # and does not represent actual processing time.
            if processing_time is not None and processing_time < 0:
                processing_time = abs(processing_time)
            doc['processing_time'] = processing_time
            if uploaded_dt:
                if tz:
                    uploaded_dt = tz.localize(uploaded_dt)
                doc['uploaded_at'] = uploaded_dt.isoformat()
            if indexed_dt:
                if tz:
                    indexed_dt = tz.localize(indexed_dt)
                doc['indexed_at'] = indexed_dt.isoformat()
            if indexing_started_dt:
                if tz:
                    indexing_started_dt = tz.localize(indexing_started_dt)
                doc['indexing_started_at'] = indexing_started_dt.isoformat()
        return documents


def save_document(user_id, doc_id, filename, file_size, file_ext, file_path):
    """Save document metadata to database."""
    current_time = get_current_time_for_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
        INSERT INTO documents (id, user_id, filename, file_size, file_ext, file_path, uploaded_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ''', (doc_id, user_id, filename, file_size, file_ext, file_path, current_time))


def update_document_index_status(doc_id, index_status, indexed_at=None, indexing_started_at=None, embedding_model=None):
    """Update document index status."""
    current_time = get_current_time_for_db()
    with get_db() as conn:
        c = conn.cursor()
        if index_status == INDEX_STATUS_INDEXED and indexed_at is None:
            indexed_at = current_time
        if embedding_model:
            if indexing_started_at is not None:
                c.execute('''
                UPDATE documents
                SET index_status = %s, indexed_at = %s, indexing_started_at = %s, embedding_model = %s
                WHERE id = %s
                ''', (index_status, indexed_at, indexing_started_at, embedding_model, doc_id))
            else:
                c.execute('''
                UPDATE documents
                SET index_status = %s, indexed_at = %s, embedding_model = %s
                WHERE id = %s
                ''', (index_status, indexed_at, embedding_model, doc_id))
        else:
            if indexing_started_at is not None:
                c.execute('''
                UPDATE documents
                SET index_status = %s, indexed_at = %s, indexing_started_at = %s
                WHERE id = %s
                ''', (index_status, indexed_at, indexing_started_at, doc_id))
            else:
                c.execute('''
                UPDATE documents
                SET index_status = %s, indexed_at = %s
                WHERE id = %s
                ''', (index_status, indexed_at, doc_id))


def get_document(doc_id, user_id):
    """Get document metadata by ID and user."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
        SELECT id, filename, file_size, file_ext, file_path, index_status, indexed_at, uploaded_at, embedding_model
        FROM documents
        WHERE id = %s AND user_id = %s
        ''', (doc_id, user_id))
        row = c.fetchone()
        return dict(row) if row else None


def delete_document(doc_id, user_id):
    """Delete document metadata from database."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM documents WHERE id = %s AND user_id = %s', (doc_id, user_id))


def get_session_text_history(session_id, max_tokens=None, max_messages=None):
    """Get session messages for context building."""
    limit = max_messages or 200
    messages = get_session_messages(session_id, limit=limit)
    if max_tokens is not None:
        from .utils import estimate_tokens
        result = []
        total = 0
        for msg in reversed(messages):
            tokens = estimate_tokens(msg.get('content', ''))
            if total + tokens > max_tokens:
                break
            result.append(msg)
            total += tokens
        return list(reversed(result))
    return messages
