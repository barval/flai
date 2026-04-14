"""Database abstraction layer supporting both SQLite and PostgreSQL.

This module provides a unified interface for database operations,
allowing the application to use either SQLite (default) or PostgreSQL.

Usage:
    # In .env:
    DATABASE_URL=sqlite:///data/chats.db          # SQLite (default)
    DATABASE_URL=postgresql://user:pass@host:5432/flai  # PostgreSQL

    # The app automatically detects and uses the appropriate driver.
"""
import os
import logging
from typing import Optional
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Database type detection
DATABASE_URL = os.getenv('DATABASE_URL')

if DATABASE_URL:
    if DATABASE_URL.startswith('postgresql://') or DATABASE_URL.startswith('postgres://'):
        DATABASE_TYPE = 'postgresql'
        logger.info(f"Using PostgreSQL database: {DATABASE_URL}")
    elif DATABASE_URL.startswith('sqlite:///'):
        DATABASE_TYPE = 'sqlite'
        DB_PATH = DATABASE_URL.replace('sqlite:///', '')
        logger.info(f"Using SQLite database: {DB_PATH}")
    else:
        raise ValueError(f"Unsupported database URL: {DATABASE_URL}")
else:
    # Default to SQLite for backward compatibility
    DATABASE_TYPE = 'sqlite'
    DB_PATH = os.getenv('DB_PATH', 'data/chats.db')
    logger.info(f"Using default SQLite database: {DB_PATH}")

# Always define DB_PATH (may be None for PostgreSQL)
if DATABASE_TYPE != 'sqlite':
    DB_PATH = None


def get_db_connection():
    """Get a database connection based on DATABASE_TYPE.

    Returns:
        A connection object (sqlite3.Connection or psycopg2.Connection)
    """
    if DATABASE_TYPE == 'sqlite':
        import sqlite3
        if not os.path.exists('data'):
            os.makedirs('data', exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        # Enable WAL mode for better concurrency
        conn.execute("PRAGMA journal_mode=WAL")
        return conn
    else:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        # Convert postgresql:// to psycopg2-compatible URL
        url = DATABASE_URL.replace('postgres://', 'postgresql://')
        conn = psycopg2.connect(url)
        # Set isolation level for better concurrency
        conn.set_session(autocommit=False)
        # Use RealDictCursor so fetchone/fetchall return dict-like objects
        conn.cursor_factory = RealDictCursor
        return conn


@contextmanager
def get_db():
    """Context manager for database connections.

    Usage:
        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT 1')
            row = c.fetchone()  # dict-like for both SQLite and PostgreSQL
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
    """Initialize the database (create tables)."""
    if DATABASE_TYPE == 'sqlite':
        from app.db import init_db as sqlite_init
        return sqlite_init()
    else:
        _init_postgresql()


def _init_postgresql():
    """Initialize PostgreSQL schema (mirrors SQLite schema)."""
    import psycopg2
    from psycopg2.extras import execute_batch

    conn = get_db_connection()
    c = conn.cursor()

    # Create tables
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
            ollama_url TEXT,
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

    conn.commit()
    conn.close()
    logger.info("PostgreSQL database initialized")


def get_database_type() -> str:
    """Return the current database type ('sqlite' or 'postgresql')."""
    return DATABASE_TYPE


def is_postgresql() -> bool:
    """Check if PostgreSQL is being used."""
    return DATABASE_TYPE == 'postgresql'


def is_sqlite() -> bool:
    """Check if SQLite is being used."""
    return DATABASE_TYPE == 'sqlite'
