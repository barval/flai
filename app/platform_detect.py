"""Platform & GPU detection abstraction.

Determines the compute backend at runtime — NVIDIA (CUDA), AMD (ROCm),
Intel (Vulkan) or CPU-only — and provides vendor-agnostic VRAM queries.
Every vendor-specific subprocess call lives here; the rest of FLAI never
talks to nvidia-smi / rocm-smi / vulkaninfo directly.

Platform selection order:
  1. FLAI_PLATFORM env var (explicit override: nvidia | amd | intel | cpu)
  2. nvidia-smi present and working         -> nvidia
  3. rocm-smi present and working           -> amd
  4. vulkaninfo present                     -> intel
  5. otherwise                              -> cpu

Full vendor memory parsing for AMD/Intel lands together with the ROCm and
Vulkan backends (see AGENTS.md roadmap). Until then, AMD/Intel report 0 VRAM
and FLAI runs them in CPU-only scheduling mode; the platform is still
detected and exposed so the UI and deploy tooling can act on it.

- AMD/Intel in CPU mode: compute_llamacpp_config() sees cuda_detected=False
  and returns n_gpu_layers=0, matching today's nvidia-smi-missing behaviour.
"""

import json
import logging
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

PLATFORMS = ("nvidia", "amd", "intel", "cpu")


@dataclass
class GpuInfo:
    """Vendor-agnostic GPU snapshot."""

    platform: str = "cpu"
    gpu_name: str = "unknown"
    total_vram_mb: int = 0
    used_vram_mb: int = 0
    available_vram_mb: int = 0
    cuda_detected: bool = False
    cuda_version: str = ""
    # What the models can actually be offloaded to: "cuda" (NVIDIA), "vulkan"
    # (AMD/Intel) or "none" (CPU). ``cuda_detected`` stays the CUDA-only switch
    # for the callers that genuinely need CUDA (video, ASR), so the two must not
    # be conflated: a Vulkan host has a GPU but no CUDA.
    accelerator: str = "none"


# ── Vendor probes ──────────────────────────────────────────────────────

_cuda_version_cache: str = ""
_CUDA_VERSION_RE = re.compile(r"CUDA Version:\s*([0-9][0-9.]*)")


def _nvidia_cuda_version() -> str:
    """Return the CUDA version reported by nvidia-smi, or an empty string.

    ``--query-gpu`` has no ``cuda_version`` field, so the value comes from the
    plain header line ("| NVIDIA-SMI 595.71.05 ... CUDA Version: 13.2 |"). The
    driver cannot change under a running container, so a non-empty answer is
    cached for the process lifetime.
    """
    global _cuda_version_cache
    if _cuda_version_cache:
        return _cuda_version_cache
    try:
        result = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=10)
    except Exception as e:
        logger.debug(f"nvidia-smi CUDA version probe failed: {e}")
        return ""
    match = _CUDA_VERSION_RE.search(result.stdout or "")
    if match:
        _cuda_version_cache = match.group(1)
    return _cuda_version_cache


def _probe_nvidia() -> GpuInfo | None:
    """Query GPU info via nvidia-smi. Returns None when unavailable."""
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
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning(f"nvidia-smi probe failed: {e}")
        return None
    if result.returncode != 0:
        return None
    lines = result.stdout.strip().split("\n")
    if not lines or not lines[0].strip():
        return None
    parts = [p.strip() for p in lines[0].split(",")]
    if len(parts) < 4:
        return None
    return GpuInfo(
        platform="nvidia",
        gpu_name=parts[0],
        total_vram_mb=int(parts[1]),
        used_vram_mb=int(parts[2]),
        available_vram_mb=int(parts[3]),
        cuda_detected=True,
        cuda_version=_nvidia_cuda_version(),
        accelerator="cuda",
    )


_ROCM_MEM_LABELS = {
    "total": ("VRAM Total Memory (B)", "VRAM Total Memory (MB)"),
    "used": ("VRAM Used Memory (B)", "VRAM Used Memory (MB)"),
    "free": ("VRAM Free Memory (B)", "VRAM Free Memory (MB)"),
}


def _rocm_parse_mb(value: str) -> int | None:
    """Convert a rocm-smi memory value (bytes or MB) to MB."""
    try:
        num = int(value.replace(",", "").strip())
    except (ValueError, AttributeError):
        return None
    # Values in MB are typically <= a few hundred thousand; bytes are huge.
    return num // (1024 * 1024) if num > 1_000_000_000 else num


def _amd_sysfs_vram(root: str = "/sys/class/drm") -> tuple[int, int, str]:
    """(total, used, name) in MB straight from the DRM sysfs.

    Consumer AMD cards rarely have the ROCm userspace installed, but the kernel
    exposes the same numbers in ``mem_info_vram_*`` (bytes), which needs no
    rocm-smi at all. Returns ``(0, 0, "")`` when no amdgpu card is present.
    """
    import glob

    for device in sorted(glob.glob(f"{root}/card[0-9]*/device")):
        try:
            with open(f"{device}/mem_info_vram_total", encoding="utf-8") as handle:
                total = int(handle.read().strip()) // (1024 * 1024)
            with open(f"{device}/mem_info_vram_used", encoding="utf-8") as handle:
                used = int(handle.read().strip()) // (1024 * 1024)
        except (OSError, ValueError):
            continue
        if total <= 0:
            continue
        name = "AMD GPU"
        try:
            with open(f"{device}/product_name", encoding="utf-8") as handle:
                name = handle.read().strip() or name
        except OSError:
            pass
        return total, used, name
    return 0, 0, ""


def _probe_amd() -> GpuInfo | None:
    """Query primary GPU via rocm-smi --json, falling back to sysfs.

    Returns None when neither source finds an AMD card.
    """
    try:
        result = subprocess.run(
            ["rocm-smi", "--showmeminfo", "vram", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        result = None
    except Exception as e:
        logger.warning(f"rocm-smi probe failed: {e}")
        result = None

    if result is None or result.returncode != 0 or not result.stdout.strip():
        total, used, name = _amd_sysfs_vram()
        if total <= 0:
            return None
        return GpuInfo(
            platform="amd",
            gpu_name=name,
            total_vram_mb=total,
            used_vram_mb=used,
            available_vram_mb=max(0, total - used),
            cuda_detected=False,
            accelerator="vulkan",
        )

    total = used = free = 0
    gpu_name = "AMD GPU"
    try:
        data = json.loads(result.stdout)
        if isinstance(data, dict):
            card = next(iter(data.items()))[1]
            if isinstance(card, dict):
                for size_label, candidates in _ROCM_MEM_LABELS.items():
                    for key in candidates:
                        if key in card:
                            value = _rocm_parse_mb(card[key])
                            if value is not None:
                                if size_label == "total":
                                    total = value
                                elif size_label == "used":
                                    used = value
                                elif size_label == "free":
                                    free = value
                            break
    except (ValueError, json.JSONDecodeError) as e:
        logger.warning(f"rocm-smi JSON parse failed: {e}")
        return None

    return GpuInfo(
        platform="amd",
        gpu_name=gpu_name,
        total_vram_mb=total,
        used_vram_mb=used,
        available_vram_mb=free or max(0, total - used),
        cuda_detected=False,
        accelerator="vulkan",
    )


def _probe_intel() -> GpuInfo | None:
    """Detect Intel GPU presence via vulkaninfo. Memory parsing lands with the
    Vulkan backend; here we only establish the platform and GPU name."""
    try:
        result = subprocess.run(
            ["vulkaninfo", "--summary"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning(f"vulkaninfo probe failed: {e}")
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None

    gpu_name = "Intel GPU"
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("deviceName"):
            _, _, value = line.partition("=")
            if value.strip():
                gpu_name = value.strip()
                break
    lowered = gpu_name.lower()
    if not any(p in lowered for p in ("intel", "arc", "iris")) or "llvmpipe" in lowered:
        return None
    total, used = _intel_vulkan_memory()
    return GpuInfo(
        platform="intel",
        gpu_name=gpu_name,
        total_vram_mb=total,
        used_vram_mb=used,
        available_vram_mb=max(0, total - used) if total else 0,
        cuda_detected=False,
        accelerator="vulkan",
    )


def _intel_vulkan_memory() -> tuple[int, int]:
    """(total, used) MB from the largest Vulkan device-local heap.

    ``vulkaninfo --summary`` names the device but carries no memory numbers, so
    the full report is read once and the biggest heap is taken as the card's
    memory. Intel iGPUs report system RAM here, which is what they actually use.
    Returns ``(0, 0)`` when the report cannot be read — the caller then behaves
    exactly like an unreadable NVIDIA card.
    """
    try:
        result = subprocess.run(["vulkaninfo"], capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        return 0, 0
    except Exception as e:
        logger.debug(f"vulkaninfo memory probe failed: {e}")
        return 0, 0
    if result.returncode != 0 or not result.stdout:
        return 0, 0

    best_total = 0
    best_budget = 0
    current_size = 0
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("size"):
            match = re.search(r"= (\d+)", stripped)
            current_size = int(match.group(1)) // (1024 * 1024) if match else 0
        elif stripped.startswith("budget"):
            match = re.search(r"= (\d+)", stripped)
            budget = int(match.group(1)) // (1024 * 1024) if match else 0
            if current_size > best_total:
                best_total = current_size
                best_budget = budget
    used = max(0, best_total - best_budget) if best_budget else 0
    return best_total, used


def _probe_cpu() -> GpuInfo:
    """CPU-only platform: no discrete GPU."""
    return GpuInfo(platform="cpu", gpu_name="unknown", cuda_detected=False)


_PROVIDERS: dict[str, str] = {
    "nvidia": "_probe_nvidia",
    "amd": "_probe_amd",
    "intel": "_probe_intel",
    "cpu": "_probe_cpu",
}


def _get_provider(platform: str) -> Callable[[], GpuInfo | None] | None:
    """Resolve a platform probe by name so module-level patches take effect."""
    name = _PROVIDERS.get(platform)
    func = globals().get(name) if name else None
    return func if callable(func) else None


# ── Public API ─────────────────────────────────────────────────────────


def detect_platform() -> str:
    """Detect the compute platform, honoring the FLAI_PLATFORM override."""
    override = os.getenv("FLAI_PLATFORM", "").strip().lower()
    if override in PLATFORMS:
        return override
    for platform in ("nvidia", "amd", "intel"):
        provider = _get_provider(platform)
        if provider is not None and provider() is not None:
            return platform
    return "cpu"


def get_platform_info(platform: str | None = None) -> GpuInfo:
    """Return vendor-agnostic GPU info for a (detected or explicit) platform."""
    if platform is None:
        platform = detect_platform()
    provider = _get_provider(platform)
    if provider is None:
        return _probe_cpu()
    info = provider()
    if info is None:
        # Explicit platform whose probe transiently failed — degrade to empty.
        return GpuInfo(platform=platform, gpu_name="unknown")
    return info


def query_free_vram_mb(platform: str | None = None) -> int | None:
    """Query available VRAM for the platform in MB, or None when unreadable."""
    info = get_platform_info(platform)
    if info.platform == "cpu":
        return None
    return info.available_vram_mb if info.available_vram_mb > 0 else 0


def query_vram(platform: str | None = None) -> tuple[int | None, int | None]:
    """Return (used_mb, total_mb) for the platform, or (None, None)."""
    info = get_platform_info(platform)
    if info.platform == "cpu" or info.total_vram_mb <= 0:
        return None, None
    return info.used_vram_mb, info.total_vram_mb
