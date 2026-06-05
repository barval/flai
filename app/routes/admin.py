# app/routes/admin.py
import json
import logging
import os
import sqlite3
from functools import wraps

import requests
from flask import Blueprint, current_app, jsonify, render_template, request, session
from flask_babel import gettext as _

from app.database import get_db
from app.model_config import get_model_config
from app.userdb import create_user, delete_user, get_user_by_login, list_users, update_password, update_user
from app.validators import ValidationError, validate_model_config_update, validate_user_input

bp = Blueprint("admin", __name__, url_prefix="/admin")
logger = logging.getLogger(__name__)


def get_file_size_bytes(path: str) -> int:
    """Get file size in bytes."""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def get_folder_size_bytes(folder_path: str) -> int:
    """Get total size of all files in a folder recursively."""
    total_size = 0
    if not os.path.exists(folder_path):
        return 0
    for dirpath, _dirnames, filenames in os.walk(folder_path):
        for filename in filenames:
            file_path = os.path.join(dirpath, filename)
            try:
                total_size += os.path.getsize(file_path)
            except OSError:
                continue
    return total_size


def admin_required(f):
    """Decorator to require admin privileges."""

    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            return jsonify({"error": _("Forbidden")}), 403
        return f(*args, **kwargs)

    return decorated


@bp.route("/")
@admin_required
def admin_panel():
    """Render admin panel with database sizes."""
    rooms = {}
    if "cam" in current_app.modules and current_app.modules["cam"].available:
        rooms = current_app.modules["cam"].get_all_rooms()

    # PostgreSQL size is tracked on the server, not accessible from app container
    user_db_size = 0
    uploads_folder = current_app.config.get("UPLOAD_FOLDER", "data/uploads")
    files_db_size = get_folder_size_bytes(uploads_folder)
    documents_folder = current_app.config.get("DOCUMENTS_FOLDER", "data/documents")
    documents_db_size = get_folder_size_bytes(documents_folder)

    # Get chunk configuration from RAG module
    chunk_size = 500
    chunk_overlap = 50
    chunk_strategy = "fixed"
    rag_top_k = 20
    max_top_k = 100
    rag_threshold_default = 0.3
    rag_threshold_reasoning = 0.3
    if "rag" in current_app.modules and current_app.modules["rag"]:
        rag = current_app.modules["rag"]
        chunk_size = rag.chunk_size
        chunk_overlap = rag.chunk_overlap
        chunk_strategy = rag.chunk_strategy
        rag_top_k = rag.top_k

    # Calculate max_top_k: 30% of reasoning model context / chunk_size (in tokens)
    # chunk_size is in characters, need to convert to tokens (TOKEN_CHARS ~3 chars/token)
    reasoning_config = get_model_config("reasoning")
    if reasoning_config:
        ctx_length = reasoning_config.get("context_length", 8192)
        max_context_tokens = int(ctx_length * 0.30)
        token_chars = current_app.config.get("TOKEN_CHARS", 3)
        chunk_size_tokens = chunk_size / token_chars
        max_top_k = max(1, int(max_context_tokens / chunk_size_tokens))

    # Clamp displayed rag_top_k to max allowed
    if rag_top_k > max_top_k:
        rag_top_k = max_top_k

    # Get RAG thresholds from config
    rag_threshold_default = current_app.config.get("RAG_RELEVANCE_THRESHOLD_DEFAULT", 0.3)
    rag_threshold_reasoning = current_app.config.get("RAG_RELEVANCE_THRESHOLD_REASONING", 0.3)

    backend_type = current_app.config.get("LLAMACP_BACKEND", "llamacpp")
    llama_swap_url = current_app.config.get("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")

    return render_template(
        "admin.html",
        rooms=rooms,
        chat_db_size=0,
        user_db_size=user_db_size,
        files_db_size=files_db_size,
        documents_db_size=documents_db_size,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        chunk_strategy=chunk_strategy,
        rag_top_k=rag_top_k,
        max_top_k=max_top_k,
        rag_threshold_default=rag_threshold_default,
        rag_threshold_reasoning=rag_threshold_reasoning,
        backend_type=backend_type,
        llama_swap_url=llama_swap_url,
    )


@bp.route("/api/users", methods=["GET"])
@admin_required
def get_users():
    """Get list of all users with stats.
    Optimized to avoid N+1 queries by using a single JOIN query.
    """
    try:
        users = list_users(exclude_admin=True)
        result = []

        # Build a single optimized query with all stats using JOINs
        with get_db() as conn:
            for u in users:
                # Single query with subqueries for all stats - no N+1
                c = conn.cursor()
                c.execute(
                    """
                    SELECT
                        COUNT(DISTINCT cs.id) as sessions,
                        COUNT(m.id) as messages,
                        (SELECT COUNT(*) FROM documents
                         WHERE user_id = %s AND file_ext IN ('.pdf', '.doc', '.docx', '.txt')) as documents_count,
                        (SELECT COUNT(DISTINCT m2.file_path)
                         FROM messages m2
                         JOIN chat_sessions cs2 ON m2.session_id = cs2.id
                         WHERE cs2.user_id = %s AND m2.file_path IS NOT NULL AND m2.file_path != '') as files_count
                    FROM chat_sessions cs
                    LEFT JOIN messages m ON cs.id = m.session_id
                    WHERE cs.user_id = %s
                """,
                    (u["login"], u["login"], u["login"]),
                )
                stats = c.fetchone()

                u_dict = dict(u)
                u_dict["sessions_count"] = stats["sessions"] if stats else 0
                u_dict["messages_count"] = stats["messages"] if stats else 0
                u_dict["files_count"] = stats["files_count"] if stats else 0
                u_dict["documents_count"] = stats["documents_count"] if stats else 0

                slm_db = os.path.join("/app/data/slm", u["login"], ".superlocalmemory", "memory.db")
                if os.path.exists(slm_db):
                    try:
                        slm_conn = sqlite3.connect(f"file:{slm_db}?mode=ro&immutable=1", uri=True)
                        slm_c = slm_conn.cursor()
                        slm_c.execute("SELECT COUNT(*) FROM memories")
                        u_dict["slm_facts_count"] = slm_c.fetchone()[0]
                        slm_conn.close()
                    except Exception:
                        u_dict["slm_facts_count"] = 0
                else:
                    u_dict["slm_facts_count"] = 0

                if u_dict["camera_permissions"]:
                    try:
                        u_dict["camera_permissions"] = json.loads(u_dict["camera_permissions"])
                    except json.JSONDecodeError:
                        u_dict["camera_permissions"] = []
                else:
                    u_dict["camera_permissions"] = []
                result.append(u_dict)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in get_users: {str(e)}", exc_info=True)
        return jsonify({"error": _("Internal server error")}), 500


@bp.route("/api/users", methods=["POST"])
@admin_required
def add_user():
    """Create a new user."""
    try:
        data = request.get_json()
        try:
            data = validate_user_input(data)
        except ValidationError as e:
            return jsonify({"error": _("Error") + ": " + str(e)}), 400

        login = data.get("login")
        password = data.get("password")
        name = data.get("name")
        service_class = data.get("service_class", 2)
        is_active = data.get("is_active", True)
        camera_permissions = data.get("camera_permissions")

        if not login or not password or not name:
            return jsonify({"error": _("Missing fields")}), 400
        if get_user_by_login(login):
            return jsonify({"error": _("Login already exists")}), 400

        create_user(
            login=login,
            password=password,
            name=name,
            service_class=service_class,
            is_admin=False,
            camera_permissions=camera_permissions,
        )
        if not is_active:
            update_user(login, is_active=False)
        return jsonify({"status": "ok"})
    except ValidationError:
        raise
    except Exception as e:
        logger.error(f"Error in add_user: {str(e)}", exc_info=True)
        return jsonify({"error": _("Internal server error")}), 500


@bp.route("/api/users/<login>", methods=["PUT"])
@admin_required
def update_user_data(login):
    """Update user data."""
    try:
        data = request.get_json()
        name = data.get("name")
        service_class = data.get("service_class")
        is_active = data.get("is_active")
        camera_permissions = data.get("camera_permissions")

        update_user(
            login=login,
            name=name,
            service_class=service_class,
            is_active=is_active,
            camera_permissions=camera_permissions,
        )
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Error in update_user_data for {login}: {str(e)}", exc_info=True)
        return jsonify({"error": _("Internal server error")}), 500


@bp.route("/api/users/<login>/password", methods=["PUT"])
@admin_required
def change_password(login):
    """Change user password."""
    try:
        data = request.get_json()
        new_password = data.get("new_password")
        if not new_password:
            return jsonify({"error": _("New password not specified")}), 400
        update_password(login, new_password)
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Error in change_password for {login}: {str(e)}", exc_info=True)
        return jsonify({"error": _("Internal server error")}), 500


@bp.route("/api/users/<login>", methods=["DELETE"])
@admin_required
def delete_user_account(login):
    """Delete a user account."""
    try:
        delete_user(login)
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Error in delete_user_account for {login}: {str(e)}", exc_info=True)
        return jsonify({"error": _("Internal server error")}), 500


@bp.route("/api/stats")
@admin_required
def get_stats():
    """Return current sizes of databases and folders in bytes."""
    try:
        chat_db_size = 0
        user_db_size = 0
        uploads_folder = current_app.config.get("UPLOAD_FOLDER", "data/uploads")
        files_db_size = get_folder_size_bytes(uploads_folder)
        documents_folder = current_app.config.get("DOCUMENTS_FOLDER", "data/documents")
        documents_db_size = get_folder_size_bytes(documents_folder)

        return jsonify(
            {
                "chat_db_size": chat_db_size,
                "user_db_size": user_db_size,
                "files_db_size": files_db_size,
                "documents_db_size": documents_db_size,
            }
        )
    except Exception as e:
        logger.error(f"Error in get_stats: {str(e)}", exc_info=True)
        return jsonify({"error": _("Internal server error")}), 500


@bp.route("/api/hardware")
def get_hardware():
    """Return hardware information for memory estimation.

    Gets GPU info via NVML (NVIDIA Management Library).
    """
    try:
        hw = {
            "gpu_name": None,
            "cuda_detected": False,
            "total_vram_mb": 0,
            "available_vram_mb": 0,
            "total_ram_mb": 0,
            "available_ram_mb": 0,
        }

        from app.resource_manager import get_resource_manager

        rm = get_resource_manager()
        rm_hw = rm.get_status()
        hw["total_ram_mb"] = rm_hw.get("total_ram_mb", 0)
        hw["available_ram_mb"] = rm_hw.get("available_ram_mb", 0)

        try:
            import pynvml

            pynvml.nvmlInit()
            count = pynvml.nvmlDeviceGetCount()
            if count > 0:
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                name = pynvml.nvmlDeviceGetName(handle)
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                hw["gpu_name"] = name.decode() if isinstance(name, bytes) else name
                hw["cuda_detected"] = True
                hw["total_vram_mb"] = mem.total // (1024 * 1024)
                hw["available_vram_mb"] = mem.free // (1024 * 1024)
                logger.info(
                    f"GPU via NVML: {hw['gpu_name']}, {hw['total_vram_mb']}MB, {hw['available_vram_mb']}MB free"
                )
            else:
                logger.warning("NVML: no GPU devices found")
            pynvml.nvmlShutdown()
        except Exception as e:
            logger.warning(f"NVML GPU detection failed: {e}")

        return jsonify(hw)
    except Exception as e:
        logger.error(f"Error in get_hardware: {str(e)}", exc_info=True)
        return jsonify({"error": _("Internal server error")}), 500


def _find_gguf_path(name: str, models_dir: str = "/models") -> str | None:
    """Find a GGUF file by name (with or without .gguf suffix), searching
    the models directory and any subdirectories."""
    if not name.endswith(".gguf"):
        name = name + ".gguf"
    full = os.path.join(models_dir, name)
    if os.path.exists(full):
        return full
    for root, _dirs, files in os.walk(models_dir):
        for f in files:
            if f == name:
                return os.path.join(root, f)
    return None


def _get_actual_vram_mb() -> tuple[int | None, int | None]:
    """Return (used_vram_mb, total_vram_mb) from nvidia-smi, or (None, None)."""
    try:
        import subprocess

        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(",")
            return int(parts[0].strip()), int(parts[1].strip())
    except Exception:
        pass
    return None, None


def _estimate_model_vram(
    file_size_mb: float,
    block_count: int,
    ngl: int,
    expert_count: int = 0,
    ctx_size: int = 8192,
    cache_type: str = "q4_0",
) -> dict:
    """Estimate VRAM usage for a model with given parameters.

    Returns dict with model_vram_mb, kv_cache_mb, compute_mb, total_mb, ngl.
    """
    # Model weights on GPU (layers * per-layer estimate)
    # For MoE: experts stay on CPU, ~95% of per-layer weight is dense (attention + FFN gate/up/down)
    moe_factor = 0.95 if expert_count > 0 else 1.0
    ratio = min(1.0, ngl / block_count) if block_count > 0 else 1.0
    model_vram = file_size_mb * ratio * moe_factor

    # KV cache estimate — calibrated against empirical measurements
    # Per-token KV cache (MB) with q4_0 compression, averaged across model sizes
    if cache_type in ("q4_0", "q4_1"):
        kv_per_token_mb = 0.04
    elif cache_type in ("q8_0",):
        kv_per_token_mb = 0.08
    else:  # f16 default
        kv_per_token_mb = 0.16
    kv_cache_mb = ctx_size * kv_per_token_mb

    # Compute buffers (scratch space)
    compute_mb = 400

    total_mb = model_vram + kv_cache_mb + compute_mb

    return {
        "model_vram_mb": round(model_vram, 1),
        "kv_cache_mb": round(kv_cache_mb, 1),
        "compute_mb": compute_mb,
        "total_mb": round(total_mb, 1),
        "ngl": ngl,
        "ratio": round(ratio, 3),
        "moe_factor": moe_factor,
    }


def _get_total_ram_mb() -> int:
    """Get total system RAM in MB from /proc/meminfo (Linux) or psutil fallback."""
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


# Thresholds for tier classification (tuned for 8/12/16+ GB GPU tiers)
TIER_VRAM_GOOD_PCT = 0.85      # vram_needed / total_vram <= this => good
TIER_RAM_SAFETY_PCT = 0.70     # (file+kv) / system_ram <= this => possible
TIER_RAM_HEADROOM_MB = 2048    # 2GB OS/other reserved


def _classify_model_fit(
    model_name: str,
    context_length: int,
    file_size_mb: float | None = None,
    block_count: int | None = None,
) -> dict:
    """Classify whether a model can be loaded and in what mode.

    Three tiers:
    - good: fits fully in VRAM
    - cpu_offload: needs partial CPU offload (degrade n_gpu_layers)
    - impossible: doesn't fit even with full CPU offload (not enough RAM)

    Returns dict with tier, can_save, ngl_recommended, message, and metadata.
    """
    from app.utils import get_gguf_models_cached

    # Defaults for unknown model
    model_key = model_name.replace(".gguf", "")
    cache = get_gguf_models_cached("/models")
    cached = cache.get(model_key, {})

    # Prefer passed-in values, fall back to cache
    if file_size_mb is None:
        file_size_mb = cached.get("file_size_mb")
    if block_count is None:
        block_count = cached.get("block_count")

    arch_max_ctx = cached.get("context_length")
    ngl_total = block_count or 32

    used_vram, total_vram = _get_actual_vram_mb()
    if total_vram is None or total_vram <= 0:
        total_vram = 16311
    total_ram = _get_total_ram_mb()

    # If model is not in cache and we don't have file_size, block save
    if not file_size_mb or not block_count:
        return {
            "tier": "unknown",
            "can_save": False,
            "ngl_recommended": ngl_total,
            "ngl_total": ngl_total,
            "vram_mb": 0,
            "file_mb": 0,
            "kv_cache_mb": 0,
            "total_vram_mb": total_vram,
            "system_ram_mb": total_ram,
            "arch_max_ctx": arch_max_ctx,
            "message": _("Model metadata not found. Run 'Refresh models' first."),
        }

    file_mb = float(file_size_mb)

    # 1. Compute VRAM needed for current context (with full GPU offload)
    est = _estimate_model_vram(
        file_size_mb=file_mb,
        block_count=block_count,
        ngl=ngl_total,
        expert_count=cached.get("expert_count") or 0,
        ctx_size=max(int(context_length), 1),
    )
    vram_full_mb = int(est["total_mb"])
    kv_cache_mb = int(est["kv_cache_mb"])

    # 2. Tier classification
    vram_budget = total_vram * TIER_VRAM_GOOD_PCT
    ram_budget = (total_ram * TIER_RAM_SAFETY_PCT) - TIER_RAM_HEADROOM_MB

    if vram_full_mb <= vram_budget:
        tier = "good"
        ngl_recommended = ngl_total
        message = _("✓ Fits in VRAM: {vram} MB / {total} MB").format(
            vram=vram_full_mb, total=total_vram
        )
        can_save = True
    else:
        # Try to find the largest ngl where both VRAM and RAM fit.
        # weights_on_gpu = file × (ngl/ngl_total)
        # weights_on_ram = file × (1 - ngl/ngl_total) = file - weights_on_gpu
        # kv is in VRAM (or partially in RAM; we treat as VRAM-side for safety)
        # overhead (compute buffers) is on GPU.
        # Find ngl_max such that:
        #   weights_on_gpu + kv + overhead <= vram_budget
        #   file - weights_on_gpu + overhead <= ram_budget
        # Solving for weights_on_gpu:
        #   weights_on_gpu <= vram_budget - kv - overhead
        #   file - weights_on_gpu <= ram_budget - overhead
        #   weights_on_gpu >= file - (ram_budget - overhead)
        vram_for_weights = vram_budget - kv_cache_mb - 400
        ram_for_weights = ram_budget - 400
        max_gpu_weights = min(vram_for_weights, file_mb)
        # We need: file_mb - gpu_weights <= ram_for_weights → gpu_weights >= file_mb - ram_for_weights
        min_gpu_weights = max(0, file_mb - ram_for_weights)

        if max_gpu_weights <= min_gpu_weights:
            # No ngl value makes both sides fit
            needed = int(file_mb + kv_cache_mb)
            tier = "impossible"
            ngl_recommended = 0
            message = _(
                "✗ Model cannot be loaded. Needs {needed} MB RAM "
                "(file + KV cache), available {total} MB."
            ).format(needed=needed, total=total_ram)
            can_save = False
        else:
            # Pick a value within the feasible range. Use min for stability
            # (less GPU usage → less likely to OOM on other tasks).
            gpu_weights = max(min_gpu_weights, min(max_gpu_weights, vram_for_weights))
            ratio = gpu_weights / file_mb if file_mb else 0
            ngl_recommended = max(1, int(ngl_total * ratio))
            cpu_layers = ngl_total - ngl_recommended
            tier = "cpu_offload"
            message = _(
                "⚠ Partial CPU offload: {ngl}/{total_layers} layers on GPU, "
                "{cpu} on RAM. ~5-10× slower."
            ).format(
                ngl=ngl_recommended, total_layers=ngl_total, cpu=cpu_layers
            )
            can_save = True

    return {
        "tier": tier,
        "can_save": can_save,
        "ngl_recommended": ngl_recommended,
        "ngl_total": ngl_total,
        "vram_mb": vram_full_mb,
        "file_mb": round(file_mb, 1),
        "kv_cache_mb": kv_cache_mb,
        "total_vram_mb": total_vram,
        "system_ram_mb": total_ram,
        "arch_max_ctx": arch_max_ctx,
        "message": message,
    }


@bp.route("/api/model-estimate", methods=["GET"])
def model_vram_estimate():
    """Return VRAM/RAM estimate for a model.

    For loaded models (via llama-swap): returns actual nvidia-smi usage.
    For unloaded models: estimates from GGUF metadata + formula.
    """
    model_name = request.args.get("model", "")
    module = request.args.get("module", "chat")
    ctx_size = request.args.get("ctx_size", 8192, type=int)
    ngl_param = request.args.get("ngl", type=int)
    cache_type = request.args.get("cache_type", "q4_0")

    if not model_name:
        return jsonify({"error": _('Missing "model" parameter')}), 400

    # 1. Get hardware info
    used_vram, total_vram = _get_actual_vram_mb()
    try:
        import psutil

        total_ram = psutil.virtual_memory().total // (1024 * 1024)
        available_ram = psutil.virtual_memory().available // (1024 * 1024)
    except Exception:
        total_ram = 0
        available_ram = 0

    # 2. Get or read GGUF metadata
    from app.utils import get_gguf_models_cached

    gguf_cache = get_gguf_models_cached("/models")
    model_key = model_name.replace(".gguf", "")
    cached = gguf_cache.get(model_key, {})

    # If not in cache, try reading from file directly
    file_size_mb = cached.get("file_size_mb")
    block_count = cached.get("block_count")
    expert_count = cached.get("expert_count") or 0
    parameter_count = cached.get("parameter_count")

    if not file_size_mb or not block_count:
        gguf_path = _find_gguf_path(model_name)
        if gguf_path and os.path.exists(gguf_path):
            file_size_mb = os.path.getsize(gguf_path) / (1024 * 1024)
            try:
                import gguf

                reader = gguf.GGUFReader(gguf_path)
                arch = None
                for key in reader.fields:
                    if "." in key and not key.startswith("GGUF") and not key.startswith("general"):
                        arch = key.split(".")[0]
                        break
                if arch:
                    bc_key = f"{arch}.block_count"
                    if bc_key in reader.fields:
                        val = reader.fields[bc_key].parts[-1]
                        if hasattr(val, "tolist"):
                            arr = val.tolist()
                            if isinstance(arr, list) and len(arr) == 1:
                                val = arr[0]
                        block_count = int(val) if val is not None else None
                    ec_key = f"{arch}.expert_count"
                    if ec_key in reader.fields:
                        val = reader.fields[ec_key].parts[-1]
                        if hasattr(val, "tolist"):
                            arr = val.tolist()
                            if isinstance(arr, list) and len(arr) == 1:
                                val = arr[0]
                        expert_count = int(val) if val is not None else expert_count
            except Exception:
                pass

    # 3. Determine n_gpu_layers
    if ngl_param is not None:
        ngl = ngl_param
    else:
        try:
            from app.resource_manager import get_resource_manager

            rm = get_resource_manager()
            config = rm.compute_llamacpp_config(module)
            ngl = config.get("n_gpu_layers", -1)
            if ngl == -1 and block_count:
                ngl = block_count
        except Exception:
            ngl = block_count or 32

    # 4. Check if model is currently loaded in llama-swap
    loaded_model = None
    try:
        swap_url = os.getenv("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")
        resp = requests.get(f"{swap_url.rstrip('/')}/running", timeout=5)
        if resp.status_code == 200:
            running = resp.json().get("running", [])
            for m in running:
                if m.get("name") == module or model_name in str(m):
                    loaded_model = m
                    break
    except Exception:
        pass

    # 5. Build response
    # Compute tier classification (3-tier VRAM/RAM fit)
    tier_info = _classify_model_fit(
        model_name=model_name,
        context_length=ctx_size,
        file_size_mb=file_size_mb,
        block_count=block_count,
    )
    if loaded_model and used_vram is not None and total_vram is not None:
        # Actual VRAM usage
        response = {
            "status": "actual",
            "vram_mb": used_vram,
            "total_vram_mb": total_vram,
            "vram_percent": round(used_vram / total_vram * 100) if total_vram > 0 else 0,
            "vram_source": "actual",
            "total_ram_mb": total_ram,
            "available_ram_mb": available_ram,
            "model": loaded_model.get("name", module),
            "has_gpu": True,
            "file_size_mb": round(file_size_mb, 1) if file_size_mb else None,
            "block_count": block_count,
            "expert_count": expert_count,
            "parameter_count": parameter_count,
            "ngl": ngl,
            "tier": tier_info["tier"],
            "can_save": tier_info["can_save"],
            "ngl_recommended": tier_info["ngl_recommended"],
            "tier_message": tier_info["message"],
            "system_ram_mb": tier_info["system_ram_mb"],
            "arch_max_ctx": tier_info["arch_max_ctx"],
        }
    elif file_size_mb and block_count:
        # Estimate
        est = _estimate_model_vram(
            file_size_mb=file_size_mb,
            block_count=block_count,
            ngl=ngl,
            expert_count=expert_count,
            ctx_size=max(ctx_size, 1),
            cache_type=cache_type,
        )
        has_gpu = total_vram is not None and total_vram > 0
        vram_pct = round(est["total_mb"] / total_vram * 100) if has_gpu and total_vram > 0 else 0
        ram_pct = round(est["total_mb"] / total_ram * 100) if total_ram > 0 else 0

        # Check for measured VRAM data in model_vram_estimates
        measured_vram_mb = None
        measurement_count = 0
        measured_ctx = None
        try:
            from app.database import get_vram_estimate
            db_est = get_vram_estimate(module, model_name=model_name)
            if db_est:
                measured_vram_mb = db_est.get("measured_vram_mb")
                measurement_count = db_est.get("measurement_count") or 0
                measured_ctx = db_est.get("context_length")
        except Exception:
            pass

        # Save computed estimate to DB for future reference
        try:
            from app.database import upsert_vram_estimate
            upsert_vram_estimate(
                module=module,
                model_name=model_name,
                context_length=ctx_size,
                n_gpu_layers=ngl,
                estimated_mb=round(est["total_mb"]),
            )
        except Exception:
            pass

        response = {
            "status": "measured" if measured_vram_mb else "estimate",
            "vram_mb": round(est["total_mb"]),
            "total_vram_mb": total_vram or 0,
            "vram_percent": vram_pct,
            "vram_source": "measured" if measured_vram_mb else "estimate",
            "measured_vram_mb": measured_vram_mb,
            "measurement_count": measurement_count,
            "measured_ctx": measured_ctx,
            "ram_mb": round(est["total_mb"]),
            "total_ram_mb": total_ram,
            "ram_percent": ram_pct,
            "has_gpu": has_gpu,
            "file_size_mb": round(file_size_mb, 1),
            "block_count": block_count,
            "expert_count": expert_count,
            "parameter_count": parameter_count,
            "ngl": ngl,
            "context_length": ctx_size,
            "tier": tier_info["tier"],
            "can_save": tier_info["can_save"],
            "ngl_recommended": tier_info["ngl_recommended"],
            "tier_message": tier_info["message"],
            "system_ram_mb": tier_info["system_ram_mb"],
            "arch_max_ctx": tier_info["arch_max_ctx"],
            "details": {
                "model_vram_mb": round(est["model_vram_mb"]),
                "kv_cache_mb": round(est["kv_cache_mb"]),
                "compute_mb": est["compute_mb"],
                "moe_factor": est["moe_factor"],
            },
        }
    else:
        # Not enough data
        has_gpu = total_vram is not None and total_vram > 0
        response = {
            "status": "nodata",
            "vram_mb": 0,
            "total_vram_mb": total_vram or 0,
            "vram_source": "nodata",
            "has_gpu": has_gpu,
            "file_size_mb": round(file_size_mb, 1) if file_size_mb else None,
            "block_count": block_count,
            "expert_count": expert_count,
            "parameter_count": parameter_count,
            "tier": tier_info["tier"],
            "can_save": tier_info["can_save"],
            "ngl_recommended": tier_info["ngl_recommended"],
            "tier_message": tier_info["message"],
            "system_ram_mb": tier_info["system_ram_mb"],
            "arch_max_ctx": tier_info["arch_max_ctx"],
        }

    return jsonify(response)


# ==================== ENDPOINTS FOR MODEL MANAGEMENT ====================


@bp.route("/api/llamacpp/check", methods=["GET"])
@admin_required
def llamacpp_check():
    """Check if llama-server is reachable at given URL via /v1/models."""
    service_url = request.args.get("url")
    if not service_url:
        return jsonify({"available": False, "error": _("Missing url")}), 400
    try:
        response = requests.get(f"{service_url.rstrip('/')}/v1/models", timeout=5)
        if response.status_code == 200:
            return jsonify({"available": True})
        else:
            return jsonify({"available": False, "error": _("HTTP error {status}").format(status=response.status_code)})
    except Exception as e:
        return jsonify({"available": False, "error": str(e)})


@bp.route("/api/llamacpp/models", methods=["GET"])
def llamacpp_models():
    """Return list of available models - either from llama-server or from models directory.

    Note: This endpoint is intentionally public as it only lists available model files.
    """
    service_url = request.args.get("url")
    backend_type = request.args.get("backend", "llamacpp")
    list_type = request.args.get("list_type", "all")

    # If using llama-swap, get models from there instead
    if backend_type == "llama-swap" or (service_url and "llamaswap" in service_url):
        service_url = "http://flai-llamaswap:8080"

    # If listing actual GGUF files from models directory
    if list_type == "gguf_files":
        import os

        models_dir = "/models"
        gguf_files = []
        seen_bases = set()
        try:
            for root, _dirs, files in os.walk(models_dir):
                for f in files:
                    if f.endswith(".gguf"):
                        # Skip mmproj files - these are auxiliary files for multimodal models
                        if "mmproj" in f.lower():
                            continue
                        # Get display name - just the filename, not the full path
                        display_name = f
                        if root != models_dir:
                            # Model in subdirectory - use just the gguf filename
                            display_name = f
                        if display_name not in seen_bases:
                            gguf_files.append(display_name)
                            seen_bases.add(display_name)
        except Exception as e:
            current_app.logger.warning(f"Error reading models directory: {e}")
        return jsonify(gguf_files)

    if not service_url:
        return jsonify({"error": _('Missing "url" parameter')}), 400
    try:
        resp = requests.get(f"{service_url.rstrip('/')}/v1/models", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            # OpenAI format: {"data": [{"id": "model1", ...}, ...]}
            all_items = [m["id"] for m in data.get("data", [])]
            # For llama-swap: include all model IDs (chat, embedding, etc.)
            # For direct llama-server: filter for .gguf files
            if backend_type == "llama-swap":
                models = all_items  # All model IDs are valid
            else:
                exclude_keys = {
                    "chat",
                    "embedding",
                    "multimodal",
                    "reasoning",
                    "chatgguf",
                    "embeddinggguf",
                    "multimodalgguf",
                    "reasoninggguf",
                }
                models = [
                    m
                    for m in all_items
                    if m.lower() not in exclude_keys and (".gguf" in m.lower() or any(c.isdigit() for c in m))
                ]
            return jsonify(models)
        else:
            return jsonify({"error": _("llama-server returned {status}").format(status=resp.status_code)}), 500
    except Exception as e:
        current_app.logger.error(f"Error fetching llama.cpp models from {service_url}: {e}")
        return jsonify({"error": _("Error") + ": " + str(e)}), 500


@bp.route("/api/llamacpp/model/<path:name>", methods=["GET"])
def llamacpp_model_info(name):
    """Return information about a specific model from llama-server.
    For llama-swap: returns basic info based on model type.
    For direct llama-server: reads context length from GGUF metadata.
    """
    service_url = request.args.get("url")
    backend = request.args.get("backend", "llamacpp")
    use_gguf = request.args.get("gguf", "true").lower() == "true"

    # Use llama-swap URL if configured
    if backend == "llama-swap" or "llamaswap" in (service_url or ""):
        service_url = "http://flai-llamaswap:8080"

    # For GGUF files - use cached metadata first (instant), fallback to reading
    if name.endswith(".gguf"):
        import os
        import re

        models_dir = "/models"

        # Try cached metadata first (instant)
        from app.utils import get_gguf_models_cached

        gguf_cache = get_gguf_models_cached(models_dir)

        model_key = name.replace(".gguf", "")
        cached = gguf_cache.get(model_key, {})

        # If not in cache, try to find and read the file
        gguf_path = os.path.join(models_dir, name)
        if not os.path.exists(gguf_path):
            # Try to find in subdirectories - the file might be in a subfolder
            base_name = name.replace(".gguf", "")
            for root, _dirs, files in os.walk(models_dir):
                for f in files:
                    if f.endswith(".gguf") and f.replace(".gguf", "") == base_name:
                        gguf_path = os.path.join(root, f)
                        break
                if os.path.exists(gguf_path):
                    break

        default_ctx = "32768"
        file_size_mb = 0

        # Get file size
        if os.path.exists(gguf_path):
            file_size_mb = os.path.getsize(gguf_path) // (1024 * 1024)

        # Try to get context from llama-swap.yaml config
        try:
            config_path = "/config/llama-swap.yaml"
            if os.path.exists(config_path):
                with open(config_path) as f:
                    config_content = f.read()
                    # Find model in config and extract --ctx-size
                    model_match = re.search(rf'{re.escape(name)}["\']?\s*--ctx-size\s+(\d+)', config_content)
                    if model_match:
                        default_ctx = model_match.group(1)
        except Exception as e:
            logger.warning(f"Could not read llama-swap config: {e}")

        # Use cached metadata if available (skip slow file reading)
        if cached:
            # Check model type by name FIRST (more reliable)
            model_type = "chat"
            display_arch = "Chat"
            name_lower = name.lower()

            # Check by name patterns
            if any(a in name_lower for a in ["embed", "bge", "gte", "e5", "bert", "embedding"]):
                model_type = "embedding"
                display_arch = "Embedding"
            elif any(a in name_lower for a in ["vl", "vision", "mmproj", "qwen3v", "qwen2_vl", "multimodal"]):
                model_type = "multimodal"
                display_arch = "Vision"
            elif any(a in name_lower for a in ["gpt-oss", "mxfp4", "qwq", "r1", "reasoning", "deepseek", "think"]):
                model_type = "reasoning"
                display_arch = "Reasoning"
            # fallback to cached value if not detected by name
            elif cached.get("embedding_length") and not cached.get("context_length"):
                model_type = "embedding"
                display_arch = "Embedding"

            from app.utils import estimate_parameters_from_filename, extract_quantization

            return jsonify(
                {
                    "architecture": display_arch,
                    "model_type": model_type,
                    "parameters": estimate_parameters_from_filename(name),
                    "quantization": extract_quantization(name),
                    "context_length": cached.get("context_length") or default_ctx,
                    "file_size_mb": cached.get("file_size_mb") or file_size_mb,
                    "embedding_length": cached.get("embedding_length"),
                    "context_source": "cache",
                    "model_path": name,
                    "gguf_architecture": cached.get("architecture"),
                    "parameter_count": cached.get("parameter_count"),
                    "block_count": cached.get("block_count"),
                    "expert_count": cached.get("expert_count"),
                    "head_count": cached.get("head_count"),
                    "head_count_kv": cached.get("head_count_kv"),
                    "key_length": cached.get("key_length"),
                    "value_length": cached.get("value_length"),
                }
            )

        if os.path.exists(gguf_path):
            from app.utils import estimate_parameters_from_filename, extract_quantization

            # Determine model type from name
            is_embedding = "embed" in name.lower() or "bge" in name.lower()
            is_vision = "vl" in name.lower() or "qwen3v" in name.lower()

            # Try to read GGUF metadata for better classification
            gguf_meta = {}
            try:
                import gguf

                reader = gguf.GGUFReader(gguf_path)

                # Determine architecture from field key prefixes
                arch = None
                for key in reader.fields:
                    # Architecture-specific keys: <arch>.<something>
                    if "." in key and not key.startswith("GGUF") and not key.startswith("general"):
                        parts = key.split(".")
                        if len(parts) >= 2:
                            potential_arch = parts[0]
                            # Skip if it's not a known model architecture prefix
                            if potential_arch not in ("gguf", "clip", "tokenizer", "llava"):
                                arch = potential_arch
                                break

                # Check for vision-related keys
                has_vision = False
                for key in reader.fields:
                    if key.startswith("clip.vision") or key.startswith("mmproj"):
                        has_vision = True
                        break
                    if key.startswith("llava."):
                        has_vision = True
                        break

                gguf_meta = {"architecture": arch, "has_vision": has_vision}
                if arch:
                    bc_key = f"{arch}.block_count"
                    if bc_key in reader.fields:
                        val = reader.fields[bc_key].parts[-1]
                        if hasattr(val, "tolist"):
                            arr = val.tolist()
                            if isinstance(arr, list) and len(arr) == 1:
                                val = arr[0]
                        gguf_meta["block_count"] = int(val) if val is not None else None
                    ec_key = f"{arch}.expert_count"
                    if ec_key in reader.fields:
                        val = reader.fields[ec_key].parts[-1]
                        if hasattr(val, "tolist"):
                            arr = val.tolist()
                            if isinstance(arr, list) and len(arr) == 1:
                                val = arr[0]
                        gguf_meta["expert_count"] = int(val) if val is not None else None
            except Exception as e:
                logger.debug(f"Could not parse GGUF metadata: {e}")

            # Classify based on metadata
            arch = (gguf_meta.get("architecture") or name).lower()

            # Known architecture types
            embedding_archs = {"bert", "nomic-bert", "bge", "gte", "e5", "stella", "jina", "snowflake", "nemo"}
            multimodal_archs = {
                "vision",
                "vl",
                "llava",
                "minicpmv",
                "mllama",
                "internvl",
                "phi3-vision",
                "qwen2_vl",
                "qwen_vl",
                "qwen2.5_vl",
                "glm4_v",
                "idefics",
                "paligemma",
                "siglip",
                "qwen3vl",
            }

            model_type = "chat"
            display_arch = "LLM"

            if any(a in arch for a in embedding_archs) or is_embedding or "bert" in arch:
                model_type = "embedding"
                display_arch = "Embedding"
            elif any(a in arch for a in multimodal_archs) or is_vision or gguf_meta.get("has_vision"):
                model_type = "multimodal"
                display_arch = "Vision"
            else:
                # Check for reasoning models by name
                name_lower = name.lower()
                # Extended reasoning patterns
                reasoning_patterns = [
                    "qwq",
                    "deepseek-r1",
                    "reasoning",
                    "thinking",
                    "open-thoughts",
                    "r1",
                    "train",
                    "gpt-oss",
                    "mxfp4",
                    "moe",
                    "reasoner",
                    "o1",
                    "o3",
                    "deepseek",
                ]
                if any(h in name_lower for h in reasoning_patterns):
                    model_type = "reasoning"
                    display_arch = "Reasoning"
                else:
                    display_arch = "Chat"

            # Override with base name check for specific cases
            name_lower = name.lower()
            if "gpt-oss" in name_lower:
                model_type = "reasoning"
                display_arch = "Reasoning"

            return jsonify(
                {
                    "architecture": display_arch,
                    "model_type": model_type,
                    "parameters": estimate_parameters_from_filename(name),
                    "quantization": extract_quantization(name),
                    "context_length": cached.get("context_length") or default_ctx,
                    "file_size_mb": cached.get("file_size_mb") or file_size_mb,
                    "embedding_length": cached.get("embedding_length")
                    or ("1024" if model_type == "embedding" else None),
                    "context_source": "cache" if cached else "gguf_file",
                    "model_path": name,
                    "gguf_architecture": gguf_meta.get("architecture"),
                    "parameter_count": cached.get("parameter_count"),
                    "block_count": cached.get("block_count") or gguf_meta.get("block_count"),
                    "expert_count": cached.get("expert_count") or gguf_meta.get("expert_count"),
                    "head_count": cached.get("head_count"),
                    "head_count_kv": cached.get("head_count_kv"),
                    "key_length": cached.get("key_length"),
                    "value_length": cached.get("value_length"),
                }
            )

    if not service_url:
        return jsonify({"error": _('Missing "url" parameter')}), 400

    # For llama-swap functional IDs, return basic info
    if backend == "llama-swap" or "llamaswap" in (service_url or ""):
        is_embedding = name in ["embedding", "bge-m3"]
        is_vision = name in ["multimodal", "vision"]

        model_info = {
            "architecture": "llama-swap model" if not is_vision else "vision model",
            "parameters": "N/A",
            "quantization": "N/A",
            "context_length": "32768" if not is_embedding else "8192",
            "embedding_length": "1024" if is_embedding else None,
            "context_source": "llama-swap",
        }
        return jsonify(model_info)

    try:
        resp = requests.get(f"{service_url.rstrip('/')}/v1/models", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            model_data = None
            for m in data.get("data", []):
                if m.get("id") == name:
                    model_data = m
                    break

            from app.utils import extract_quantization

            quantization = extract_quantization(name)

            is_embedding = "embed" in name.lower() or "bge" in name.lower()
            is_vision = "vl" in name.lower() or "vision" in name.lower()

            known_models = {
                "Qwen3-4B-Instruct-2507-Q4_K_M": {"arch": "qwen3", "params": "~4B", "ctx": 32768, "emb": 2560},
                "gemma-4-26B-A4B-it-MXFP4_MOE": {"arch": "gemma", "params": "~26B (MoE)", "ctx": 32768, "emb": 4608},
                "gpt-oss-20b-Q4_K_M": {"arch": "gpt-oss", "params": "~20B", "ctx": 32768, "emb": 5120},
                "Qwen3VL-8B-Instruct-Q4_K_M": {"arch": "qwen3-vl", "params": "~8B", "ctx": 32768, "emb": 4096},
                "bge-m3-Q8_0": {"arch": "bge", "params": "~567M", "ctx": 8192, "emb": 1024},
            }

            known = known_models.get(name, {})

            if not known.get("arch"):
                name_lower = name.lower()
                if "qwen3" in name_lower and "vl" in name_lower:
                    arch = "qwen3-vl"
                elif "qwen3" in name_lower:
                    arch = "qwen3"
                elif "qwen2.5" in name_lower or "qwen2" in name_lower:
                    arch = "qwen2.5"
                elif "gemma" in name_lower:
                    arch = "gemma"
                elif "gpt-oss" in name_lower:
                    arch = "gpt-oss"
                elif "bge" in name_lower:
                    arch = "bge"
                elif "llama" in name_lower:
                    arch = "llama"
                elif "mistral" in name_lower:
                    arch = "mistral"
                else:
                    arch = "N/A"
            else:
                arch = known["arch"]

            if known.get("params"):
                params = known["params"]
            else:
                params = "N/A"
                for hint, label in [
                    ("70b", "~70B"),
                    ("32b", "~32B"),
                    ("27b", "~27B"),
                    ("20b", "~20B"),
                    ("26b", "~26B (MoE)"),
                    ("a4b", "~26B (MoE)"),
                    ("14b", "~14B"),
                    ("12b", "~12B"),
                    ("9b", "~9B"),
                    ("8b", "~8B"),
                    ("7b", "~7B"),
                    ("4b", "~4B"),
                    ("3b", "~3B"),
                    ("1b", "~1B"),
                ]:
                    if hint in name.lower():
                        params = label
                        break

            ctx_length = None
            emb_length = None
            ctx_source = "unknown"
            emb_source = "unknown"

            if use_gguf:
                gguf_info = _get_gguf_metadata(name, service_url)
                if gguf_info.get("context_length"):
                    ctx_length = gguf_info["context_length"]
                    ctx_source = "gguf"
                if gguf_info.get("embedding_length"):
                    emb_length = gguf_info["embedding_length"]
                    emb_source = "gguf"

            if not ctx_length:
                if known.get("ctx"):
                    ctx_length = known["ctx"]
                    ctx_source = "known"
                else:
                    ctx_length = "N/A"
                    ctx_source = "none"

            if not emb_length:
                if known.get("emb"):
                    emb_length = known["emb"]
                    emb_source = "known"
                else:
                    emb_length = "N/A"
                    emb_source = "none"

            status = "unknown"
            if model_data:
                status = model_data.get("status", {}).get("value", "unknown")

            return jsonify(
                {
                    "id": name,
                    "architecture": arch,
                    "parameters": params,
                    "quantization": quantization,
                    "context_length": ctx_length,
                    "embedding_length": emb_length,
                    "context_source": ctx_source,
                    "embedding_source": emb_source,
                    "status": status,
                    "type": "embedding" if is_embedding else ("vision" if is_vision else "text"),
                    "block_count": gguf_info.get("block_count"),
                    "file_size_mb": gguf_info.get("file_size_mb"),
                }
            )
        else:
            return jsonify({"error": _("llama-server returned {status}").format(status=resp.status_code)}), 500
    except Exception as e:
        current_app.logger.error(f"Error fetching llama.cpp model info for {name}: {e}")
        return jsonify({"error": _("Error") + ": " + str(e)}), 500


def _get_gguf_metadata(model_name: str, service_url: str) -> dict:
    """Get cached GGUF metadata for a model.

    Args:
        model_name: Name of the model file
        service_url: URL of llama.cpp service (unused, kept for compatibility)

    Returns:
        Dict with context_length, embedding_length, etc.
    """
    info = {}

    try:
        from app.utils import get_gguf_models_cached

        models_dir = "/models"
        gguf_cache = get_gguf_models_cached(models_dir)

        model_key = model_name
        if model_key.endswith(".gguf"):
            model_key = model_key[:-5]

        if model_key in gguf_cache:
            cached = gguf_cache[model_key]
            if cached.get("context_length"):
                info["context_length"] = cached["context_length"]
            if cached.get("embedding_length"):
                info["embedding_length"] = cached["embedding_length"]
            if cached.get("architecture"):
                info["architecture"] = cached["architecture"]
            if cached.get("block_count"):
                info["block_count"] = cached["block_count"]
            if cached.get("file_size_mb"):
                info["file_size_mb"] = cached["file_size_mb"]
            if cached.get("parameter_count"):
                info["parameter_count"] = cached["parameter_count"]
            if cached.get("size_label"):
                info["parameters"] = cached["size_label"]
            current_app.logger.debug(f"GGUF metadata from cache: {info}")
        else:
            current_app.logger.debug(f"Model not in GGUF cache: {model_key}")

    except ImportError:
        current_app.logger.debug("gguf library not installed")
    except Exception as e:
        current_app.logger.warning(f"Error reading GGUF metadata: {e}")

    return info


@bp.route("/api/model_configs", methods=["GET"])
def get_model_configs():
    """Return all model configurations from the database."""
    from app.model_config import reload_all_model_configs

    configs = reload_all_model_configs()
    return jsonify(configs)


@bp.route("/api/model_configs/<module>", methods=["PUT"])
@admin_required
def update_model_config(module):
    """Update configuration for a specific module."""
    from app.database import get_db
    from app.model_config import get_model_config, invalidate_model_config_cache

    data = request.get_json()
    try:
        updates = validate_model_config_update(data, module)
    except ValidationError as e:
        return jsonify({"error": _("Error") + ": " + str(e)}), 400

    # ── Server-side VRAM/RAM/ctx validation (defense in depth) ──
    new_model_name = updates.get("model_name")
    new_ctx = updates.get("context_length")
    if new_model_name and module != "embedding":
        # Tier classification blocks "impossible" models (file + KV > 70% RAM)
        # Default ctx from current config if not being updated
        if new_ctx is None:
            existing_cfg = get_model_config(module) or {}
            new_ctx = existing_cfg.get("context_length", 8192)
        tier_info = _classify_model_fit(
            model_name=new_model_name,
            context_length=int(new_ctx),
        )
        if not tier_info["can_save"]:
            return jsonify({
                "error": tier_info["message"],
                "tier": tier_info["tier"],
                "details": tier_info,
            }), 400

    old_model = None
    if module == "embedding":
        old_config = get_model_config("embedding")
        old_model = old_config.get("model_name") if old_config else None
        current_app.logger.info(f"Embedding old_model from config: '{old_model}'")

    with get_db() as conn:
        c = conn.cursor()
        set_clause = ", ".join([f"{k} = %s" for k in updates])
        values = list(updates.values()) + [module]
        c.execute(
            f"""
            UPDATE model_configs
            SET {set_clause}, updated_at = CURRENT_TIMESTAMP
            WHERE module = %s
        """,
            values,
        )
        conn.commit()

    # Invalidate cache for updated module
    invalidate_model_config_cache(module)

    result = {"status": "ok"}
    if module == "embedding":
        new_model = updates.get("model_name")
        current_app.logger.info(f"Embedding new_model: '{new_model}', old_model: '{old_model}'")
        # Only trigger reindex if the model actually CHANGED
        if new_model and old_model is not None and new_model != old_model:
            current_app.logger.info(
                f"Embedding model changed from '{old_model}' to '{new_model}', starting reindex all"
            )
            current_app.request_queue.add_reindex_all_task(lang="ru")
            result["model_name"] = new_model
            result["reindex_triggered"] = True
        elif new_model == old_model:
            current_app.logger.info(f"Embedding model '{new_model}' saved but unchanged — skipping reindex")
            result["model_name"] = new_model
            result["reindex_triggered"] = False
        else:
            result["model_name"] = new_model or old_model
            result["reindex_triggered"] = False
            current_app.logger.info(f"Embedding model save: new='{new_model}', old='{old_model}' — no reindex")
    else:
        result["model_name"] = updates.get("model_name")

    if module == "reasoning":
        reasoning_config = get_model_config("reasoning")
        if reasoning_config:
            ctx_length = reasoning_config.get("context_length", 8192)
            chunk_config = get_model_config("chunks")
            chunk_size = chunk_config.get("chunk_size", 500) if chunk_config else 500
            token_chars = current_app.config.get("TOKEN_CHARS", 3)
            max_context_tokens = int(ctx_length * 0.30)
            chunk_size_tokens = chunk_size / token_chars
            result["max_top_k"] = max(1, int(max_context_tokens / chunk_size_tokens))

    backend_type = current_app.config.get("LLAMACP_BACKEND")
    if backend_type == "llama-swap":
        try:
            from app.llama_swap_config import LlamaSwapConfigGenerator, generate_and_write

            if generate_and_write(current_app):
                generator = LlamaSwapConfigGenerator(current_app)
                generator.signal_reload()
                current_app.logger.info(f"llama-swap config regenerated after updating {module}")
                result["llama_swap_updated"] = True
            else:
                current_app.logger.warning(f"Failed to regenerate llama-swap config for {module}")
        except Exception as e:
            current_app.logger.warning(f"Error updating llama-swap config: {e}")

    # ── Schedule background dry-load + auto-rollback on failure ──
    if module != "embedding" and new_model_name:
        try:
            from app.tasks.dry_load import schedule_dry_load

            schedule_dry_load(current_app, module, new_model_name)
            result["dry_load_scheduled"] = True
        except Exception as e:
            current_app.logger.warning(f"Failed to schedule dry_load: {e}")

    return jsonify(result)


@bp.route("/api/admin/reindex-all", methods=["POST"])
@admin_required
def api_admin_reindex_all():
    """Manually trigger reindex of all documents."""
    current_app.logger.info(f"Reindex API called, is_admin={session.get('is_admin')}")
    try:
        if not hasattr(current_app, "request_queue") or not current_app.request_queue:
            return jsonify({"ok": False, "error": _("Request queue not available")}), 500
        lang = request.json.get("lang", "ru") if request.is_json else "ru"
        current_app.request_queue.add_reindex_all_task(lang=lang)
        current_app.logger.info("Manual reindex all documents triggered via admin")
        return jsonify({"ok": True, "message": _("Reindex started")})
    except Exception as e:
        current_app.logger.error(f"Error triggering reindex: {e}")
        return jsonify({"ok": False, "error": _("Error") + ": " + str(e)}), 500


@bp.route("/api/admin/chunks", methods=["PUT"])
@admin_required
def api_save_chunks_config():
    """Save chunk configuration and trigger reindex if changed."""
    try:
        from app.database import get_db

        data = request.get_json()
        new_chunk_size = data.get("chunk_size", 500)
        new_chunk_overlap = data.get("chunk_overlap", 50)
        new_chunk_strategy = data.get("chunk_strategy", "fixed")
        new_rag_top_k = data.get("rag_top_k", 20)
        new_threshold_default = data.get("rag_threshold_default", 0.3)
        new_threshold_reasoning = data.get("rag_threshold_reasoning", 0.3)

        # Clamp rag_top_k to max allowed by reasoning model context
        reasoning_config = get_model_config("reasoning")
        if reasoning_config:
            ctx_length = reasoning_config.get("context_length", 8192)
            max_context_tokens = int(ctx_length * 0.30)
            token_chars = current_app.config.get("TOKEN_CHARS", 3)
            chunk_size_tokens = new_chunk_size / token_chars
            max_top_k = max(1, int(max_context_tokens / chunk_size_tokens))
            if new_rag_top_k > max_top_k:
                new_rag_top_k = max_top_k

        # Get original config
        rag = current_app.modules.get("rag")
        if not rag:
            return jsonify({"ok": False, "error": _("RAG module unavailable")}), 500

        old_chunk_size = rag.chunk_size
        old_chunk_overlap = rag.chunk_overlap
        old_chunk_strategy = rag.chunk_strategy
        old_rag_top_k = rag.top_k

        # Get old thresholds from config
        old_threshold_default = current_app.config.get("RAG_RELEVANCE_THRESHOLD_DEFAULT", 0.3)
        old_threshold_reasoning = current_app.config.get("RAG_RELEVANCE_THRESHOLD_REASONING", 0.3)

        # Check if anything changed
        config_changed = (
            new_chunk_size != old_chunk_size
            or new_chunk_overlap != old_chunk_overlap
            or new_chunk_strategy != old_chunk_strategy
            or new_rag_top_k != old_rag_top_k
            or new_threshold_default != old_threshold_default
            or new_threshold_reasoning != old_threshold_reasoning
        )

        if config_changed:
            # Save chunk config to config table
            with get_db() as conn:
                c = conn.cursor()
                # Add top_k column if not exists
                c.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                                      WHERE table_name = 'model_configs' AND column_name = 'top_k') THEN
                            ALTER TABLE model_configs ADD COLUMN top_k INTEGER;
                        END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                                      WHERE table_name = 'model_configs' AND column_name = 'rag_threshold_default') THEN
                            ALTER TABLE model_configs ADD COLUMN rag_threshold_default FLOAT DEFAULT 0.3;
                        END IF;
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                                      WHERE table_name = 'model_configs' AND column_name = 'rag_threshold_reasoning') THEN
                            ALTER TABLE model_configs ADD COLUMN rag_threshold_reasoning FLOAT DEFAULT 0.2;
                        END IF;
                    END
                    $$
                """)
                c.execute(
                    """
                    INSERT INTO model_configs (module, chunk_size, chunk_overlap, chunk_strategy, top_k, rag_threshold_default, rag_threshold_reasoning, updated_at)
                    VALUES ('chunks', %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (module) DO UPDATE SET
                        chunk_size = EXCLUDED.chunk_size,
                        chunk_overlap = EXCLUDED.chunk_overlap,
                        chunk_strategy = EXCLUDED.chunk_strategy,
                        top_k = EXCLUDED.top_k,
                        rag_threshold_default = EXCLUDED.rag_threshold_default,
                        rag_threshold_reasoning = EXCLUDED.rag_threshold_reasoning,
                        updated_at = CURRENT_TIMESTAMP
                """,
                    (
                        new_chunk_size,
                        new_chunk_overlap,
                        new_chunk_strategy,
                        new_rag_top_k,
                        new_threshold_default,
                        new_threshold_reasoning,
                    ),
                )
                conn.commit()

            # Update RAG module values
            rag.chunk_size = new_chunk_size
            rag.chunk_overlap = new_chunk_overlap
            rag.chunk_strategy = new_chunk_strategy
            rag.top_k = new_rag_top_k

            # Update thresholds in app config
            current_app.config["RAG_RELEVANCE_THRESHOLD_DEFAULT"] = new_threshold_default
            current_app.config["RAG_RELEVANCE_THRESHOLD_REASONING"] = new_threshold_reasoning

            current_app.logger.info(
                f"Chunk config changed: size={old_chunk_size}->{new_chunk_size}, overlap={old_chunk_overlap}->{new_chunk_overlap}, strategy={old_chunk_strategy}->{new_chunk_strategy}, top_k={old_rag_top_k}->{new_rag_top_k}, threshold_default={old_threshold_default}->{new_threshold_default}, threshold_reasoning={old_threshold_reasoning}->{new_threshold_reasoning}"
            )

            # Trigger reindex only if chunking params changed
            reindex_triggered = False
            if (
                (new_chunk_size != old_chunk_size or new_chunk_strategy != old_chunk_strategy)
                and hasattr(current_app, "request_queue")
                and current_app.request_queue
            ):
                current_app.request_queue.add_reindex_all_task(lang="ru")
                current_app.logger.info("Reindex triggered due to chunk config change")
                reindex_triggered = True

            return jsonify({"ok": True, "reindex_triggered": reindex_triggered, "rag_top_k": new_rag_top_k})
        else:
            current_app.logger.info("Chunk config unchanged")
            return jsonify({"ok": True, "reindex_triggered": False, "rag_top_k": new_rag_top_k})
    except Exception as e:
        current_app.logger.error(f"Error saving chunks config: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500
