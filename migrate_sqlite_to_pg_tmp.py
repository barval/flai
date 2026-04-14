#!/usr/bin/env python3
"""
Migrate data from SQLite to PostgreSQL for FLAI.

Reads from:
  - data/users.db    (users table)
  - data/chats.db    (all other tables)

Writes to:
  - PostgreSQL DATABASE_URL from .env

Usage:
    python3 migrate_sqlite_to_pg.py
"""
import os
import sys
import sqlite3
import json
from dotenv import load_dotenv

load_dotenv()

# ── Config ──
SQLITE_USERS_PATH = 'data/users.db'
SQLITE_CHATS_PATH = 'data/chats.db'
PG_URL = os.getenv('DATABASE_URL')

if not PG_URL or not PG_URL.startswith('postgresql'):
    print("ERROR: DATABASE_URL must be set to PostgreSQL in .env")
    sys.exit(1)

# ── Connect ──
import psycopg2
from psycopg2.extras import RealDictCursor, execute_batch, execute_values

pg_conn = psycopg2.connect(PG_URL.replace('postgres://', 'postgresql://'))
pg_conn.set_session(autocommit=True)
pg_cur = pg_conn.cursor(cursor_factory=RealDictCursor)

print(f"Connected to PostgreSQL: {PG_URL.split('@')[1]}")

# ── Migrate users.db → users table ──
if os.path.exists(SQLITE_USERS_PATH):
    sq_conn = sqlite3.connect(SQLITE_USERS_PATH)
    sq_conn.row_factory = sqlite3.Row
    sq = sq_conn.cursor()

    sq.execute("SELECT * FROM users")
    users = [dict(row) for row in sq.fetchall()]

    if users:
        print(f"\nMigrating {len(users)} users from {SQLITE_USERS_PATH}...")
        for u in users:
            # Convert SQLite booleans to PostgreSQL booleans
            u['is_active'] = bool(u['is_active'])
            u['is_admin'] = bool(u['is_admin'])

            pg_cur.execute("""
                INSERT INTO users (id, login, name, password_hash, service_class,
                                   is_active, is_admin, camera_permissions,
                                   language, voice_gender, theme, created_at, updated_at)
                VALUES (%(id)s, %(login)s, %(name)s, %(password_hash)s, %(service_class)s,
                        %(is_active)s, %(is_admin)s, %(camera_permissions)s,
                        %(language)s, %(voice_gender)s, %(theme)s, %(created_at)s, %(updated_at)s)
                ON CONFLICT (id) DO NOTHING
            """, u)

        # Reset sequence
        pg_cur.execute("SELECT setval('users_id_seq', (SELECT MAX(id) FROM users))")

        sq_conn.close()
        print(f"  ✅ {len(users)} users migrated")
    else:
        print(f"  ⚠ No users found in {SQLITE_USERS_PATH}")
else:
    print(f"  ⚠ {SQLITE_USERS_PATH} not found — skipping")

# ── Migrate chats.db → all other tables ──
if os.path.exists(SQLITE_CHATS_PATH):
    sq2 = sqlite3.connect(SQLITE_CHATS_PATH)
    sq2.row_factory = sqlite3.Row
    sq2_cur = sq2.cursor()

    # Table mapping: sqlite_table → pg_table (same name)
    tables = [
        'user_sessions',
        'chat_sessions',
        'messages',
        'session_visits',
        'documents',
        'model_configs',
        'user_storage',
    ]

    for table in tables:
        sq2_cur.execute(f"PRAGMA table_info({table})")
        sqlite_cols = [row['name'] for row in sq2_cur.fetchall()]

        sq2_cur.execute(f"SELECT * FROM {table}")
        rows_raw = sq2_cur.fetchall()

        # Filter out columns that don't exist in PostgreSQL
        PG_COLUMNS = {
            'documents': {'id', 'user_id', 'filename', 'file_size', 'file_ext',
                          'file_path', 'index_status', 'indexed_at',
                          'indexing_started_at', 'embedding_model', 'uploaded_at'},
        }
        allowed_cols = set(PG_COLUMNS.get(table, set(sqlite_cols))) & set(sqlite_cols)

        rows = []
        for r in rows_raw:
            row_dict = dict(r)
            # Keep only columns that exist in PG
            filtered = {k: v for k, v in row_dict.items() if k in allowed_cols}
            rows.append(filtered)

        if not rows:
            print(f"  ⚠ {table}: empty")
            continue

        print(f"\nMigrating {len(rows)} rows from {table}...")

        # Build column list and placeholders
        cols = list(rows[0].keys())
        col_names = ', '.join(cols)
        col_placeholders = ', '.join([f'%({c})s' for c in cols])

        # Convert booleans for PostgreSQL
        bool_cols = ['is_active', 'is_admin']
        for row in rows:
            for bc in bool_cols:
                if bc in row:
                    row[bc] = bool(row[bc])

        insert_sql = f"""
            INSERT INTO {table} ({col_names})
            VALUES ({col_placeholders})
            ON CONFLICT DO NOTHING
        """

        execute_batch(pg_cur, insert_sql, rows)
        print(f"  ✅ {table}: {len(rows)} rows migrated")

    # Reset sequences
    print("\nResetting sequences...")
    for seq_table in ['messages']:
        pg_cur.execute(f"SELECT setval('{seq_table}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {seq_table}))")

    sq2.close()
    print("  ✅ Sequences reset")
else:
    print(f"  ⚠ {SQLITE_CHATS_PATH} not found — skipping")

# ── Verify ──
print("\n" + "=" * 50)
print("Migration complete. Verification:")
print("=" * 50)

for table in ['users', 'chat_sessions', 'messages', 'documents', 'model_configs', 'user_sessions', 'session_visits', 'user_storage']:
    try:
        pg_cur.execute(f"SELECT COUNT(*) FROM {table}")
        count = pg_cur.fetchone()[0]
        print(f"  {table}: {count} rows")
    except Exception as e:
        print(f"  {table}: ERROR — {e}")

pg_cur.close()
pg_conn.close()

print("\n✅ Migration finished successfully!")
