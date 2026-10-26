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


def _gpu_used_mb() -> int:
    """Total VRAM in use, straight from the driver."""
    import subprocess

    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    first = (out.stdout or "").strip().splitlines()
    return int(first[0]) if first and first[0].strip().isdigit() else 0


def _weights_mb(model_name: str) -> int:
    import os

    for root in ("/models", os.path.expanduser("~/models")):
        candidate = os.path.join(root, model_name)
        if os.path.exists(candidate):
            return os.path.getsize(candidate) // (1024 * 1024)
    return 0


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
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            running = requests.get(f"{swap_url}/running", timeout=5).json().get("running", [])
        except Exception:
            running = []
        if not running:
            break
        time.sleep(1)
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

    summary = summarize_probe(
        baseline_mb=baseline,
        loaded_mb=peak,
        weights_mb=_weights_mb(model_name),
        mmproj_mb=get_mmproj_size_mb(model_name) if module == "multimodal" else 0,
        ctx_size=ctx_size,
    )
    summary.update({"module": module, "model": model_name, "ctx_size": ctx_size, "baseline_mb": baseline})
    return summary
