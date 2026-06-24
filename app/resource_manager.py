"""
Resource Manager — adaptive GPU/CPU/RAM management.

Detects available hardware resources at startup and computes optimal
configuration for llama-server and sd-cli to prevent OOM errors.

Monitors VRAM/RAM usage in real-time and provides gating logic so that
fast-worker tasks don't collide with slow-worker GPU operations.
"""

import contextlib
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)


class HardwareInfo:
    """Detected hardware capabilities."""

    def __init__(self) -> None:
        self.total_vram_mb: int = 0
        self.available_vram_mb: int = 0
        self.total_ram_mb: int = 0
        self.available_ram_mb: int = 0
        self.cpu_count: int = 0
        self.gpu_name: str = "unknown"
        self.cuda_detected: bool = False


class ResourceManager:
    """Manages GPU/CPU/RAM resources and computes optimal model configurations.

    Usage:
        rm = ResourceManager()
        rm.detect_hardware()
        config = rm.compute_llamacpp_config('multimodal')
        # config['n_gpu_layers'] -> adaptive value
        # config['can_run_parallel'] -> whether fast-worker can proceed
    """

    def __init__(self) -> None:
        self.hardware = HardwareInfo()
        self._lock = threading.Lock()
        self._sd_busy = False  # True while sd-cli is actively using GPU
        self._sd_busy_since = 0.0
        self._video_busy = False  # True while ltx-video is actively using GPU
        self._video_busy_since = 0.0
        self._vram_poll_timer: threading.Timer | None = None
        self._vram_poll_interval = 60  # seconds
        self._shutdown_event = threading.Event()
        # LTX-Video unload cache: skip repeat HTTP unload within 30s
        self._last_ltx_unload_at: float = 0.0
        # LTX-Video hang detection: 3 consecutive timeouts -> docker restart
        self._ltx_unload_consecutive_timeouts: int = 0
        self._ltx_restart_initiated_at: float = 0.0

    # ── Hardware detection ──

    def detect_hardware(self) -> HardwareInfo:
        """Detect VRAM, RAM, CPU at startup."""
        hw = self.hardware
        hw.cpu_count = os.cpu_count() or 1

        # Detect RAM
        hw.total_ram_mb = self._detect_total_ram_mb()
        hw.available_ram_mb = self._detect_available_ram_mb()

        # Detect GPU via nvidia-smi
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,memory.used,memory.free",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                if lines:
                    parts = lines[0].split(",")
                    hw.gpu_name = parts[0].strip()
                    hw.total_vram_mb = int(parts[1].strip())
                    used = int(parts[2].strip())
                    hw.available_vram_mb = int(parts[3].strip())
                    hw.cuda_detected = True
                    logger.info(
                        f"GPU detected: {hw.gpu_name}, "
                        f"VRAM: {hw.total_vram_mb}MB total, "
                        f"{hw.available_vram_mb}MB available, "
                        f"{used}MB used"
                    )
                else:
                    logger.warning("nvidia-smi returned empty output")
            else:
                logger.warning(f"nvidia-smi failed: {result.stderr.strip()}")
        except FileNotFoundError:
            logger.info("nvidia-smi not found — running CPU-only mode")
            hw.cuda_detected = False
        except Exception as e:
            logger.warning(f"GPU detection failed: {e}")
            hw.cuda_detected = False

        self.hardware = hw
        self._start_vram_polling()
        return hw  # type: ignore[no-any-return]

    def _start_vram_polling(self):
        """Start background VRAM polling."""
        if not self.hardware.cuda_detected:
            return
        self._poll_vram()

    def _poll_vram(self):
        """Query nvidia-smi for available VRAM and update hardware info."""
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                free = int(result.stdout.strip().split("\n")[0].strip())
                self.hardware.available_vram_mb = free
        except Exception:
            pass
        # Reschedule next poll to keep available_vram_mb fresh (Bug A6 fix)
        if self._vram_poll_interval > 0 and not self._shutdown_event.is_set():
            self._vram_poll_timer = threading.Timer(self._vram_poll_interval, self._poll_vram)
            self._vram_poll_timer.daemon = True
            self._vram_poll_timer.start()

    def _detect_total_ram_mb(self) -> int:
        """Get total system RAM in MB."""
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) // 1024  # kB → MB
        except Exception:
            pass
        # Fallback
        try:
            import resource

            return resource.getrlimit(resource.RLIMIT_AS)[0] // (1024 * 1024)
        except Exception:
            return 8192  # Assume 8GB

    def _detect_available_ram_mb(self) -> int:
        """Get available system RAM in MB."""
        try:
            with open("/proc/meminfo") as f:
                total = avail = 0
                for line in f:
                    if line.startswith("MemTotal:"):
                        total = int(line.split()[1]) // 1024
                    elif line.startswith("MemAvailable:"):
                        avail = int(line.split()[1]) // 1024
                return avail if avail > 0 else total
        except Exception:
            return 4096  # Assume 4GB free

    # Safety caps removed — iterative degradation in compute_llamacpp_config()
    # dynamically computes n_gpu_layers from actual model file_size, block_count,
    # ctx_size, and measured VRAM.  Hardcoded per-tier caps caused OOM-free models
    # (e.g. 9 GB IQ2_XXS) to be needlessly offloaded to CPU on 16 GB GPUs.

    # ── Adaptive config computation ──

    def compute_llamacpp_config(self, model_type: str) -> dict[str, Any]:
        """Compute optimal llama-server parameters for given model type.

        Returns dict with:
            n_gpu_layers: how many layers to put on GPU (-1 = all)
            ctx_size: context window size
            cache_capacity: model cache limit
            offload_to_cpu: whether to offload KQV cache to CPU
            cache_type_k: KV cache type for K
            cache_type_v: KV cache type for V
            n_cpu_moe: number of MoE experts to offload to CPU (0 = all on GPU)
        """
        from app.utils import get_gguf_models_cached

        hw = self.hardware
        total_vram = hw.total_vram_mb

        # Get model configuration from database to determine actual file size
        from app.model_config import get_model_config

        model_name = None
        try:
            config = get_model_config(model_type)
            if config:
                model_name = config.get("model_name") or config.get("model")
        except Exception:
            pass

        # Context length: from DB config, fallback 8192 (matches default in result dict)
        ctx_size = config.get("context_length") if config else None
        if not ctx_size:
            ctx_size = 8192

        # Try to get model size from GGUF cache
        file_size_mb = None
        block_count = None
        expert_count = 0
        supports_mtp = False
        if model_name:
            try:
                gguf_cache = get_gguf_models_cached("/models")
                model_info = gguf_cache.get(model_name, {})
                if model_info:
                    file_size_mb = model_info.get("file_size_mb")
                    block_count = model_info.get("block_count")
                    expert_count = model_info.get("expert_count") or 0
                    supports_mtp = model_info.get("supports_mtp", False)
            except Exception:
                pass

        # Fallback to approximate sizes if cache data unavailable
        model_vram = {
            "chat": 2500,
            "multimodal": 5000,
            "reasoning": 10000,
            "embedding": 2000,
        }

        # Use actual file size if available, otherwise use fallback estimate
        needed = int(file_size_mb * 1.2) if file_size_mb is not None else model_vram.get(model_type, 3000)
        if supports_mtp:
            needed = int(needed * 1.15)

        # How much VRAM to reserve for other operations (sd-cli, overhead)
        reserve = 2000  # 2GB safety margin

        flash_attn_default = hw.cuda_detected

        result = {
            "n_gpu_layers": -1,  # default: all on GPU
            "ctx_size": ctx_size,
            "cache_capacity": 4096,
            "offload_kqv": False,
            "flash_attn": flash_attn_default,
            "cache_type_k": "q4_0",
            "cache_type_v": "q4_0",
            "n_cpu_moe": 0,
            "warning": "",
        }

        if not hw.cuda_detected:
            # CPU-only mode
            result["n_gpu_layers"] = 0
            result["flash_attn"] = False
            result["warning"] = "No GPU detected — running CPU-only mode"
            return result

        # Calculate n_gpu_layers based on available VRAM and model requirements
        if block_count is not None and block_count > 0:
            # Distribute layers proportionally based on available VRAM
            available_for_model = max(0, total_vram - reserve)
            if needed > 0 and available_for_model > 0:
                layer_ratio = min(1.0, available_for_model / needed)
                result["n_gpu_layers"] = max(1, int(block_count * layer_ratio))
                if result["n_gpu_layers"] >= block_count:
                    result["n_gpu_layers"] = -1  # All layers fit
                else:
                    result["offload_kqv"] = True
                    result["warning"] = (
                        f"Model partially offloaded ({result['n_gpu_layers']}/{block_count} layers on GPU)"
                    )
        elif needed + reserve > total_vram:
            # Model doesn't fit fully — reduce layers based on estimate
            result["n_gpu_layers"] = max(10, int((total_vram - reserve) / needed * 32))
            result["offload_kqv"] = True
            result["cache_capacity"] = 2048
            result["warning"] = (
                f"Model {model_type} ({needed}MB) partially offloaded to CPU. Performance may be reduced."
            )

        if total_vram >= 24000:
            # 24GB+ (RTX 3090/4090) — everything fits
            result["cache_capacity"] = 8192
        elif total_vram >= 16000:
            # 16GB (RTX 4060 Ti / 4070) — tight
            if result["n_gpu_layers"] == -1 and needed + reserve > total_vram:
                # Override if we didn't catch it above
                result["n_gpu_layers"] = max(10, int((total_vram - reserve) / needed * 32))
                result["offload_kqv"] = True
                result["cache_capacity"] = 2048
        elif total_vram >= 8000:
            # 8GB — most models need CPU offloading
            if result["n_gpu_layers"] == -1:
                result["n_gpu_layers"] = 10
            result["offload_kqv"] = True
            result["ctx_size"] = 4096
            result["cache_capacity"] = 1024
            result["warning"] = f"Limited VRAM ({total_vram}MB) — heavy CPU offloading"
        else:
            # <8GB — CPU-only
            result["n_gpu_layers"] = 0
            result["warning"] = f"Very limited VRAM ({total_vram}MB) — CPU-only mode"

        # Degradation: ensure model fits in available VRAM BEFORE loading
        # Iteratively reduce n_gpu_layers if estimated VRAM exceeds capacity
        if result["n_gpu_layers"] != 0 and block_count:
            effective_ngl: int = result["n_gpu_layers"]  # type: ignore[assignment]
            if effective_ngl == -1:
                effective_ngl = block_count
            while effective_ngl > 0:
                ratio = min(1.0, effective_ngl / block_count) if block_count > 0 else 1.0
                est_weights = max(file_size_mb or needed, 100) * ratio * (0.95 if expert_count > 0 else 1.0) * (1.15 if supports_mtp else 1.0)
                est_kv = ctx_size * 0.12  # q4_0: ~0.12 MB per token (matches get_vram_needed_mb)
                est_overhead = max(400, int((file_size_mb or needed) * 0.05 + ctx_size * 0.002))
                est_total = est_weights + est_kv + est_overhead
                if est_total <= available_for_model or effective_ngl == 0:
                    break
                effective_ngl = max(0, effective_ngl - max(1, block_count // 8))
            if effective_ngl != result["n_gpu_layers"]:
                if effective_ngl == -1 if result["n_gpu_layers"] == -1 else False:
                    pass
                result["n_gpu_layers"] = effective_ngl if effective_ngl < block_count else -1
                if result["n_gpu_layers"] != -1:
                    result["offload_kqv"] = True
                    result["warning"] = (
                        f"Auto-degraded: n_gpu_layers={result['n_gpu_layers']}/{block_count} "
                        f"to fit VRAM ({est_total:.0f}MB > {available_for_model}MB)"
                    )

        # n_cpu_moe: for MoE models that don't fully fit — offload experts proportionally
        is_moe = expert_count > 0
        ngl: int = result["n_gpu_layers"]  # type: ignore[assignment]
        if is_moe and ngl != -1 and ngl > 0:
            # Model is MoE and partially offloaded — offload some experts to CPU
            partial_ratio = 1.0 - (ngl / block_count) if (block_count and block_count > 0) else 0.5
            result["n_cpu_moe"] = max(1, int(expert_count * partial_ratio))
            if "warning" in result and result["warning"]:
                result["warning"] = f"{result['warning']}; {result['n_cpu_moe']}/{expert_count} experts on CPU"
            else:
                result["warning"] = f"{result['n_cpu_moe']}/{expert_count} experts on CPU"

        return result

    # ── Dynamic VRAM estimation ──

    def get_vram_needed_mb(self, model_type: str, ctx_size: int | None = None) -> int:
        """Compute VRAM needed for a model from GGUF metadata + DB config.

        Reads model file size and layer count from GGUF cache,
        context_length from model_configs, and n_gpu_layers from
        compute_llamacpp_config().  Returns total estimated MB.
        """
        from app.model_config import get_model_config
        from app.utils import get_gguf_models_cached

        config = get_model_config(model_type)
        model_name = config.get("model_name", "") if config else ""

        # Context length: prefer explicit argument, then DB config, then fallback
        if ctx_size is None:
            ctx_size = config.get("context_length") if config else 4096
        if not ctx_size:
            ctx_size = 4096

        # GGUF metadata
        gguf_info = get_gguf_models_cached("/models").get(model_name.replace(".gguf", ""), {})
        file_size_mb = gguf_info.get("file_size_mb") or 0
        block_count = gguf_info.get("block_count") or 0
        expert_count = gguf_info.get("expert_count") or 0
        supports_mtp = gguf_info.get("supports_mtp", False)

        # Fallback block_count when GGUF metadata missing
        if block_count == 0:
            default_blocks = {"chat": 28, "reasoning": 40, "multimodal": 36, "embedding": 12}
            block_count = default_blocks.get(model_type, 30)

        # Fallback file_size when GGUF metadata missing
        if file_size_mb == 0:
            default_sizes = {"chat": 2500, "reasoning": 12000, "multimodal": 5000, "embedding": 2000}
            file_size_mb = default_sizes.get(model_type, 3000)

        # n_gpu_layers from the same logic used for llama-swap config
        try:
            rm_config = self.compute_llamacpp_config(model_type)
            ngl = rm_config.get("n_gpu_layers", -1)
        except Exception:
            ngl = -1
        if ngl == -1 and block_count:
            ngl = block_count
        elif ngl is None:
            ngl = block_count or 1

        moe_factor = 0.95 if (expert_count or 0) > 0 else 1.0
        mtp_factor = 1.15 if supports_mtp else 1.0
        ratio = min(1.0, ngl / block_count) if (block_count or 0) > 0 else 1.0
        weights_mb = (file_size_mb or 0) * ratio * moe_factor * mtp_factor

        # KV cache estimate (q4_0: ~0.12 MB per token including CUDA overhead)
        # Actual measured on RTX 5060 Ti: chat 0.05, multimodal 0.18, reasoning 0.12 MB/token.
        # Old 0.35 was 3-7x too high, causing ensure_vram_for to fail with generous margin.
        kv_per_token = 0.12
        kv_mb = ctx_size * kv_per_token

        overhead = max(400, int(file_size_mb * 0.05 + ctx_size * 0.002))

        total = int(weights_mb + kv_mb + overhead)

        # Prefer measured VRAM from model_vram_estimates when available —
        # far more accurate than GGUF formula (e.g. multimodal measured 10367 MB
        # vs formula 6178 MB, chat 5733 vs 4410). Adds 1 GB safety margin
        # to keep headroom for CUDA fragmentation and temporary buffers.
        # The table stores rows under either the actual file name (e.g.
        # "Qwen3-4B-Instruct-2507-MXFP4_MOE.gguf") or the module name
        # (e.g. "chat") — try most-recent record for the module first
        # (newer measurement), then exact model_name match.
        try:
            from app.database import get_vram_estimate

            measured = get_vram_estimate(model_type) or get_vram_estimate(model_type, model_name)
            if measured and measured.get("measured_vram_mb"):
                measured_mb = int(measured["measured_vram_mb"])
                total = max(total, measured_mb + 1000)
        except Exception:
            pass

        return max(total, 100)

    # ── Unified VRAM guarantee ──

    def ensure_vram_for(self, model_type: str, needed_mb: int | None = None, timeout: int = 15) -> bool:
        """Ensure VRAM is available for the given model type.

        1. Unload ALL llama.cpp models via llama-swap
        2. Unload video pipeline
        3. Flush CUDA cache
        4. Poll /running until 0 models remain
        5. Poll nvidia-smi until needed_mb is free

        Returns True only when VRAM is confirmed available.
        NEVER proceeds if VRAM is insufficient — returns False on timeout.
        """
        if needed_mb is None:
            needed_mb = self.get_vram_needed_mb(model_type)

        llamacpp_url = os.getenv("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")

        # 1. Check if needed model is already loaded — skip unload if so
        try:
            resp_check = requests.get(f"{llamacpp_url}/running", timeout=2)
            if resp_check.status_code == 200:
                running = resp_check.json().get("running", [])
                if len(running) == 1:
                    cmd = running[0].get("cmd", "")
                    from app.model_config import get_model_config

                    config = get_model_config(model_type)
                    model_name = config.get("model_name", "") if config else ""
                    if model_name and model_name in cmd:
                        # Needed model already loaded — just verify VRAM
                        self._poll_vram()
                        free = self.hardware.available_vram_mb
                        if free >= needed_mb:
                            logger.info(
                                f"ensure_vram_for [{model_type}]: model already loaded, "
                                f"{free}MB free >= {needed_mb}MB needed — OK"
                            )
                            return True
        except Exception:
            pass

        # Model not loaded or different model active — full unload
        logger.info(f"ensure_vram_for [{model_type}]: unloading all models, need {needed_mb}MB")
        self.unload_llamacpp_model(llamacpp_url)

        # 2. Unload video pipeline
        with contextlib.suppress(Exception):
            self.unload_video_pipeline()

        # 3+4. Poll /running + nvidia-smi until VRAM sufficient
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = requests.get(f"{llamacpp_url}/running", timeout=5)
                if resp.status_code == 200:
                    models = resp.json().get("running", [])
                    if len(models) > 0:
                        logger.debug(f"ensure_vram_for [{model_type}]: {len(models)} model(s) still active")
                        time.sleep(2)
                        continue
            except Exception:
                pass

            self._poll_vram()
            free = self.hardware.available_vram_mb
            if free >= needed_mb:
                logger.info(f"ensure_vram_for [{model_type}]: {free}MB free >= {needed_mb}MB needed — OK")
                return True

            logger.debug(f"ensure_vram_for [{model_type}]: {free}MB free, need {needed_mb}MB — waiting...")
            time.sleep(2)

        logger.error(
            f"ensure_vram_for [{model_type}]: TIMEOUT after {timeout}s — "
            f"{self.hardware.available_vram_mb}MB free, need {needed_mb}MB"
        )
        return False

    # ── Runtime gating ──

    def mark_sd_busy(self):
        """Signal that sd-cli started using GPU."""
        with self._lock:
            self._sd_busy = True
            self._sd_busy_since = time.time()

    def mark_sd_idle(self):
        """Signal that sd-cli finished."""
        with self._lock:
            self._sd_busy = False

    def mark_video_busy(self):
        """Signal that ltx-video started using GPU."""
        with self._lock:
            self._video_busy = True
            self._video_busy_since = time.time()

    def mark_video_idle(self):
        """Signal that ltx-video finished."""
        with self._lock:
            self._video_busy = False

    # ── llama.cpp model management ──

    def unload_video_pipeline(self) -> bool:
        """Unload the LTX-Video pipeline from VRAM via /v1/unload.

        Retries on failure and verifies that VRAM was actually freed by polling
        nvidia-smi after the unload. If the POST succeeds but VRAM stays high,
        retries the unload up to 3 times (CUDA deallocation can be lazy).

        Optimizations (v8.9+):
          - Pre-flight GET /v1/vram_info: if pipeline not loaded, skip HTTP entirely
            (avoids 8s × 8 polls of waiting when nothing needs unloading)
          - Success condition is clamped to total - 1GB so it's reachable when
            free_before is already near total
          - 30s result cache: repeated calls within window skip the whole flow
            (eliminates double-call between _process_image_chat_task and
            ensure_vram_for for the same request)
          - 3 consecutive HTTP timeouts trigger docker restart of flai-ltxvideo
            via the mounted /var/run/docker.sock
        """
        ltx_url = os.getenv("LTX_VIDEO_WRAPPER_URL", "http://flai-ltxvideo:7872")

        # Cache: skip repeat unload if it succeeded within the last 30s
        if time.time() - self._last_ltx_unload_at < 30:
            return True

        # Pre-flight: ask ltxvideo if pipeline is loaded at all
        # /v1/vram_info is cheap and side-effect-free; if pipeline_loaded=false
        # the 8×1s polling loop is pure waste
        try:
            resp = requests.get(f"{ltx_url.rstrip('/')}/v1/vram_info", timeout=2)
            if resp.status_code == 200:
                info = resp.json()
                if not info.get("pipeline_loaded", False):
                    logger.debug("LTX-Video pipeline not loaded — skipping unload")
                    self._last_ltx_unload_at = time.time()
                    self._ltx_unload_consecutive_timeouts = 0
                    return True
        except Exception as e:
            logger.debug(f"LTX-Video pre-flight check failed: {e}")
            # Continue with regular flow on pre-flight failure (container may be
            # alive but slow to respond)

        self._poll_vram()
        free_before = self.hardware.available_vram_mb
        total = self.hardware.total_vram_mb

        for attempt in range(3):
            try:
                resp = requests.post(f"{ltx_url.rstrip('/')}/v1/unload", timeout=30)
                if resp.status_code != 200:
                    logger.warning(f"LTX-Video unload HTTP {resp.status_code} (attempt {attempt + 1})")
                    if attempt < 2:
                        time.sleep(3)
                    continue
            except Exception as e:
                logger.warning(f"Error unloading LTX-Video (attempt {attempt + 1}): {e}")
                if "timed out" in str(e).lower() or "timeout" in str(e).lower():
                    self._ltx_unload_consecutive_timeouts += 1
                    if self._ltx_unload_consecutive_timeouts >= 3:
                        self._maybe_restart_ltx_video()
                if attempt < 2:
                    time.sleep(3)
                continue

            # HTTP 200 received — reset hang counter
            self._ltx_unload_consecutive_timeouts = 0

            # Success target: either +3000 MB freed OR total-1GB reached
            # (the latter is the physical ceiling when LTX-Video was small
            # relative to total VRAM, or wasn't loaded at all)
            target = min(total - 1000, free_before + 3000)
            for _ in range(8):
                self._poll_vram()
                if self.hardware.available_vram_mb >= target:
                    logger.info(
                        f"LTX-Video pipeline unloaded — VRAM freed "
                        f"({free_before}MB → {self.hardware.available_vram_mb}MB)"
                    )
                    self._last_ltx_unload_at = time.time()
                    return True
                time.sleep(0.5)
            logger.warning(
                f"LTX-Video unload returned 200 but VRAM did not free "
                f"(was {free_before}MB, now {self.hardware.available_vram_mb}MB, "
                f"total {total}MB, target {target}MB) — attempt {attempt + 1}/3"
            )
            if attempt < 2:
                time.sleep(2)

        return False

    def _maybe_restart_ltx_video(self) -> None:
        """If LTX-Video container is unresponsive (3 consecutive timeouts), restart it.

        Uses the Docker socket mounted at /var/run/docker.sock in
        docker-compose.gpu.yml:27. Rate-limited to 1 restart per 5 minutes
        to avoid a restart loop.
        """
        if time.time() - self._ltx_restart_initiated_at < 300:
            return
        self._ltx_restart_initiated_at = time.time()

        logger.error("LTX-Video unresponsive: 3 consecutive timeouts. Restarting container.")
        self._restart_ltx_container()

    def _force_restart_ltx_video(self) -> None:
        """Force-restart LTX-Video to free CUDA context overhead (~3 GB).

        Called after every video generation task completes. The gunicorn worker
        inside flai-ltxvideo holds a CUDA context that survives /v1/unload —
        only a container restart releases it. Unconditional: Docker handles
        concurrent restart gracefully (returns error if already restarting).
        """
        logger.warning("Force-restarting LTX-Video container to free CUDA context")
        self._restart_ltx_container()

    def _restart_ltx_container(self) -> None:
        """Restart the flai-ltxvideo container via Docker socket."""
        try:
            result = subprocess.run(
                ["docker", "restart", "flai-ltxvideo"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                logger.info("flai-ltxvideo restart initiated via docker CLI")
                self._ltx_unload_consecutive_timeouts = 0
                self._last_ltx_unload_at = 0.0
            else:
                logger.error(f"Docker restart failed (rc={result.returncode}): {result.stderr}")
        except FileNotFoundError:
            logger.error("Docker CLI not found in container — cannot restart flai-ltxvideo")
        except Exception as e:
            logger.error(f"Failed to restart flai-ltxvideo: {e}")

    def estimate_video_vram_needed(self) -> int:
        """VRAM threshold for LTX-Video pipeline loading.

        Resolution order:
          1. Measured peak from previous successful gen (model_vram_estimates table)
          2. Query ltxvideo's /v1/vram_info endpoint for component file sizes
             (text_encoder stays on CPU per ltx_wrapper.py:159)
          3. Compute from local filesystem (if /app/models is mounted)
          4. Env var LTX_VIDEO_VRAM_MB fallback (default 8500)
        """
        try:
            from app.database import get_vram_estimate

            measured = get_vram_estimate("ltx-video")
            if measured and measured.get("measured_vram_mb"):
                return int(measured["measured_vram_mb"])
        except Exception as e:
            logger.debug(f"Could not load measured VRAM for ltx-video: {e}")

        # Try querying ltxvideo for component sizes (works without /app/models mount)
        try:
            ltx_url = os.getenv("LTX_VIDEO_WRAPPER_URL", "http://flai-ltxvideo:7872")
            resp = requests.get(f"{ltx_url.rstrip('/')}/v1/vram_info", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                sizes = data.get("component_sizes_mb", {})
                transformer_mb = sizes.get("transformer", 0)
                upscaler_mb = sizes.get("upscaler", 0)
                if transformer_mb > 0:
                    persistent_mb = transformer_mb + upscaler_mb
                    peak_mb = int(persistent_mb * 1.15)
                    logger.info(
                        f"LTX-Video estimated peak VRAM (from ltxvideo): {peak_mb} MB "
                        f"(transformer={transformer_mb}MB, upscaler={upscaler_mb}MB)"
                    )
                    return peak_mb
        except Exception as e:
            logger.debug(f"Could not query ltxvideo /v1/vram_info: {e}")

        # Fallback: compute from local filesystem
        models_dir = Path(os.getenv("LTX_MODELS_DIR", "/app/models"))
        transformer_path = models_dir / "ltxv-2b-0.9.8-distilled.safetensors"
        upscaler_path = models_dir / "ltxv-spatial-upscaler-0.9.8.safetensors"

        transformer_bytes = transformer_path.stat().st_size if transformer_path.exists() else 0
        upscaler_bytes = upscaler_path.stat().st_size if upscaler_path.exists() else 0

        if transformer_bytes == 0:
            return int(os.getenv("LTX_VIDEO_VRAM_MB", "8500"))

        persistent_bytes = transformer_bytes + upscaler_bytes
        peak_mb = int(persistent_bytes * 1.15 // (1024 * 1024))
        logger.info(
            f"LTX-Video estimated peak VRAM (from local fs): {peak_mb} MB "
            f"(transformer={transformer_bytes // 1024**2}MB, upscaler={upscaler_bytes // 1024**2}MB)"
        )
        return peak_mb

    def ensure_vram_for_reasoning(self, needed_mb: int | None = None) -> bool:
        """Ensure sufficient VRAM for reasoning model.
        Delegates to ensure_vram_for() with dynamic VRAM estimate.
        """
        if not self.hardware.cuda_detected:
            return True
        return self.ensure_vram_for("reasoning", needed_mb)

    def measure_model_vram(self, module: str, model_name: str, ctx_size: int, ngl: int) -> None:
        """Measure actual VRAM consumption after a model loads and store in DB.

        Called from llamacpp_client after a successful model response to track
        real VRAM usage per model type, which feeds into the admin panel display.
        For ltx-video the model_name is the pipeline config and ctx_size/ngl are unused.
        """
        try:
            from app.database import upsert_vram_estimate

            self._poll_vram()
            after_free = self.hardware.available_vram_mb
            total = self.hardware.total_vram_mb

            if total > 0 and after_free > 0:
                used = total - after_free
                upsert_vram_estimate(
                    module=module,
                    model_name=model_name,
                    context_length=ctx_size,
                    n_gpu_layers=ngl,
                    measured_mb=used,
                )
                logger.info(f"VRAM measurement [{module}]: {used}MB used ({after_free}MB free / {total}MB total)")
        except Exception as e:
            logger.debug(f"VRAM measurement failed for {module}: {e}")

    def measure_video_vram_peak(self, model_name: str = "ltxv-2b-0.9.8-distilled") -> None:
        """Record peak VRAM during/after video generation.

        Called from queue after a successful video gen. Reads current VRAM usage
        and stores it as a measurement for the ltx-video module, so future
        estimate_video_vram_needed() returns the real value.
        """
        try:
            self._poll_vram()
            free = self.hardware.available_vram_mb
            total = self.hardware.total_vram_mb
            if total > 0 and free > 0:
                from app.database import upsert_vram_estimate

                upsert_vram_estimate(
                    module="ltx-video",
                    model_name=model_name,
                    context_length=0,
                    n_gpu_layers=0,
                    measured_mb=total - free,
                )
                logger.info(f"VRAM measurement [ltx-video]: {total - free}MB used ({free}MB free / {total}MB total)")
        except Exception as e:
            logger.debug(f"Video VRAM measurement failed: {e}")

    def unload_llamacpp_model(self, llamacpp_url: str | None = None) -> bool:
        """Force LLM backend to unload its current model from VRAM.

        This is called before sd-cli starts to free ALL VRAM for image operations.
        The model will be reloaded automatically on the next LLM request.

        Returns True if unload was successful or not needed.
        """
        import os

        backend_type = os.getenv("LLAMACP_BACKEND", "llamacpp")

        if backend_type == "llama-swap":
            swap_url = os.getenv("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")
            try:
                resp = requests.post(f"{swap_url.rstrip('/')}/api/models/unload", timeout=30)
                if resp.status_code == 200:
                    logger.info("llama-swap: all models unloaded for SD")
                    return True
                logger.warning(f"llama-swap unload failed: {resp.status_code}")
                return False
            except Exception as e:
                logger.warning(f"Error unloading llama-swap models: {e}")
                return False

        if not llamacpp_url:
            llamacpp_url = "http://flai-llamacpp:8033"

        try:
            resp = requests.get(f"{llamacpp_url.rstrip('/')}/v1/models", timeout=5)
            if resp.status_code != 200:
                logger.warning("Cannot get llama.cpp model list")
                return False

            models = resp.json().get("data", [])
            loaded_model = None
            for m in models:
                if m.get("status", {}).get("value") == "loaded":
                    loaded_model = m["id"]
                    break

            if not loaded_model:
                logger.info("No llama.cpp model loaded — VRAM already free")
                return True

            resp = requests.post(f"{llamacpp_url.rstrip('/')}/models/unload", json={"model": loaded_model}, timeout=30)
            if resp.status_code == 200:
                logger.info(f"Unloaded llama.cpp model: {loaded_model}")
                return True
            else:
                logger.warning(f"Failed to unload llama.cpp model: {resp.status_code} {resp.text[:200]}")
                return False
        except Exception as e:
            logger.warning(f"Error unloading llama.cpp model: {e}")
            return False

    def get_status(self) -> dict[str, Any]:
        """Get current resource status for debugging/health check."""
        import os

        status = {
            "gpu_name": self.hardware.gpu_name,
            "cuda_detected": self.hardware.cuda_detected,
            "total_vram_mb": self.hardware.total_vram_mb,
            "available_vram_mb": self.hardware.available_vram_mb,
            "total_ram_mb": self.hardware.total_ram_mb,
            "available_ram_mb": self.hardware.available_ram_mb,
            "cpu_count": self.hardware.cpu_count,
            "sd_busy": self._sd_busy,
            "video_busy": self._video_busy,
        }

        backend_type = os.getenv("LLAMACP_BACKEND", "llamacpp")
        if backend_type == "llama-swap":
            try:
                swap_url = os.getenv("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")
                resp = requests.get(f"{swap_url.rstrip('/')}/running", timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    status["loaded_models"] = data.get("running", [])
            except Exception:
                pass

        return status

    def log_gpu_memory(self, tag: str = "") -> dict[str, int]:
        """Query and log current GPU memory via nvidia-smi.

        Returns dict with total_mb and free_mb (0 if query fails).
        """
        result = {"total_mb": 0, "free_mb": 0}
        try:
            swap_url = os.getenv("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")
            resp = requests.get(f"{swap_url.rstrip('/')}/running", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                loaded = data.get("running", [])
                mem_info = data.get("vram", {})
                total = mem_info.get("total", 0)
                free = mem_info.get("free", 0)
                if total and free:
                    result = {"total_mb": total // (1024 * 1024), "free_mb": free // (1024 * 1024)}
                    logger.info(
                        f"GPU memory [{'after ' + tag if tag else 'status'}]: "
                        f"{result['free_mb']}MB free / {result['total_mb']}MB total, "
                        f"loaded models: {loaded}"
                    )
        except Exception:
            # Fallback: raw nvidia-smi
            try:
                out = subprocess.check_output(
                    ["nvidia-smi", "--query-gpu=memory.total,memory.free", "--format=csv,noheader,nounits"],
                    timeout=10,
                ).decode()
                parts = out.strip().split(", ")
                if len(parts) >= 2:
                    total = int(parts[0].strip())
                    free = int(parts[1].strip())
                    result = {"total_mb": total, "free_mb": free}
                    logger.info(
                        f"GPU memory [nvidia-smi{' after ' + tag if tag else ''}]: {free}MB free / {total}MB total"
                    )
            except Exception:
                logger.warning("Could not query GPU memory")
        return result


# Global singleton
_resource_manager: ResourceManager | None = None
_rm_lock = threading.Lock()


def get_resource_manager() -> ResourceManager:
    """Get the global ResourceManager singleton."""
    global _resource_manager
    if _resource_manager is None:
        with _rm_lock:
            if _resource_manager is None:
                _resource_manager = ResourceManager()
                _resource_manager.detect_hardware()
    return _resource_manager
