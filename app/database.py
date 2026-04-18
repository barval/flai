"""PostgreSQL database abstraction layer.

This module provides the database connection and initialization
for the FLAI application using PostgreSQL.

DATABASE_URL must be set in .env:
    DATABASE_URL=postgresql://user:pass@host:5432/flai
"""
import os
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    raise ValueError("DATABASE_URL must be set in .env (postgresql://user:pass@host:5432/flai)")
if not DATABASE_URL.startswith('postgresql://') and not DATABASE_URL.startswith('postgres://'):
    raise ValueError(f"Only PostgreSQL is supported. Got: {DATABASE_URL}")

logger.info(f"Using PostgreSQL database: {DATABASE_URL}")


def get_db_connection():
    """Get a PostgreSQL connection with RealDictCursor (dict-like results)."""
    import psycopg2
    from psycopg2.extras import RealDictCursor
    url = DATABASE_URL.replace('postgres://', 'postgresql://')
    conn = psycopg2.connect(url)
    conn.set_session(autocommit=False)
    conn.cursor_factory = RealDictCursor
    return conn


@contextmanager
def get_db():
    """Context manager for database connections.

    Usage:
        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT 1')
            row = c.fetchone()  # dict-like
    """
    conn = None
    try:
        conn = get_db_connection()
        yield conn
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()


def close_db(e=None):
    """Close database connection (for Flask teardown)."""
    from flask import g
    db = getattr(g, '_database', None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass
        g.pop('_database', None)


def init_db():
    """Initialize the PostgreSQL database (create tables)."""
    _init_postgresql()


def _init_postgresql():
    """Initialize PostgreSQL schema."""
    conn = get_db_connection()
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
            model_name TEXT DEFAULT 'auto',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            session_id TEXT,
            role TEXT,
            content TEXT,
            file_data TEXT,
            file_type TEXT,
            file_name TEXT,
            file_path TEXT,
            model_name TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            response_time TEXT,
            mm_time TEXT,
            gen_time TEXT,
            mm_model TEXT,
            gen_model TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            filename TEXT,
            file_size INTEGER,
            file_ext TEXT,
            file_path TEXT,
            index_status TEXT,
            indexed_at TIMESTAMP,
            indexing_started_at TIMESTAMP,
            embedding_model TEXT,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS session_visits (
            user_id TEXT,
            session_id TEXT,
            last_visit TIMESTAMP,
            PRIMARY KEY (user_id, session_id)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS model_configs (
            module TEXT PRIMARY KEY,
            model_name TEXT,
            context_length INTEGER,
            temperature REAL,
            top_p REAL,
            timeout INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            service_url TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_storage (
            user_id TEXT PRIMARY KEY,
            used_bytes INTEGER DEFAULT 0
        )
    ''')

    # Create indexes
    c.execute('CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_messages_session_timestamp ON messages(session_id, timestamp)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_messages_role ON messages(role)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id ON user_sessions(user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_session_visits_user_session ON session_visits(user_id, session_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_id ON chat_sessions(user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_documents_index_status ON documents(index_status)')

    # Seed default model_configs if not present
    c.execute("SELECT COUNT(*) as cnt FROM model_configs")
    if c.fetchone()['cnt'] == 0:
        c.execute('''
            INSERT INTO model_configs (module, model_name, context_length, temperature, top_p, timeout, service_url)
            VALUES
                ('chat', 'Qwen3-4B-Instruct-2507-Q4_K_M', 8192, 0.1, 0.1, 120, 'http://llamacpp:8033'),
                ('reasoning', 'gpt-oss-20b-mxfp4', 8192, 0.7, 0.9, 120, 'http://llamacpp:8033'),
                ('multimodal', 'Qwen3VL-8B-Instruct-Q4_K_M', 8192, 0.7, 0.9, 120, 'http://llamacpp:8033'),
                ('embedding', 'bge-m3-Q8_0', 512, NULL, NULL, 120, 'http://llamacpp:8033')
        ''')

    conn.commit()
    conn.close()
    logger.info("PostgreSQL database initialized")


def get_database_type() -> str:
    """Return the database type."""
    return 'postgresql'


def is_postgresql() -> bool:
    """Always True — PostgreSQL is the only supported database."""
    return True
