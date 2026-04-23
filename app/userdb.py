# app/userdb.py
"""User database module — PostgreSQL only."""
import json
from typing import Any, Dict, List, Optional
from werkzeug.security import generate_password_hash, check_password_hash
from app.database import get_db


def get_user_by_login(login: str) -> Optional[Dict[str, Any]]:
    """Get a user by login. Returns dict or None."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE login = %s', (login,))
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
        c.execute('''
            INSERT INTO users (login, name, password_hash, service_class, is_admin,
                               camera_permissions, language, voice_gender, theme)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (login) DO NOTHING
        ''', (login, name, password_hash, service_class, is_admin,
              camera_permissions, language, voice_gender, theme))


def update_user(login, name=None, service_class=None, is_active=None,
                camera_permissions=None, language=None, voice_gender=None, theme=None):
    """Update user data (except password)."""
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
            if field == 'camera_permissions':
                params.append(json.dumps(value))
            elif field == 'is_active':
                params.append(bool(value))
            else:
                params.append(value)
            updates.append(f"{field} = %s")

    if not updates:
        return

    params.append(login)
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            f"UPDATE users SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP WHERE login = %s",
            params
        )


def update_password(login, new_password):
    """Update a user's password."""
    password_hash = generate_password_hash(new_password)
    with get_db() as conn:
        c = conn.cursor()
        c.execute('UPDATE users SET password_hash = %s, updated_at = CURRENT_TIMESTAMP WHERE login = %s',
                  (password_hash, login))


def delete_user(login):
    """Delete a user."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM users WHERE login = %s', (login,))


def list_users(exclude_admin=True):
    """List all users."""
    with get_db() as conn:
        c = conn.cursor()
        if exclude_admin:
            c.execute('SELECT * FROM users WHERE login != %s ORDER BY login', ('admin',))
        else:
            c.execute('SELECT * FROM users ORDER BY login')
        return [dict(r) for r in c.fetchall()]


def check_camera_permission(login, room_code):
    """Check if a user has permission to access a specific camera."""
    user = get_user_by_login(login)
    if not user or not user.get('is_active'):
        return False
    if user.get('camera_permissions') is None:
        return False
    try:
        perms = user['camera_permissions']
        if isinstance(perms, str):
            allowed = json.loads(perms)
        else:
            allowed = perms
        return room_code in allowed
    except Exception:
        return False


def init_user_db() -> None:
    """Initialize the users table."""
    with get_db() as conn:
        c = conn.cursor()
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
    _ensure_admin_exists()


def _ensure_admin_exists():
    """Create default admin user if it doesn't exist."""
    try:
        user = get_user_by_login('admin')
        if user is None:
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
        pass
