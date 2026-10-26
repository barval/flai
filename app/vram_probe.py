# app/vram_probe.py
"""Measure what a model really costs in VRAM, and plan evictions with it.

The analytical estimate in ``resource_manager.KV_PER_TOKEN_MB`` is a constant per
module; the real footprint depends on the architecture, the KV quantisation and
the fitted context window. On this card the multimodal model occupies 7 694 MB
at ctx 32 768 (weights 4 795 + mmproj 1 105 + KV and buffers 1 794 — 0.055
MB/token), while the constant says 0.10. A 45 % overestimate costs a tier: on
8 GB it can push a model that fits into a partial CPU offload.

``flask vram-probe`` measures the real number and stores it in
``model_vram_estimates``; ``plan_unloads`` decides what has to go before a new
model is loaded, so co-residency is a measured decision instead of a guess.
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Kept free for prefill buffers, CUDA context growth and whatever else shares
# the card. A prompt of a few thousand tokens spikes the compute buffers for a
# second; loading into the last megabyte means the driver has nowhere to put it.
DEFAULT_HEADROOM_MB = 1200


def summarize_probe(
    baseline_mb: int,
    loaded_mb: int,
    weights_mb: int,
    mmproj_mb: int,
    ctx_size: int,
) -> dict[str, Any]:
    """Split a measured footprint into weights, vision encoder and KV/buffers."""
    total = max(0, int(loaded_mb) - int(baseline_mb))
    kv_and_buffers = max(0, total - int(weights_mb) - int(mmproj_mb))
    return {
        "total_mb": total,
        "weights_mb": int(weights_mb),
        "mmproj_mb": int(mmproj_mb),
        "kv_and_buffers_mb": kv_and_buffers,
        "kv_mb_per_token": round(kv_and_buffers / ctx_size, 4) if ctx_size else 0.0,
    }


def plan_unloads(
    loaded: list[dict[str, Any]],
    needed_mb: int,
    total_mb: int,
    headroom_mb: int = DEFAULT_HEADROOM_MB,
    protect: set[str] | None = None,
) -> list[str]:
    """Modules to unload so ``needed_mb`` fits next to what stays resident.

    ``loaded`` entries carry ``name``, ``mb`` and ``last_used``. Nothing is
    evicted when everything already fits; otherwise the least recently used
    models go one by one, stopping as soon as the new model fits. A protected
    model is only evicted when the window cannot hold both — the alternative is
    a load that spills into host memory and runs at a fraction of the speed.

    An empty list means "load it next to the residents"; a list containing every
    loaded model means the caller falls back to the one-model-at-a-time path.
    """
    protect = protect or set()
    used = sum(int(entry["mb"]) for entry in loaded)
    if used + needed_mb + headroom_mb <= total_mb:
        return []

    candidates = sorted(
        (entry for entry in loaded if entry["name"] not in protect),
        key=lambda entry: entry.get("last_used") or 0.0,
    )

    plan: list[str] = []
    freed = 0
    for entry in candidates:
        if used - freed + needed_mb + headroom_mb <= total_mb:
            break
        plan.append(entry["name"])
        freed += int(entry["mb"])

    if used - freed + needed_mb + headroom_mb > total_mb:
        plan.extend(entry["name"] for entry in loaded if entry["name"] not in plan)
    return plan


# q4_0 keeps 32 values in 18 bytes (one fp16 scale + 16 nibble bytes).
KV_BYTES_PER_VALUE = 0.5625


def kv_per_token_from_metadata(meta: dict[str, Any], bytes_per_value: float = KV_BYTES_PER_VALUE) -> float | None:
    """KV cache size per token, straight from the GGUF architecture.

    ``blocks * kv_heads * (key_len + value_len) * bytes`` — the same arithmetic
    llama.cpp does when it allocates the cache, so it holds for any model the
    admin picks instead of being a per-module constant. Returns None when the
    metadata does not carry the shapes (older cache rows, embedding models).
    """
    try:
        blocks = int(meta.get("block_count") or 0)
        kv_heads = int(meta.get("head_count_kv") or 0)
        key_len = int(meta.get("key_length") or 0)
        value_len = int(meta.get("value_length") or 0)
    except (TypeError, ValueError):
        return None
    if not (blocks and kv_heads and key_len and value_len):
        return None
    per_token = blocks * kv_heads * (key_len + value_len) * bytes_per_value
    return round(per_token / (1024 * 1024), 4)


def _gpu_used_mb() -> int:
    """Total VRAM in use, straight from the driver (0 on a CPU-only host)."""
    import subprocess

    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return 0
    first = (out.stdout or "").strip().splitlines()
    return int(first[0]) if first and first[0].strip().isdigit() else 0


def is_plausible_measurement(measured_mb: int, weights_mb: int, mmproj_mb: int = 0) -> bool:
    """A model cannot occupy less than its own weights.

    Below that the sample caught the wrong moment — usually a model that was
    still resident when the baseline was taken, so the delta is another model's
    tail. Such a number must never replace the estimate.
    """
    return measured_mb > 0 and measured_mb >= (weights_mb + mmproj_mb) * 0.9


def _weights_mb(module: str, model_name: str) -> int:
    """Size of the model file itself, resolved the same way llama-swap gets it."""
    import os

    if not model_name:
        return 0
    try:
        from app.llama_swap_config import LlamaSwapConfigGenerator

        path = LlamaSwapConfigGenerator().get_model_path(module, model_name)
        if path and os.path.exists(path):
            return os.path.getsize(path) // (1024 * 1024)
    except Exception as e:
        logger.debug(f"vram probe: cannot resolve {module} model path: {e}")
    return 0


def parse_compute_apps(csv_text: str) -> int:
    """VRAM held by llama-server processes, from `nvidia-smi --query-compute-apps`.

    Sampling the whole card counts every other consumer (sd-cli, a stray CUDA
    context); only the model server's own allocation belongs to the model.
    """
    total = 0
    for line in (csv_text or "").strip().splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 3:
            continue
        if "llama" not in parts[1].lower():
            continue
        if parts[2].isdigit():
            total += int(parts[2])
    return total


def llama_process_used_mb() -> int:
    """VRAM currently held by llama-server processes (0 without CUDA)."""
    import subprocess

    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return 0
    return parse_compute_apps(out.stdout)


def parse_proc_status_rss_mb(status_text: str) -> int:
    """Resident set of a process from /proc/<pid>/status, in MiB."""
    for line in (status_text or "").splitlines():
        if line.startswith("VmRSS:"):
            fields = line.split()
            if len(fields) >= 2 and fields[1].isdigit():
                return int(fields[1]) // 1024
    return 0


def llama_process_rss_mb() -> int:
    """Resident memory of llama-server processes — the CPU-only footprint.

    On a host without a GPU the weights and the KV cache live in RAM, so the
    same measurement (weights + KV + buffers) is taken from /proc instead of
    the driver. Only used when the platform is CPU; a llama-server running on a
    GPU host with ``-ngl 0`` would count there too.
    """
    import os

    total = 0
    try:
        pids = [name for name in os.listdir("/proc") if name.isdigit()]
    except OSError:
        return 0
    for pid in pids:
        try:
            with open(f"/proc/{pid}/comm", encoding="utf-8") as handle:
                if "llama" not in handle.read().strip().lower():
                    continue
            with open(f"/proc/{pid}/status", encoding="utf-8") as handle:
                total += parse_proc_status_rss_mb(handle.read())
        except OSError:
            continue
    return total


def memory_used_mb() -> int:
    """What the model servers occupy right now: VRAM on a GPU host, RSS on CPU."""
    import os

    platform = os.getenv("FLAI_PLATFORM", "").strip().lower()
    if platform == "cpu":
        return llama_process_rss_mb()
    used = llama_process_used_mb()
    return used if used > 0 else llama_process_rss_mb()


def record_measured_vram(module: str, swap_url: str) -> dict[str, Any] | None:
    """Calibrate a module from the model that is loaded at this moment.

    Called after a task finishes, while its model is still resident: no extra
    load, no extra unload. Only the model server's own allocation is sampled, and
    only when it is the single running model — otherwise the sample cannot be
    attributed, and a wrong number is worse than the analytical constant.
    """
    import requests

    from app.database import upsert_vram_estimate
    from app.model_config import get_model_config
    from app.utils import get_gguf_models_cached, get_mmproj_size_mb

    try:
        resp = requests.get(f"{swap_url}/running", timeout=3)
        running = resp.json().get("running", []) if resp.status_code == 200 else []
    except Exception:
        return None
    if len(running) != 1 or running[0].get("name") != module:
        return None

    used_mb = memory_used_mb()
    if used_mb <= 0:
        return None

    config = get_model_config(module) or {}
    model_name = config.get("model_name") or ""
    ctx_size = int(config.get("context_length") or 0)
    weights_mb = _weights_mb(module, model_name)
    mmproj_mb = get_mmproj_size_mb(model_name) if module == "multimodal" else 0
    if used_mb <= (weights_mb + mmproj_mb) * 0.9:
        logger.debug(
            f"vram calibration skipped for {module}: {used_mb}MB vs {weights_mb}+{mmproj_mb}MB weights"
        )
        return None
    meta = get_gguf_models_cached("/models").get(model_name.replace(".gguf", ""), {})
    kv_per_token = kv_per_token_from_metadata(meta)

    upsert_vram_estimate(
        module,
        model_name=model_name,
        context_length=ctx_size,
        n_gpu_layers=int(config.get("n_gpu_layers") or -1),
        measured_mb=used_mb,
    )
    logger.info(
        f"vram calibration: {module} = {used_mb}MB at ctx {ctx_size} "
        f"(KV {kv_per_token} MB/token, weights {weights_mb}, mmproj {mmproj_mb})"
    )
    return {
        "module": module,
        "measured_mb": used_mb,
        "ctx_size": ctx_size,
        "kv_per_token": kv_per_token,
    }


def probe_module(module: str, swap_url: str, lang: str = "ru", peak_prompt_chars: int = 0) -> dict[str, Any]:
    """Measure one module's real VRAM footprint (and optionally its prefill peak).

    Unloads every model first, samples the baseline, then forces llama-swap to
    load the target and samples while it happens — the maximum of those samples
    is the peak, which is what co-residency has to budget for.
    """
    import requests

    from app.model_config import get_model_config
    from app.resource_manager import get_resource_manager
    from app.utils import get_mmproj_size_mb

    rm = get_resource_manager()
    config = get_model_config(module) or {}
    model_name = config.get("model_name") or ""
    ctx_size = int(config.get("context_length") or 0)

    rm.unload_llamacpp_model(swap_url)
    deadline = time.time() + 90
    running: list[dict[str, Any]] = []
    while time.time() < deadline:
        try:
            running = requests.get(f"{swap_url}/running", timeout=5).json().get("running", [])
        except Exception:
            running = []
        if not running:
            break
        time.sleep(1)
    if running:
        raise RuntimeError(
            f"cannot measure {module}: {[m.get('name') for m in running]} stayed resident after the unload"
        )
    time.sleep(2)
    baseline = _gpu_used_mb()

    def _call(text: str, max_tokens: int) -> None:
        if module == "embedding":
            requests.post(
                f"{swap_url}/v1/embeddings",
                json={"model": module, "input": text or "."},
                timeout=900,
            )
            return
        requests.post(
            f"{swap_url}/v1/chat/completions",
            json={
                "model": module,
                "messages": [{"role": "user", "content": text or "."}],
                "max_tokens": max_tokens,
                "stream": False,
            },
            timeout=900,
        )

    peak = baseline
    _call(".", 1)
    with contextlib.suppress(Exception):
        peak = max(peak, _gpu_used_mb())

    if peak_prompt_chars:
        filler = "контекст " * (peak_prompt_chars // 10)
        import threading

        stop = {"flag": False}

        def _sample() -> None:
            nonlocal peak
            while not stop["flag"]:
                peak = max(peak, _gpu_used_mb())
                time.sleep(0.2)

        sampler = threading.Thread(target=_sample, daemon=True)
        sampler.start()
        try:
            _call(filler, 8)
        finally:
            stop["flag"] = True
            sampler.join(timeout=2)

    from app.utils import get_gguf_models_cached

    meta = get_gguf_models_cached("/models").get(model_name.replace(".gguf", ""), {})
    expected = kv_per_token_from_metadata(meta)
    summary = summarize_probe(
        baseline_mb=baseline,
        loaded_mb=peak,
        weights_mb=_weights_mb(module, model_name),
        mmproj_mb=get_mmproj_size_mb(model_name) if module == "multimodal" else 0,
        ctx_size=ctx_size,
    )
    summary["kv_expected_mb"] = round(expected * ctx_size) if expected else 0
    summary["plausible"] = is_plausible_measurement(
        summary["total_mb"], summary["weights_mb"], summary["mmproj_mb"]
    )
    summary.update({"module": module, "model": model_name, "ctx_size": ctx_size, "baseline_mb": baseline})
    return summary
