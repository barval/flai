"""VRAM/RAM fit estimation helpers shared by the admin Models tab and Model Hub.

Extracted verbatim from app/routes/admin.py (v12.2) so Model Hub can reuse the
same tier classification without importing route code. app/routes/admin.py
re-exports these names for backward compatibility — no behavior changes.
"""

import os

import requests
from flask_babel import gettext as _


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
    """Return (used_vram_mb, total_vram_mb) from platform_detect, or (None, None)."""
    try:
        from app.platform_detect import get_platform_info
        from app.resource_manager import get_resource_manager

        rm = get_resource_manager()
        info = get_platform_info(rm.hardware.platform)
        if info.total_vram_mb > 0:
            return info.used_vram_mb, info.total_vram_mb
    except Exception:
        pass
    return None, None


def _platform_has_gpu() -> bool:
    """True when a usable GPU (nonzero VRAM) is detected."""
    return _get_actual_vram_mb()[1] not in (None, 0)


def _estimate_model_vram(
    file_size_mb: float,
    block_count: int,
    ngl: int,
    expert_count: int = 0,
    ctx_size: int = 8192,
    cache_type: str = "q4_0",
    supports_mtp: bool = False,
    mmproj_size_mb: float = 0,
    kv_per_token: float | None = None,
) -> dict:
    """Estimate VRAM usage for a model with given parameters.

    Returns dict with model_vram_mb, kv_cache_mb, compute_mb, total_mb, ngl.
    """
    # Model weights on GPU (layers * per-layer estimate)
    # For MoE: experts stay on CPU, ~95% of per-layer weight is dense (attention + FFN gate/up/down)
    moe_factor = 0.95 if expert_count > 0 else 1.0
    # MTP draft prediction layers add ~15% overhead to model weights in VRAM
    mtp_factor = 1.15 if supports_mtp else 1.0
    ratio = min(1.0, ngl / block_count) if block_count > 0 else 1.0
    model_vram = file_size_mb * ratio * moe_factor * mtp_factor

    # KV cache estimate — calibrated against empirical measurements.
    # Per-token KV cache (MB) with q4_0 compression, averaged across model sizes.
    # The caller may pass the per-module calibrated value (see KV_PER_TOKEN_MB in
    # resource_manager.py); otherwise fall back to the old generic constants.
    if kv_per_token is None:
        if cache_type in ("q4_0", "q4_1"):
            kv_per_token = 0.04
        elif cache_type in ("q8_0",):
            kv_per_token = 0.08
        else:  # f16 default
            kv_per_token = 0.16
    kv_cache_mb = ctx_size * kv_per_token

    # Compute buffers (scratch space)
    compute_mb = 400

    # mmproj (vision encoder) is resident in VRAM regardless of n_gpu_layers
    total_mb = model_vram + kv_cache_mb + compute_mb + mmproj_size_mb

    return {
        "model_vram_mb": round(model_vram, 1),
        "kv_cache_mb": round(kv_cache_mb, 1),
        "compute_mb": compute_mb,
        "mmproj_mb": round(mmproj_size_mb, 1),
        "total_mb": round(total_mb, 1),
        "ngl": ngl,
        "ratio": round(ratio, 3),
        "moe_factor": moe_factor,
        "mtp_factor": mtp_factor,
    }


def _kv_per_token_for_module(module: str, cache_type: str = "q4_0") -> float:
    """Return the calibrated per-token KV cache cost (MB) for a module.

    Uses the same per-module values as resource_manager.KV_PER_TOKEN_MB so that
    the admin estimate and the runtime VRAM accounting agree.
    """
    from app.resource_manager import KV_PER_TOKEN_MB

    if module in KV_PER_TOKEN_MB:
        return KV_PER_TOKEN_MB[module]
    if cache_type in ("q4_0", "q4_1"):
        return 0.04
    if cache_type in ("q8_0",):
        return 0.08
    return 0.16


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


def _get_free_ram_mb() -> int:
    """Get currently available system RAM in MB (/proc/meminfo MemAvailable).

    Used as the offload budget for "GPU+CPU" / "CPU" fit tiers: a model only
    counts as launchable in mixed mode when its non-GPU resident portion fits
    into the RAM that is actually free right now.
    """
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    try:
        import psutil

        avail = psutil.virtual_memory().available  # type: ignore[attr-defined]
        return int(avail) // (1024 * 1024)
    except Exception:
        return 0


def _swap_running_models() -> list[str]:
    """Names of models currently resident in llama-swap via /running.

    llama-swap may return a list of strings or a list of model dicts
    (with a 'model'/'name' field). Empty list when llama-swap is not
    reachable or the direct backend is used.
    """
    if os.getenv("LLAMACP_BACKEND", "llamacpp") != "llama-swap":
        return []
    swap_url = os.getenv("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")
    import re

    try:
        resp = requests.get(f"{swap_url.rstrip('/')}/running", timeout=2)
        if resp.status_code == 200:
            items = resp.json().get("running", []) or []
            names = []
            for it in items:
                if isinstance(it, str):
                    names.append(it)
                elif isinstance(it, dict):
                    # Prefer the GGUF path from cmd (e.g. "-m /models/X/Y.gguf"),
                    # which is what the gguf_models_cache is keyed on.
                    cmd = it.get("cmd") or ""
                    m = re.search(r"-m\s+(\S+\.gguf)", cmd)
                    if m:
                        names.append(m.group(1).rsplit("/", 1)[-1])
                    else:
                        names.append(it.get("name") or it.get("model") or "")
            return [n for n in names if n]
    except Exception:
        pass
    return []


def _get_llama_swap_running_ram_mb() -> int:
    """RAM (MB) held by llama-swap models that are currently loaded.

    A model selected in the Model Hub replaces whatever is resident right now,
    so this memory counts as free for the fit calculation.
    """
    from app.utils import get_gguf_models_cached

    running = _swap_running_models()
    if not running:
        return 0
    cache = get_gguf_models_cached("/models")
    total = 0
    for name in running:
        meta = cache.get(str(name).replace(".gguf", ""), {})
        size = meta.get("file_size_mb") or 0
        if size:
            total += int(size)
    return total


# Thresholds for tier classification (tuned for 8/12/16+ GB GPU tiers)
TIER_VRAM_GOOD_PCT = 0.85  # vram_needed / total_vram <= this => good
TIER_RAM_HEADROOM_MB = 2048  # 2GB OS/other reserved


def _classify_model_fit(
    model_name: str,
    context_length: int,
    file_size_mb: float | None = None,
    block_count: int | None = None,
    module: str = "multimodal",
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

    total_vram = _get_actual_vram_mb()[1]
    has_gpu = _platform_has_gpu()
    if total_vram is None or total_vram <= 0:
        total_vram = 0
    total_ram = _get_total_ram_mb()
    # RAM held by currently resident llama-swap models counts as free: selecting
    # a new model replaces them, so their memory becomes available again.
    free_ram_mb = _get_free_ram_mb() + _get_llama_swap_running_ram_mb()

    # If model is not in cache and we don't have file_size, try reading from GGUF file
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
                        raw_val = reader.fields[bc_key].parts[-1]
                        if hasattr(raw_val, "tolist"):
                            arr = raw_val.tolist()
                            block_count = int(arr[0]) if isinstance(arr, list) and len(arr) == 1 else int(raw_val)  # type: ignore[arg-type]
                        else:
                            block_count = int(raw_val) if raw_val is not None else None
            except Exception:
                pass

    # If model is still not resolvable, block save
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
            "free_ram_mb": free_ram_mb,
            "platform": "gpu" if has_gpu else "cpu",
            "arch_max_ctx": arch_max_ctx,
            "message": _("Model metadata not found. Run 'Refresh models' first."),
        }

    file_mb = float(file_size_mb)

    # 1. Compute VRAM needed for current context (with full GPU offload)
    from app.utils import get_mmproj_size_mb

    mmproj_mb = get_mmproj_size_mb(model_name) if module == "multimodal" else 0
    est = _estimate_model_vram(
        file_size_mb=file_mb,
        block_count=block_count,
        ngl=ngl_total,
        expert_count=cached.get("expert_count") or 0,
        ctx_size=max(int(context_length), 1),
        supports_mtp=cached.get("supports_mtp", False),
        mmproj_size_mb=mmproj_mb,
        kv_per_token=_kv_per_token_for_module(module),
    )
    vram_full_mb = int(est["total_mb"])
    kv_cache_mb = int(est["kv_cache_mb"])

    # 2. Tier classification
    # On a GPU-less host a model can only be "CPU" (fits in free RAM) or
    # "impossible" — never "good", since there is no VRAM to fit into.
    if not has_gpu:
        needed = int(file_mb + kv_cache_mb)
        ram_budget = max(0, free_ram_mb - TIER_RAM_HEADROOM_MB)
        if needed <= ram_budget:
            tier = "cpu_offload"
            ngl_recommended = 0
            can_save = True
            message = _("⚠ Runs on CPU only: {needed} MB needed, {free} MB free RAM.").format(
                needed=needed, free=free_ram_mb
            )
        else:
            tier = "impossible"
            ngl_recommended = 0
            can_save = False
            message = _("✗ Model cannot be loaded. Needs {needed} MB (file + KV cache), free RAM {free} MB.").format(
                needed=needed, free=free_ram_mb
            )
        return {
            "tier": tier,
            "can_save": can_save,
            "ngl_recommended": ngl_recommended,
            "ngl_total": ngl_total,
            "vram_mb": vram_full_mb,
            "file_mb": round(file_mb, 1),
            "kv_cache_mb": kv_cache_mb,
            "total_vram_mb": 0,
            "system_ram_mb": total_ram,
            "free_ram_mb": free_ram_mb,
            "platform": "cpu",
            "arch_max_ctx": arch_max_ctx,
            "message": message,
        }

    vram_budget = total_vram * TIER_VRAM_GOOD_PCT
    ram_budget = max(0, free_ram_mb - TIER_RAM_HEADROOM_MB)

    if vram_full_mb <= vram_budget:
        tier = "good"
        ngl_recommended = ngl_total
        message = _("✓ Fits in VRAM: {vram} MB / {total} MB").format(vram=vram_full_mb, total=total_vram)
        can_save = True
    else:
        # Try to find the largest ngl where both VRAM and RAM fit.
        # weights_on_gpu = file × (ngl/ngl_total)
        # weights_on_ram = file × (1 - ngl/ngl_total) = file - weights_on_gpu
        # kv is in VRAM (or partially in RAM; we treat as VRAM-side for safety)
        # overhead (compute buffers) is on GPU.
        # mmproj (vision encoder) is always resident in VRAM, regardless of ngl.
        # Find ngl_max such that:
        #   weights_on_gpu + kv + overhead + mmproj <= vram_budget
        #   file - weights_on_gpu + overhead <= ram_budget
        # Solving for weights_on_gpu:
        #   weights_on_gpu <= vram_budget - kv - overhead - mmproj
        #   file - weights_on_gpu <= ram_budget - overhead
        #   weights_on_gpu >= file - (ram_budget - overhead)
        vram_for_weights = vram_budget - kv_cache_mb - 400 - mmproj_mb
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
                "✗ Model cannot be loaded. Needs {needed} MB RAM (file + KV cache), available {total} MB."
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
                "⚠ Partial CPU offload: {ngl}/{total_layers} layers on GPU, {cpu} on RAM. ~5-10× slower."
            ).format(ngl=ngl_recommended, total_layers=ngl_total, cpu=cpu_layers)
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
        "free_ram_mb": free_ram_mb,
        "platform": "gpu",
        "arch_max_ctx": arch_max_ctx,
        "message": message,
    }
