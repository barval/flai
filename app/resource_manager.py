"""
Resource Manager — adaptive GPU/CPU/RAM management.

Detects available hardware resources at startup and computes optimal
configuration for llama-server and sd-cli to prevent OOM errors.

Monitors VRAM/RAM usage in real-time and provides gating logic so that
fast-worker tasks don't collide with slow-worker GPU operations.
"""

import os
import subprocess
import logging
import threading
import time
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)


class HardwareInfo:
    """Detected hardware capabilities."""

    def __init__(self):
        self.total_vram_mb: int = 0
        self.available_vram_mb: int = 0
        self.total_ram_mb: int = 0
        self.available_ram_mb: int = 0
        self.cpu_count: int = 0
        self.gpu_name: str = 'unknown'
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
                ['nvidia-smi', '--query-gpu=name,memory.total,memory.used,memory.free',
                 '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                if lines:
                    parts = lines[0].split(',')
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
        return hw

    def _detect_total_ram_mb(self) -> int:
        """Get total system RAM in MB."""
        try:
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if line.startswith('MemTotal:'):
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
            with open('/proc/meminfo', 'r') as f:
                total = avail = 0
                for line in f:
                    if line.startswith('MemTotal:'):
                        total = int(line.split()[1]) // 1024
                    elif line.startswith('MemAvailable:'):
                        avail = int(line.split()[1]) // 1024
                return avail if avail > 0 else total
        except Exception:
            return 4096  # Assume 4GB free

    # ── Adaptive config computation ──

    def compute_llamacpp_config(self, model_type: str) -> Dict[str, Any]:
        """Compute optimal llama-server parameters for given model type.

        Returns dict with:
            n_gpu_layers: how many layers to put on GPU (-1 = all)
            ctx_size: context window size
            cache_capacity: model cache limit
            offload_to_cpu: whether to offload KQV cache to CPU
        """
        hw = self.hardware
        vram = hw.available_vram_mb  # what's free right now
        total_vram = hw.total_vram_mb

        # Model VRAM requirements (approximate, for Q4_K_M)
        model_vram = {
            'chat': 2500,          # Qwen3-4B ~2.5GB
            'multimodal': 5000,    # Qwen3VL-8B ~5GB
            'reasoning': 15000,    # gemma-4-26B-A4B ~15GB
            'embedding': 2000,     # bge-m3 ~2GB
        }
        needed = model_vram.get(model_type, 3000)

        # How much VRAM to reserve for other operations (sd-cli, overhead)
        reserve = 2000  # 2GB safety margin

        result = {
            'n_gpu_layers': -1,  # default: all on GPU
            'ctx_size': 8192,
            'cache_capacity': 4096,
            'offload_kqv': False,
            'flash_attn': False,
            'warning': None,
        }

        if not hw.cuda_detected:
            # CPU-only mode
            result['n_gpu_layers'] = 0
            result['warning'] = 'No GPU detected — running CPU-only mode'
            return result

        if total_vram >= 24000:
            # 24GB+ (RTX 3090/4090) — everything fits
            result['cache_capacity'] = 8192
            result['flash_attn'] = True
        elif total_vram >= 16000:
            # 16GB (RTX 4060 Ti / 4070) — tight
            if needed + reserve > total_vram:
                # Model doesn't fit fully — reduce layers
                result['n_gpu_layers'] = max(10, int((total_vram - reserve) / needed * 32))
                result['offload_kqv'] = True
                result['cache_capacity'] = 2048
                result['warning'] = (
                    f'Model {model_type} ({needed}MB) partially offloaded to CPU. '
                    f'Performance may be reduced.'
                )
        elif total_vram >= 8000:
            # 8GB — most models need CPU offloading
            result['n_gpu_layers'] = 10
            result['offload_kqv'] = True
            result['ctx_size'] = 4096
            result['cache_capacity'] = 1024
            result['warning'] = f'Limited VRAM ({total_vram}MB) — heavy CPU offloading'
        else:
            # <8GB — CPU-only
            result['n_gpu_layers'] = 0
            result['warning'] = f'Very limited VRAM ({total_vram}MB) — CPU-only mode'

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

    def unload_llamacpp_model(self, llamacpp_url: str) -> bool:
        """Force llama-server to unload its current model from VRAM.

        This is called before sd-cli starts to free ALL VRAM for image operations.
        The model will be reloaded automatically on the next llama.cpp request.

        Returns True if unload was successful or not needed.
        """
        try:
            import requests as req
            # POST /models/unload with model name (or empty to unload current)
            # First, get current model
            resp = req.get(f"{llamacpp_url.rstrip('/')}/v1/models", timeout=5)
            if resp.status_code != 200:
                logger.warning("Cannot get llama.cpp model list")
                return False

            models = resp.json().get('data', [])
            loaded_model = None
            for m in models:
                if m.get('status', {}).get('value') == 'loaded':
                    loaded_model = m['id']
                    break

            if not loaded_model:
                logger.info("No llama.cpp model loaded — VRAM already free")
                return True

            # Unload the model
            resp = req.post(
                f"{llamacpp_url.rstrip('/')}/models/unload",
                json={'model': loaded_model},
                timeout=30
            )
            if resp.status_code == 200:
                logger.info(f"Unloaded llama.cpp model: {loaded_model}")
                return True
            else:
                logger.warning(f"Failed to unload llama.cpp model: {resp.status_code} {resp.text[:200]}")
                return False
        except Exception as e:
            logger.warning(f"Error unloading llama.cpp model: {e}")
            return False

    def get_status(self) -> Dict[str, Any]:
        """Get current resource status for debugging/health check."""
        return {
            'gpu_name': self.hardware.gpu_name,
            'cuda_detected': self.hardware.cuda_detected,
            'total_vram_mb': self.hardware.total_vram_mb,
            'available_vram_mb': self.hardware.available_vram_mb,
            'total_ram_mb': self.hardware.total_ram_mb,
            'available_ram_mb': self.hardware.available_ram_mb,
            'cpu_count': self.hardware.cpu_count,
            'sd_busy': self._sd_busy,
        }


# Global singleton
_resource_manager: Optional[ResourceManager] = None
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
