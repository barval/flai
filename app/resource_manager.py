"""
Resource Manager — adaptive GPU/CPU/RAM management.

Detects available hardware resources at startup and computes optimal
configuration for llama-server and sd-cli to prevent OOM errors.

Monitors VRAM/RAM usage in real-time and provides gating logic so that
fast-worker tasks don't collide with slow-worker GPU operations.
"""

import logging
import os
import subprocess
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


class HardwareInfo:
    """Detected hardware capabilities."""

    def __init__(self):
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

    def __init__(self):
        self.hardware = HardwareInfo()
        self._lock = threading.Lock()
        self._sd_busy = False  # True while sd-cli is actively using GPU
        self._sd_busy_since = 0.0
        self._vram_poll_timer: threading.Timer | None = None
        self._vram_poll_interval = 60  # seconds

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

    # ── Known model VRAM limits (tested safe n_gpu_layers per GPU VRAM tier) ──
    # Keys: model_type, Values: dict of {min_vram_mb: max_n_gpu_layers}
    # Used to clamp the computed n_gpu_layers and prevent OOM.
    _MAX_SAFE_NGL: dict[str, list[tuple[int, int]]] = {
        "reasoning": [
            (24000, -1),  # 24GB+ — all layers on GPU
            (15844, 24),  # 16GB (RTX 5060 Ti) — tested stable
            (12000, 16),  # 12GB — partial offload
            (8000, 8),  # 8GB — minimal GPU
        ],
        "chat": [
            (8000, -1),  # 8GB+ — all layers fit
        ],
        "multimodal": [
            (12000, -1),  # 12GB+ — all layers fit
            (8000, 20),  # 8GB — partial
        ],
        "embedding": [
            (4000, -1),  # 4GB+ — all layers
        ],
    }

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
                model_name = config.get("model")
        except Exception:
            pass

        # Try to get model size from GGUF cache
        file_size_mb = None
        block_count = None
        expert_count = 0
        if model_name:
            try:
                gguf_cache = get_gguf_models_cached("/models")
                model_info = gguf_cache.get(model_name, {})
                if model_info:
                    file_size_mb = model_info.get("file_size_mb")
                    block_count = model_info.get("block_count")
                    expert_count = model_info.get("expert_count") or 0
            except Exception:
                pass

        # Fallback to approximate sizes if cache data unavailable
        model_vram = {
            "chat": 2500,  # Qwen3-4B ~2.5GB
            "multimodal": 5000,  # Qwen3VL-8B ~5GB
            "reasoning": 15000,  # gemma-4-26B-A4B ~15GB
            "embedding": 2000,  # bge-m3 ~2GB
        }

        # Use actual file size if available, otherwise use fallback estimate
        needed = int(file_size_mb * 1.2) if file_size_mb is not None else model_vram.get(model_type, 3000)

        # How much VRAM to reserve for other operations (sd-cli, overhead)
        reserve = 2000  # 2GB safety margin

        flash_attn_default = hw.cuda_detected

        result = {
            "n_gpu_layers": -1,  # default: all on GPU
            "ctx_size": 8192,
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

        # n_cpu_moe: for MoE models that don't fully fit — offload experts proportionally
        is_moe = expert_count > 0
        if is_moe and result["n_gpu_layers"] != -1 and result["n_gpu_layers"] > 0:
            # Model is MoE and partially offloaded — offload some experts to CPU
            partial_ratio = 1.0 - (result["n_gpu_layers"] / block_count) if (block_count and block_count > 0) else 0.5
            result["n_cpu_moe"] = max(1, int(expert_count * partial_ratio))
            if "warning" in result and result["warning"]:
                result["warning"] += f"; {result['n_cpu_moe']}/{expert_count} experts on CPU"
            else:
                result["warning"] = f"{result['n_cpu_moe']}/{expert_count} experts on CPU"

        # Clamp n_gpu_layers to known safe limits per VRAM tier
        caps = self._MAX_SAFE_NGL.get(model_type, [])
        current_ngl = result["n_gpu_layers"]
        if current_ngl != 0 and caps:
            capped = -1
            for min_vram, max_ngl in caps:
                if total_vram >= min_vram:
                    capped = max_ngl
                    break
            if capped is not None:
                if capped == -1:
                    capped = block_count if block_count else -1
                if current_ngl != -1 and capped != -1 and current_ngl > capped:
                    result["n_gpu_layers"] = capped
                    result["offload_kqv"] = True
                    if "warning" in result and result["warning"]:
                        result["warning"] += f" (capped to {capped} by safety limit)"
                    else:
                        result["warning"] = f"Safety cap: n_gpu_layers limited to {capped} for VRAM"

        return result

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

    # ── llama.cpp model management ──

    def unload_llamacpp_model(self, llamacpp_url: str | None = None) -> bool:
        """Force LLM backend to unload its current model from VRAM.

        This is called before sd-cli starts to free ALL VRAM for image operations.
        The model will be reloaded automatically on the next LLM request.

        Returns True if unload was successful or not needed.
        """
        import os

        import requests as req

        backend_type = os.getenv("LLAMACP_BACKEND", "llamacpp")

        if backend_type == "llama-swap":
            swap_url = os.getenv("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")
            try:
                resp = req.post(f"{swap_url.rstrip('/')}/api/models/unload", timeout=30)
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
            resp = req.get(f"{llamacpp_url.rstrip('/')}/v1/models", timeout=5)
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

            resp = req.post(f"{llamacpp_url.rstrip('/')}/models/unload", json={"model": loaded_model}, timeout=30)
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
        }

        backend_type = os.getenv("LLAMACP_BACKEND", "llamacpp")
        if backend_type == "llama-swap":
            try:
                import requests as req

                swap_url = os.getenv("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")
                resp = req.get(f"{swap_url.rstrip('/')}/running", timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    status["loaded_models"] = data.get("models", [])
            except Exception:
                pass

        return status


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
