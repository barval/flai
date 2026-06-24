"""PostgreSQL database abstraction layer.

This module provides the database connection and initialization
for the FLAI application using PostgreSQL.

DATABASE_URL must be set in .env:
    DATABASE_URL=postgresql://user:pass@host:5432/flai
"""

import logging
import os
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL must be set in .env (postgresql://user:pass@host:5432/flai)")
if not DATABASE_URL.startswith("postgresql://") and not DATABASE_URL.startswith("postgres://"):
    raise ValueError(f"Only PostgreSQL is supported. Got: {DATABASE_URL}")

logger.info(f"Using PostgreSQL database: {DATABASE_URL}")


def get_db_connection():
    """Get a PostgreSQL connection with RealDictCursor (dict-like results)."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    url = DATABASE_URL.replace("postgres://", "postgresql://")
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
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()


def init_db():
    """Initialize the PostgreSQL database (create tables)."""
    _init_postgresql()


def _init_postgresql():
    """Initialize PostgreSQL schema."""
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS user_sessions (
            user_id TEXT PRIMARY KEY,
            last_session_id TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            title TEXT,
            model_name TEXT DEFAULT 'auto',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
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
    """)
    c.execute("""
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
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS session_visits (
            user_id TEXT,
            session_id TEXT,
            last_visit TIMESTAMP,
            PRIMARY KEY (user_id, session_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS model_configs (
            module TEXT PRIMARY KEY,
            model_name TEXT,
            context_length INTEGER,
            temperature REAL,
            top_p REAL,
            timeout INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            service_url TEXT,
            model_path TEXT,
            aliases TEXT,
            group_name TEXT DEFAULT 'default',
            ttl INTEGER DEFAULT 0,
            preload BOOLEAN DEFAULT FALSE,
            repeat_penalty REAL DEFAULT 1.1
        )
    """)
    c.execute("""
        DO $migrate$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                          WHERE table_name = 'model_configs' AND column_name = 'repeat_penalty') THEN
                ALTER TABLE model_configs ADD COLUMN repeat_penalty REAL DEFAULT 1.1;
            END IF;
        END
        $migrate$
    """)
    # Fix default for reasoning — should be 1.15, not 1.1
    # NOTE: ::real cast is needed because real != numeric (1.1 vs 1.100000023841858)
    c.execute("""
        UPDATE model_configs SET repeat_penalty = 1.15::real
        WHERE module = 'reasoning' AND (repeat_penalty IS NULL OR repeat_penalty = 1.1::real)
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_storage (
            user_id TEXT PRIMARY KEY,
            used_bytes INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS slm_import_progress (
            user_id TEXT PRIMARY KEY,
            last_message_id INTEGER NOT NULL DEFAULT 0,
            total_imported INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Create indexes
    c.execute("CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_messages_session_timestamp ON messages(session_id, timestamp)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_messages_role ON messages(role)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id ON user_sessions(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_session_visits_user_session ON session_visits(user_id, session_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_id ON chat_sessions(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_documents_index_status ON documents(index_status)")

    # Seed default model_configs if not present — architecture-aware
    # MXFP4 on Blackwell GPUs (native FP4), Q4_0/Q4_K_M on others
    c.execute("SELECT COUNT(*) as cnt FROM model_configs")
    if c.fetchone()["cnt"] == 0:
        from app.utils import is_blackwell_gpu

        if is_blackwell_gpu():
            chat_model = "Qwen3-4B-Instruct-2507-MXFP4_MOE.gguf"
            reasoning_model = "gpt-oss-20b-mxfp4"
        else:
            chat_model = "Qwen3-4B-Instruct-2507-Q4_0.gguf"
            reasoning_model = "gpt-oss-20b-Q4_K_M"
        c.execute("""
            INSERT INTO model_configs (module, model_name, context_length, temperature, top_p, timeout, service_url, repeat_penalty)
            VALUES
                ('chat', %s, 16384, 0.7, 0.9, 120, 'http://flai-llamacpp:8033', 1.1),
                ('reasoning', %s, 16384, 0.7, 0.9, 120, 'http://flai-llamacpp:8033', 1.15),
                ('multimodal', 'Qwen3VL-8B-Instruct-Q4_K_M', 16384, 0.7, 0.9, 120, 'http://flai-llamacpp:8033', 1.1),
                ('embedding', 'bge-m3-Q8_0', 512, NULL, NULL, 120, 'http://flai-llamacpp:8033', NULL)
        """, (chat_model, reasoning_model))

    # model_vram_estimates — stores computed estimates and actual VRAM measurements per model
    c.execute("""
        CREATE TABLE IF NOT EXISTS model_vram_estimates (
            module TEXT NOT NULL,
            model_name TEXT NOT NULL DEFAULT 'unknown',
            context_length INTEGER,
            n_gpu_layers INTEGER,
            estimated_vram_mb INTEGER,
            measured_vram_mb INTEGER,
            measurement_count INTEGER DEFAULT 0,
            last_measured_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (module, model_name)
        )
    """)

    # Migration: PK (module) → (module, model_name).
    # Older deployments had PRIMARY KEY (module) and allowed model_name=NULL,
    # which caused "phantom" measurements to leak between models in the same module.
    c.execute("""
        DO $migrate$
        DECLARE
            pk_cols TEXT;
            has_model_name_col BOOLEAN;
        BEGIN
            -- Drop old single-column PK if it exists
            SELECT string_agg(a.attname, ',' ORDER BY a.attnum) INTO pk_cols
            FROM pg_constraint c
            JOIN pg_class t ON c.conrelid = t.oid
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(c.conkey)
            WHERE t.relname = 'model_vram_estimates' AND c.contype = 'p';

            IF pk_cols = 'module' THEN
                -- Backfill NULL model_names before tightening the schema
                UPDATE model_vram_estimates SET model_name = 'unknown' WHERE model_name IS NULL;
                -- If the unknown group already exists for this module, merge: keep the row
                -- with most measurements, delete the duplicates.
                DELETE FROM model_vram_estimates a
                USING model_vram_estimates b
                WHERE a.module = b.module
                  AND a.model_name = 'unknown' AND b.model_name = 'unknown'
                  AND a.ctid <> b.ctid
                  AND (a.measurement_count, a.updated_at) < (b.measurement_count, b.updated_at);
                ALTER TABLE model_vram_estimates DROP CONSTRAINT model_vram_estimates_pkey;
                ALTER TABLE model_vram_estimates ALTER COLUMN model_name SET DEFAULT 'unknown';
                ALTER TABLE model_vram_estimates ALTER COLUMN model_name SET NOT NULL;
                ALTER TABLE model_vram_estimates ADD PRIMARY KEY (module, model_name);
            END IF;

            -- Make model_name NOT NULL if it isn't already (in case PK migration
            -- was skipped because PK was already composite from a fresh install)
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'model_vram_estimates'
                  AND column_name = 'model_name' AND is_nullable = 'YES'
            ) INTO has_model_name_col;

            IF has_model_name_col THEN
                UPDATE model_vram_estimates SET model_name = 'unknown' WHERE model_name IS NULL;
                ALTER TABLE model_vram_estimates ALTER COLUMN model_name SET DEFAULT 'unknown';
                ALTER TABLE model_vram_estimates ALTER COLUMN model_name SET NOT NULL;
            END IF;
        END
        $migrate$
    """)

    # Add response_style column to messages table
    c.execute("""
        DO $migrate$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                          WHERE table_name = 'messages' AND column_name = 'response_style') THEN
                ALTER TABLE messages ADD COLUMN response_style TEXT DEFAULT 'neutral';
            END IF;
        END
        $migrate$
    """)

    # Add completion_tokens column to messages table
    c.execute("""
        DO $migrate$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                          WHERE table_name = 'messages' AND column_name = 'completion_tokens') THEN
                ALTER TABLE messages ADD COLUMN completion_tokens INTEGER;
            END IF;
        END
        $migrate$
    """)

    # camera_rooms — configurable camera/room definitions (single source of truth)
    c.execute("""
        CREATE TABLE IF NOT EXISTS camera_rooms (
            code        TEXT PRIMARY KEY,
            name_forms  TEXT[] NOT NULL DEFAULT '{}',
            enabled     BOOLEAN DEFAULT TRUE,
            sort_order  INTEGER DEFAULT 0,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Migration: drop unused columns if they exist from previous schema
    c.execute("""
        DO $migrate$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                      WHERE table_name = 'camera_rooms' AND column_name = 'name_en') THEN
                ALTER TABLE camera_rooms DROP COLUMN name_en;
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.columns
                      WHERE table_name = 'camera_rooms' AND column_name = 'rtsp_ip') THEN
                ALTER TABLE camera_rooms DROP COLUMN rtsp_ip;
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.columns
                      WHERE table_name = 'camera_rooms' AND column_name = 'rtsp_port') THEN
                ALTER TABLE camera_rooms DROP COLUMN rtsp_port;
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.columns
                      WHERE table_name = 'camera_rooms' AND column_name = 'rtsp_stream') THEN
                ALTER TABLE camera_rooms DROP COLUMN rtsp_stream;
            END IF;
        END
        $migrate$
    """)

    # Switch chat model back to Qwen3-4B-Instruct-2507-MXFP4_MOE.gguf (Instruct, ~2 GB)
    # Match both with and without .gguf suffix (admin panel may store either form)
    for old_name in [
        'Qwen3-1.7B-Q8_0.gguf', 'Qwen3-1.7B-Instruct-Q4_K_M',
        'Qwen3-4B-Instruct-2507-Q4_K_M', 'Qwen3-4B-Instruct-2507-Q4_K_M.gguf',
    ]:
        c.execute("""
            UPDATE model_configs
            SET model_name = 'Qwen3-4B-Instruct-2507-MXFP4_MOE.gguf'
            WHERE module = 'chat' AND model_name = %s
        """, (old_name,))

    # Update chat model defaults: temperature 0.1→0.7, top_p 0.1→0.9
    # (old values were for router classification, not suitable for chat responses)
    c.execute("""
        UPDATE model_configs
        SET temperature = 0.7, top_p = 0.9
        WHERE module = 'chat'
          AND temperature = 0.1 AND top_p = 0.1
    """)

    # Update multimodal model context_length: 8192→16384
    # (8192 too small for vision token counts from Qwen3VL)
    c.execute("""
        UPDATE model_configs
        SET context_length = 16384
        WHERE module = 'multimodal' AND context_length = 8192
    """)

    conn.commit()
    conn.close()
    logger.info("PostgreSQL database initialized")


# ── VRAM estimates helpers ──────────────────────────────────────

def get_vram_estimate(
    module: str,
    model_name: str | None = None,
) -> dict | None:
    """Get VRAM estimate for a module from model_vram_estimates table.

    If model_name is provided — exact (module, model_name) match.
    If model_name is None — most recently updated record for the module
    (legacy behavior; returns None if no rows).
    """
    with get_db() as conn:
        c = conn.cursor()
        if model_name is not None:
            c.execute(
                "SELECT * FROM model_vram_estimates WHERE module = %s AND model_name = %s",
                (module, model_name),
            )
        else:
            c.execute(
                "SELECT * FROM model_vram_estimates WHERE module = %s "
                "ORDER BY updated_at DESC LIMIT 1",
                (module,),
            )
        row = c.fetchone()
        return dict(row) if row else None


def upsert_vram_estimate(
    module: str,
    model_name: str,
    context_length: int,
    n_gpu_layers: int,
    estimated_mb: int | None = None,
    measured_mb: int | None = None,
) -> None:
    """Insert or update VRAM estimate for a (module, model_name) row.

    Measurements are scoped to the specific model_name; switching to a new model
    in the same module creates a fresh row with measured_vram_mb=NULL and
    measurement_count=0 — old measurements stay attached to the previous model.
    """
    with get_db() as conn:
        c = conn.cursor()

        existing = None
        c.execute(
            "SELECT * FROM model_vram_estimates WHERE module = %s AND model_name = %s",
            (module, model_name),
        )
        row = c.fetchone()
        if row:
            existing = dict(row)

        if existing:
            if measured_mb is not None:
                new_count = (existing.get("measurement_count") or 0) + 1
                # Weighted average: smooth measurements over time
                old_weight = min(new_count - 1, 10)  # cap at 10 for smoothing
                new_weight = 1
                total_weight = old_weight + new_weight
                avg_mb = (
                    (existing.get("measured_vram_mb") or 0) * old_weight +
                    measured_mb * new_weight
                ) // total_weight
                c.execute("""
                    UPDATE model_vram_estimates
                    SET model_name = %s, context_length = %s, n_gpu_layers = %s,
                        estimated_vram_mb = COALESCE(%s, estimated_vram_mb),
                        measured_vram_mb = %s,
                        measurement_count = %s,
                        last_measured_at = NOW(),
                        updated_at = NOW()
                    WHERE module = %s AND model_name = %s
                """, (model_name, context_length, n_gpu_layers,
                     estimated_mb, avg_mb, new_count, module, model_name))
            else:
                c.execute("""
                    UPDATE model_vram_estimates
                    SET model_name = %s, context_length = %s, n_gpu_layers = %s,
                        estimated_vram_mb = COALESCE(%s, estimated_vram_mb),
                        updated_at = NOW()
                    WHERE module = %s AND model_name = %s
                """, (model_name, context_length, n_gpu_layers,
                     estimated_mb, module, model_name))
        else:
            c.execute("""
                INSERT INTO model_vram_estimates
                    (module, model_name, context_length, n_gpu_layers,
                     estimated_vram_mb, measured_vram_mb,
                     measurement_count, last_measured_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            """, (module, model_name, context_length, n_gpu_layers,
                 estimated_mb, measured_mb,
                 1 if measured_mb is not None else 0))
        conn.commit()
