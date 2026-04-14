# app/userdb.py
"""User database module — works with both SQLite and PostgreSQL.

Uses the unified database abstraction from app.database.
Users table lives in the same database as all other tables (no separate users.db).
"""
import json
import os
from typing import Any, Dict, List, Optional
from werkzeug.security import generate_password_hash, check_password_hash
from flask import current_app
from app.database import get_db, get_database_type, is_postgresql, is_sqlite


# Row factory depends on database type
def _row_to_dict(row, cursor_description=None):
    """Convert a database row to a dict (works with both sqlite3.Row and psycopg2)."""
    if hasattr(row, 'keys'):
        # sqlite3.Row or psycopg2.extras.RealDictRow
        return dict(row)
    elif isinstance(row, (list, tuple)) and cursor_description:
        return dict(zip([d[0] for d in cursor_description], row))
    return dict(row)


def get_user_db_conn():
    """Get a connection to the user database (same as main DB)."""
    return get_db()


def init_user_db() -> None:
    """Initialize the users table in the configured database."""
    with get_db() as conn:
        c = conn.cursor()

        if is_postgresql():
            # PostgreSQL schema
            c.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    login TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    service_class INTEGER NOT NULL DEFAULT 2,
                    is_active BOOLEAN NOT NULL DEFAULT true,
                    is_admin BOOLEAN NOT NULL DEFAULT false,
                    camera_permissions TEXT,
                    language TEXT DEFAULT 'ru',
                    voice_gender TEXT DEFAULT 'male',
                    theme TEXT DEFAULT 'light',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        else:
            # SQLite schema
            c.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    login TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    service_class INTEGER NOT NULL DEFAULT 2,
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    is_admin BOOLEAN NOT NULL DEFAULT 0,
                    camera_permissions TEXT,
                    language TEXT DEFAULT 'ru',
                    voice_gender TEXT DEFAULT 'male',
                    theme TEXT DEFAULT 'light',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Add columns for existing SQLite databases (migrations)
            try:
                cursor = c.execute("PRAGMA table_info(users)")
                columns = [col[1] for col in cursor.fetchall()]
                if 'language' not in columns:
                    c.execute("ALTER TABLE users ADD COLUMN language TEXT DEFAULT 'ru'")
                if 'voice_gender' not in columns:
                    c.execute("ALTER TABLE users ADD COLUMN voice_gender TEXT DEFAULT 'male'")
                if 'theme' not in columns:
                    c.execute("ALTER TABLE users ADD COLUMN theme TEXT DEFAULT 'light'")
            except Exception:
                pass  # Column already exists or non-SQLite

        conn.commit()

    # Create default admin user if not exists
    _ensure_admin_exists()


def _ensure_admin_exists():
    """Create default admin user if it doesn't exist in the database."""
    try:
        user = get_user_by_login('admin')
        if user is None:
            # Admin doesn't exist — create with placeholder password
            # Password must be set via CLI: flask admin-password <password>
            from werkzeug.security import generate_password_hash
            import secrets
            placeholder_pw = secrets.token_urlsafe(32)
            create_user(
                login='admin',
                password=placeholder_pw,
                name='Administrator',
                service_class=0,
                is_admin=True
            )
    except Exception:
        pass  # Table may not exist yet — will be created on next init


def get_user_by_login(login: str) -> Optional[Dict[str, Any]]:
    """Get a user by login. Returns dict or None."""
    from app.database import is_postgresql, get_db_connection

    if is_postgresql():
        conn = get_db_connection()
        try:
            c = conn.cursor()
            c.execute('SELECT * FROM users WHERE login = %s', (login,))
            row = c.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    else:
        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM users WHERE login = ?', (login,))
            row = c.fetchone()
            return dict(row) if row else None


def create_user(
    login: str,
    password: str,
    name: str,
    service_class: int = 2,
    is_admin: bool = False,
    camera_permissions: Optional[List[str]] = None,
    language: str = 'ru',
    voice_gender: str = 'male',
    theme: str = 'light'
) -> None:
    """Create a new user."""
    if camera_permissions is not None:
        camera_permissions = json.dumps(camera_permissions)
    password_hash = generate_password_hash(password)

    with get_db() as conn:
        c = conn.cursor()
        if is_postgresql():
            c.execute('''
                INSERT INTO users (login, name, password_hash, service_class, is_admin,
                                   camera_permissions, language, voice_gender, theme)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (login) DO NOTHING
            ''', (login, name, password_hash, service_class, is_admin,
                  camera_permissions, language, voice_gender, theme))
        else:
            c.execute('''
                INSERT OR IGNORE INTO users
                (login, name, password_hash, service_class, is_admin,
                 camera_permissions, language, voice_gender, theme)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (login, name, password_hash, service_class, is_admin,
                  camera_permissions, language, voice_gender, theme))
        conn.commit()


def update_user(login, name=None, service_class=None, is_active=None,
                camera_permissions=None, language=None, voice_gender=None, theme=None):
    """Update user data (except password)."""
    ALLOWED_COLUMNS = {
        'name': 'name',
        'service_class': 'service_class',
        'is_active': 'is_active',
        'camera_permissions': 'camera_permissions',
        'language': 'language',
        'voice_gender': 'voice_gender',
        'theme': 'theme'
    }

    updates = []
    params = []

    values_to_update = {
        'name': name,
        'service_class': service_class,
        'is_active': is_active,
        'camera_permissions': camera_permissions,
        'language': language,
        'voice_gender': voice_gender,
        'theme': theme
    }

    for field, value in values_to_update.items():
        if value is not None:
            if field not in ALLOWED_COLUMNS:
                raise ValueError(f"Invalid field name: {field}")
            column_name = ALLOWED_COLUMNS[field]
            if is_postgresql():
                updates.append(f"{column_name} = %s")
            else:
                updates.append(f"{column_name} = ?")
            if field == 'camera_permissions':
                params.append(json.dumps(value) if value is not None else None)
            elif field == 'is_active':
                params.append(bool(value))
            else:
                params.append(value)

    if not updates:
        return

    params.append(login)
    with get_db() as conn:
        c = conn.cursor()
        placeholder = '%s' if is_postgresql() else '?'
        c.execute(
            f"UPDATE users SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP WHERE login = {placeholder}",
            params
        )
        conn.commit()


def update_password(login, new_password):
    """Update a user's password."""
    password_hash = generate_password_hash(new_password)
    with get_db() as conn:
        c = conn.cursor()
        if is_postgresql():
            c.execute('UPDATE users SET password_hash = %s, updated_at = CURRENT_TIMESTAMP WHERE login = %s',
                      (password_hash, login))
        else:
            c.execute('UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE login = ?',
                      (password_hash, login))
        conn.commit()


def delete_user(login):
    """Delete a user."""
    with get_db() as conn:
        c = conn.cursor()
        if is_postgresql():
            c.execute('DELETE FROM users WHERE login = %s', (login,))
        else:
            c.execute('DELETE FROM users WHERE login = ?', (login,))
        conn.commit()


def list_users(exclude_admin=True):
    """List all users (excluding admin if exclude_admin=True)."""
    from app.database import is_postgresql, get_db_connection

    if is_postgresql():
        conn = get_db_connection()
        try:
            c = conn.cursor()
            if exclude_admin:
                c.execute('SELECT * FROM users WHERE login != %s ORDER BY login', ('admin',))
            else:
                c.execute('SELECT * FROM users ORDER BY login')
            rows = c.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    else:
        with get_db() as conn:
            c = conn.cursor()
            if exclude_admin:
                c.execute("SELECT * FROM users WHERE login != 'admin' ORDER BY login")
            else:
                c.execute('SELECT * FROM users ORDER BY login')
            rows = c.fetchall()
            return [dict(row) for row in rows]


def check_camera_permission(login, room_code):
    """Check if a user has permission to access a specific camera.

    Deny-by-default: if camera_permissions is NULL/None, access is denied.
    An explicit list of room codes must be set to grant access.
    """
    user = get_user_by_login(login)
    if not user or not user.get('is_active'):
        return False
    if user.get('camera_permissions') is None:
        return False  # Deny by default
    try:
        perms = user['camera_permissions']
        if isinstance(perms, str):
            allowed = json.loads(perms)
        else:
            allowed = perms
        return room_code in allowed
    except Exception:
        return False
