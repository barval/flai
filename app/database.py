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

# ── Context-window auto-fit (GPU deployments) ────────────────────────────
# v11.4: the seed and context migrations pick the largest window that (a) does
# not exceed the model's architectural max (read from GGUF metadata) and (b)
# fits fully into VRAM, instead of blindly bumping to fixed values that can
# OOM or force partial offload on smaller cards. Goals are tiered by VRAM:
#   24 GB   — multimodal 32768 / reasoning 32768
#   16 GB   — multimodal 32768 / reasoning 24576  (reasoning 32768 needs 24 GB)
#   8–12 GB — multimodal 16384 / reasoning 16384  (16384 is the vision floor)
# Unknown VRAM keeps the previous defaults (32768 / 24576).
# (min_vram_mb, multimodal_goal, reasoning_goal)
_GPU_CTX_GOALS: tuple[tuple[int, int, int], ...] = (
    (24000, 32768, 32768),
    (16000, 32768, 24576),
    (8192, 16384, 16384),
)
_GPU_CTX_FALLBACK = (32768, 24576)
# Steps to try when the goal does not fit fully in VRAM (largest first).
_GPU_CTX_STEPS = (32768, 24576, 16384, 8192)


def _total_ram_mb() -> int:
    """Total system RAM in MB from /proc/meminfo (Linux) or psutil fallback."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    try:
        import psutil

        return psutil.virtual_memory().total // (1024 * 1024)  # type: ignore[no-any-return]
    except Exception:
        return 0


def _gpu_total_vram_mb() -> int:
    """Total VRAM in MB for the current platform, or 0 when undetectable."""
    try:
        from app.platform_detect import query_vram

        _used, total = query_vram()
        return total or 0
    except Exception:
        return 0


def _find_gguf_file(model_name: str, models_dir: str = "/models") -> str | None:
    """Locate a GGUF file by model name (with or without .gguf suffix)."""
    base = model_name[:-5] if model_name.endswith(".gguf") else model_name
    for candidate in (
        os.path.join(models_dir, base + ".gguf"),
        os.path.join(models_dir, base, base + ".gguf"),
    ):
        if os.path.exists(candidate):
            return candidate
    if not os.path.isdir(models_dir):
        return None
    for root, _dirs, files in os.walk(models_dir):
        for f in files:
            if f == base + ".gguf":
                return os.path.join(root, f)
    return None


def _read_gguf_meta(model_name: str, models_dir: str = "/models") -> dict:
    """Read context_length (architectural max), file size and block_count
    straight from the GGUF file header — no DB cache, safe during init_db."""
    path = _find_gguf_file(model_name, models_dir)
    if not path:
        return {}
    meta: dict = {"file_size_mb": os.path.getsize(path) / (1024 * 1024)}
    try:
        from gguf import GGUFReader
        from gguf.constants import GGML_QUANT_SIZES, GGMLQuantizationType

        for _dt in range(42, 64):
            if _dt not in GGMLQuantizationType._value2member_map_:
                _m = int.__new__(GGMLQuantizationType, _dt)
                _m._name_ = f"UNKNOWN_{_dt}"
                _m._value_ = _dt
                GGMLQuantizationType._member_map_[_dt] = _m  # type: ignore[index]
                GGMLQuantizationType._value2member_map_[_dt] = _m
                GGML_QUANT_SIZES[_m] = (1, 1)

        orig_build_tensors = GGUFReader._build_tensors
        GGUFReader._build_tensors = lambda self, *a, **kw: None  # type: ignore[method-assign]
        try:
            reader = GGUFReader(path)
        finally:
            GGUFReader._build_tensors = orig_build_tensors  # type: ignore[method-assign]
        arch = None
        for key in reader.fields:
            if "." in key and not key.startswith("GGUF") and not key.startswith("general"):
                arch = key.split(".")[0]
                break
        if arch:
            bc_key = f"{arch}.block_count"
            if bc_key in reader.fields:
                raw = reader.fields[bc_key].parts[-1]
                arr = raw.tolist() if hasattr(raw, "tolist") else None
                if isinstance(arr, list) and len(arr) == 1:
                    raw = arr[0]
                try:
                    meta["block_count"] = int(raw)
                except (TypeError, ValueError):
                    meta["block_count"] = None
            ec_key = f"{arch}.expert_count"
            if ec_key in reader.fields:
                raw = reader.fields[ec_key].parts[-1]
                arr = raw.tolist() if hasattr(raw, "tolist") else None
                if isinstance(arr, list) and len(arr) == 1:
                    raw = arr[0]
                try:
                    meta["expert_count"] = int(raw)
                except (TypeError, ValueError):
                    meta["expert_count"] = 0
            mtp_key = f"{arch}.nextn_predict_layers"
            if mtp_key in reader.fields:
                raw = reader.fields[mtp_key].parts[-1]
                arr = raw.tolist() if hasattr(raw, "tolist") else None
                if isinstance(arr, list) and len(arr) == 1:
                    raw = arr[0]
                try:
                    meta["supports_mtp"] = int(raw) > 0
                except (TypeError, ValueError):
                    meta["supports_mtp"] = False
        for key in reader.fields:
            if key.endswith(".context_length"):
                raw = reader.fields[key].parts[-1]
                arr = raw.tolist() if hasattr(raw, "tolist") else None
                if isinstance(arr, list) and len(arr) == 1:
                    raw = arr[0]
                try:
                    meta["arch_max_ctx"] = int(raw)
                except (TypeError, ValueError):
                    meta["arch_max_ctx"] = None
                break
    except Exception:
        pass
    return meta


def _autofit_context(
    module: str,
    model_name: str,
    goal: int,
    total_vram_mb: int,
    total_ram_mb: int,
    meta: dict,
) -> int:
    """Pick the largest context window that fits fully in VRAM.

    Caps the tier goal by the model's architectural max, then steps down
    through common sizes until the full-GPU VRAM footprint fits. Returns the
    goal (arch-capped) when nothing fits — the normal degradation path
    (reduce n_gpu_layers) still applies, but a smaller KV cache is preferred
    whenever possible.
    """
    from app.resource_manager import KV_PER_TOKEN_MB

    arch_max = meta.get("arch_max_ctx")
    # The vision module needs at least 16384 context for full image token counts.
    floor = 16384 if module == "multimodal" else _GPU_CTX_STEPS[3]
    candidates = [g for g in (goal, *_GPU_CTX_STEPS) if floor <= g <= goal and (arch_max is None or g <= arch_max)]
    if not candidates:
        # arch_max below the floor — use arch_max itself (best effort).
        if arch_max is not None:
            return max(0, arch_max if module == "multimodal" else min(goal, arch_max))
        return goal

    file_size_mb = meta.get("file_size_mb")
    if not file_size_mb:
        # No metadata on disk — keep the goal without VRAM-guessing.
        return candidates[0]
    file_mb = float(file_size_mb)
    moe_factor = 0.95 if meta.get("expert_count") else 1.0
    mtp_factor = 1.15 if meta.get("supports_mtp") else 1.0
    model_vram = file_mb * moe_factor * mtp_factor  # full GPU offload (ngl=all)

    mmproj_mb = 0
    if module == "multimodal":
        try:
            from app.utils import get_mmproj_size_mb

            mmproj_mb = get_mmproj_size_mb(model_name)
        except Exception:
            mmproj_mb = 0

    kv_per_token = KV_PER_TOKEN_MB.get(module, 0.08)
    vram_budget = (total_vram_mb or 0) * 0.85
    ram_budget = (total_ram_mb * 0.70) - 2048

    for ctx in candidates:
        kv_cache_mb = ctx * kv_per_token
        vram_full = model_vram + kv_cache_mb + 400 + mmproj_mb
        if total_vram_mb > 0 and vram_full > vram_budget:
            continue
        # Even fully offloaded to RAM the model must leave room for the OS.
        if total_ram_mb > 0 and file_mb + kv_cache_mb + mmproj_mb > ram_budget:
            continue
        return ctx
    # Nothing fits fully — the model needs partial CPU offload anyway, so
    # shrinking the KV cache won't make it fit; keep the (arch-capped) goal
    # and let the usual n_gpu_layers degradation handle the offload.
    return candidates[0]


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

    # Seed default model_configs if not present.
    # v11.4: CPU mode seeds lightweight models (Qwen3VL-4B + gpt-oss-20b-mxfp4)
    # with 8192 context; GPU mode seeds Qwen3VL-8B + Qwen3.6-35B.
    is_cpu = os.getenv("FLAI_PLATFORM", "").strip().lower() == "cpu"
    c.execute("SELECT COUNT(*) as cnt FROM model_configs")
    if c.fetchone()["cnt"] == 0:
        if is_cpu:
            # CPU mode: lightweight vision model + native MXFP4 reasoning
            c.execute(
                """
                INSERT INTO model_configs
                    (module, model_name, context_length, temperature, top_p, timeout, service_url, repeat_penalty)
                VALUES
                    ('multimodal', 'Qwen3VL-4B-Instruct-Q4_K_M', 8192, 0.7, 0.9, 120, 'http://flai-llamacpp:8033', 1.1),
                    ('reasoning', 'gpt-oss-20b-mxfp4', 8192, 0.7, 0.9, 120, 'http://flai-llamacpp:8033', 1.15),
                    ('embedding', 'bge-m3-Q8_0', 512, NULL, NULL, 120, 'http://flai-llamacpp:8033', NULL)
            """,
            )
        else:
            # GPU mode: full multimodal + Qwen3.6-35B reasoning.
            # Auto-fit context windows to the available VRAM so small GPUs
            # don't burn budget on oversized KV caches (see _GPU_CTX_GOALS).
            reasoning_model = "Qwen3.6-35B-A3B-UD-Q2_K_XL"
            multimodal_model = "Qwen3VL-8B-Instruct-Q4_K_M"
            total_vram_mb = _gpu_total_vram_mb()
            total_ram_mb = _total_ram_mb()
            mm_goal, rz_goal = _GPU_CTX_FALLBACK
            for min_vram, mm_g, rz_g in _GPU_CTX_GOALS:
                if total_vram_mb >= min_vram:
                    mm_goal, rz_goal = mm_g, rz_g
                    break
            mm_ctx = _autofit_context(
                "multimodal",
                multimodal_model,
                mm_goal,
                total_vram_mb,
                total_ram_mb,
                _read_gguf_meta(multimodal_model),
            )
            rz_ctx = _autofit_context(
                "reasoning",
                reasoning_model,
                rz_goal,
                total_vram_mb,
                total_ram_mb,
                _read_gguf_meta(reasoning_model),
            )
            c.execute(
                """
                INSERT INTO model_configs
                    (module, model_name, context_length, temperature, top_p, timeout, service_url, repeat_penalty)
                VALUES
                    ('multimodal', %s, %s, 0.7, 0.9, 120, 'http://flai-llamacpp:8033', 1.1),
                    ('reasoning', %s, %s, 0.7, 0.9, 120, 'http://flai-llamacpp:8033', 1.15),
                    ('embedding', 'bge-m3-Q8_0', 512, NULL, NULL, 120, 'http://flai-llamacpp:8033', NULL)
            """,
                (multimodal_model, mm_ctx, reasoning_model, rz_ctx),
            )

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
    c.execute("""
        DO $migrate$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                          WHERE table_name = 'messages' AND column_name = 'model_type') THEN
                ALTER TABLE messages ADD COLUMN model_type TEXT;
            END IF;
        END
        $migrate$
    """)

    # v11.3: rolling session summary columns (chat thread compression)
    c.execute("""
        DO $migrate$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                          WHERE table_name = 'chat_sessions' AND column_name = 'summary') THEN
                ALTER TABLE chat_sessions ADD COLUMN summary TEXT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                          WHERE table_name = 'chat_sessions' AND column_name = 'summary_upto_id') THEN
                ALTER TABLE chat_sessions ADD COLUMN summary_upto_id INTEGER;
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

    # v10.0: chat module was merged into multimodal (single chat model).
    # Drop any stray 'chat' row (including from restored pre-v10.0 backups) so
    # the codebase can never fall back to a chat-only configuration.
    c.execute("DELETE FROM model_configs WHERE module = 'chat'")

    # Context migrations — only on GPU. On CPU, the seed values (8192) are kept
    # so the lightweight models don't burn RAM with oversized KV cache.
    # v11.4: bumps are VRAM-aware — capped by the same auto-fit logic as the
    # seed so existing deployments on smaller GPUs aren't pushed to large
    # windows that would force partial offload.
    if not is_cpu:
        total_vram_mb = _gpu_total_vram_mb()
        total_ram_mb = _total_ram_mb()
        for module, goal_idx in (("multimodal", 0), ("reasoning", 1)):
            c.execute("SELECT model_name, context_length FROM model_configs WHERE module = %s", (module,))
            row = c.fetchone()
            if not row or not row["model_name"]:
                continue
            cur_ctx = row["context_length"] or 0
            goal = _GPU_CTX_FALLBACK[goal_idx]
            for min_vram, *goals in _GPU_CTX_GOALS:
                if total_vram_mb >= min_vram:
                    goal = goals[goal_idx]
                    break
            target = _autofit_context(
                module,
                row["model_name"],
                goal,
                total_vram_mb,
                total_ram_mb,
                _read_gguf_meta(row["model_name"]),
            )
            if target > cur_ctx:
                c.execute(
                    "UPDATE model_configs SET context_length = %s WHERE module = %s",
                    (target, module),
                )

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
                "SELECT * FROM model_vram_estimates WHERE module = %s ORDER BY updated_at DESC LIMIT 1",
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
                    (existing.get("measured_vram_mb") or 0) * old_weight + measured_mb * new_weight
                ) // total_weight
                c.execute(
                    """
                    UPDATE model_vram_estimates
                    SET model_name = %s, context_length = %s, n_gpu_layers = %s,
                        estimated_vram_mb = COALESCE(%s, estimated_vram_mb),
                        measured_vram_mb = %s,
                        measurement_count = %s,
                        last_measured_at = NOW(),
                        updated_at = NOW()
                    WHERE module = %s AND model_name = %s
                """,
                    (model_name, context_length, n_gpu_layers, estimated_mb, avg_mb, new_count, module, model_name),
                )
            else:
                c.execute(
                    """
                    UPDATE model_vram_estimates
                    SET model_name = %s, context_length = %s, n_gpu_layers = %s,
                        estimated_vram_mb = COALESCE(%s, estimated_vram_mb),
                        updated_at = NOW()
                    WHERE module = %s AND model_name = %s
                """,
                    (model_name, context_length, n_gpu_layers, estimated_mb, module, model_name),
                )
        else:
            c.execute(
                """
                INSERT INTO model_vram_estimates
                    (module, model_name, context_length, n_gpu_layers,
                     estimated_vram_mb, measured_vram_mb,
                     measurement_count, last_measured_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            """,
                (
                    module,
                    model_name,
                    context_length,
                    n_gpu_layers,
                    estimated_mb,
                    measured_mb,
                    1 if measured_mb is not None else 0,
                ),
            )
        conn.commit()
