# app/userdb.py
import sqlite3
import json
import os
from werkzeug.security import generate_password_hash, check_password_hash
from flask import current_app

USER_DB_PATH = 'data/users.db'

def get_db():
    """Return a connection to the user database."""
    conn = sqlite3.connect(USER_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_user_db():
    """Initialize the user table."""
    if not os.path.exists('data'):
        os.makedirs('data', exist_ok=True)
    with get_db() as conn:
        # Check if columns exist and add them if not
        conn.execute('''
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
        # For existing databases, add columns if missing
        cursor = conn.execute("PRAGMA table_info(users)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'language' not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN language TEXT DEFAULT 'ru'")
        if 'voice_gender' not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN voice_gender TEXT DEFAULT 'male'")
        if 'theme' not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN theme TEXT DEFAULT 'light'")
        conn.commit()

def get_user_by_login(login):
    """Get a user by login."""
    with get_db() as conn:
        return conn.execute('SELECT * FROM users WHERE login = ?', (login,)).fetchone()

def create_user(login, password, name, service_class=2, is_admin=False, camera_permissions=None, language='ru', voice_gender='male', theme='light'):
    """Create a new user."""
    if camera_permissions is not None:
        camera_permissions = json.dumps(camera_permissions)
    password_hash = generate_password_hash(password)
    with get_db() as conn:
        conn.execute('''
            INSERT INTO users (login, name, password_hash, service_class, is_admin, camera_permissions, language, voice_gender, theme)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (login, name, password_hash, service_class, is_admin, camera_permissions, language, voice_gender, theme))
        conn.commit()

def update_user(login, name=None, service_class=None, is_active=None, camera_permissions=None, language=None, voice_gender=None, theme=None):
    """Update user data (except password)."""
    updates = []
    params = []
    if name is not None:
        updates.append("name = ?")
        params.append(name)
    if service_class is not None:
        updates.append("service_class = ?")
        params.append(service_class)
    if is_active is not None:
        updates.append("is_active = ?")
        params.append(int(is_active))
    if camera_permissions is not None:
        updates.append("camera_permissions = ?")
        params.append(json.dumps(camera_permissions) if camera_permissions is not None else None)
    if language is not None:
        updates.append("language = ?")
        params.append(language)
    if voice_gender is not None:
        updates.append("voice_gender = ?")
        params.append(voice_gender)
    if theme is not None:
        updates.append("theme = ?")
        params.append(theme)
    if not updates:
        return
    params.append(login)
    with get_db() as conn:
        conn.execute(f'UPDATE users SET {", ".join(updates)}, updated_at = CURRENT_TIMESTAMP WHERE login = ?', params)
        conn.commit()

def update_password(login, new_password):
    """Update a user's password."""
    password_hash = generate_password_hash(new_password)
    with get_db() as conn:
        conn.execute('UPDATE users SET password_hash = ? WHERE login = ?', (password_hash, login))
        conn.commit()

def delete_user(login):
    """Delete a user."""
    with get_db() as conn:
        conn.execute('DELETE FROM users WHERE login = ?', (login,))
        conn.commit()

def list_users(exclude_admin=True):
    """List all users (excluding admin if exclude_admin=True)."""
    with get_db() as conn:
        if exclude_admin:
            return conn.execute('SELECT * FROM users WHERE login != "admin" ORDER BY login').fetchall()
        else:
            return conn.execute('SELECT * FROM users ORDER BY login').fetchall()

def check_camera_permission(login, room_code):
    """Check if a user has permission to access a specific camera."""
    user = get_user_by_login(login)
    if not user or not user['is_active']:
        return False
    if user['camera_permissions'] is None:
        return True
    try:
        allowed = json.loads(user['camera_permissions'])
        return room_code in allowed
    except:
        return False