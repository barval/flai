# modules/video.py
"""
Module for video generation.

Two interchangeable backends, selected by ``VIDEO_BACKEND``:

  - ``ltx`` (default) — LTX-Video 2B 0.9.8 distilled through ltx-wrapper:
    fast, up to 10 seconds (768×512×240 @ 24 fps, 8 steps).
  - ``wan`` — Wan 2.2 TI2V 5B Turbo through the sd.cpp wrapper:
    slower but noticeably better motion and detail, 5 seconds
    (832×480×121 @ 24 fps, 4 steps).

Both speak the same HTTP contract (``POST /v1/video/generations``), so the
video pipeline — queue, VRAM planning, image-to-video — is shared.
"""

import base64
import logging
import os
import time
from datetime import datetime
from io import BytesIO
from typing import Any

import requests
from PIL import Image

from app.mixins import TranslationMixin
from app.vram_estimate import wan_video_vram_mb
from modules.base import wait_for_service

BACKEND_LTX = "ltx"
BACKEND_WAN = "wan"
VIDEO_BACKENDS = (BACKEND_LTX, BACKEND_WAN)

# Defaults shared with multimodal.py video parameter generation
DEFAULT_VIDEO_NEGATIVE_PROMPT = "worst quality, inconsistent motion, blurry, jittery, distorted"
LTX_DEFAULT_PARAMS = {
    "negative_prompt": DEFAULT_VIDEO_NEGATIVE_PROMPT,
    "width": 768,
    "height": 512,
    "num_frames": 240,
    "frame_rate": 24,
}
# Wan 2.2 5B was trained on 1280×704 at 24 fps for 121 frames (5 s). Frames
# must be 4n+1 (VAE temporal stride 4) and both sides divisible by 32, or
# sd.cpp pads and crops the result.
WAN_DEFAULT_PARAMS = {
    "negative_prompt": DEFAULT_VIDEO_NEGATIVE_PROMPT,
    "width": 832,
    "height": 480,
    "num_frames": 121,
    "frame_rate": 24,
}
DEFAULT_VIDEO_PARAMS = LTX_DEFAULT_PARAMS

WAN_MAX_FRAMES = 121
WAN_720P_PARAMS = {"width": 1280, "height": 704, "num_frames": 121, "frame_rate": 24}
# Step-down chain: keep the 5-second length as long as the card allows and only
# then shorten. Measured footprints (see app/vram_estimate.py): 832×480×121
# ~15.0 GB, 640×384×121 ~13.0 GB, 512×288×121 ~11.7 GB, 512×288×49 ~10.6 GB.
WAN_FALLBACK_PARAMS: list[dict[str, int]] = [
    {"width": 832, "height": 480, "num_frames": 121, "frame_rate": 24},
    {"width": 640, "height": 384, "num_frames": 121, "frame_rate": 24},
    {"width": 512, "height": 288, "num_frames": 121, "frame_rate": 24},
    {"width": 512, "height": 288, "num_frames": 49, "frame_rate": 24},
]

# RAM budgeting for CPU generation (no VRAM to rely on).
# Measured: 384×256×49 peaked at ~19.2 GiB cgroup usage (~17400 MB static
# models + ~2.2 GiB activations). Activations scale ~linearly with latent volume
# (spatial//32 × spatial//32 × temporal//8), fit to that measurement with margin.
VIDEO_RAM_STATIC_MB = 17_400
VIDEO_RAM_ACTIVATIONS_MB_PER_UNIT = 3.0
VIDEO_RAM_SAFETY_MARGIN_MB = 1024

# Wan has no PyTorch stack: sd.cpp holds the 3.4 GB Q4 transformer, the 6 GB
# umt5 encoder and the 0.4 GB VAE in RAM (~11 GB static). Calibrated on two
# CPU probes: 256×192×17 finished at ~12 GB peak, 512×288×49 exceeded a 12 GB
# cap, which the ~2.5 MB per latent unit below reproduces (960 units → 13.4 GB,
# 7488 units → 29.7 GB). CPU Wan is an opt-in escape hatch, not a default.
WAN_RAM_STATIC_MB = 11_000
WAN_RAM_ACTIVATIONS_MB_PER_UNIT = 2.5
# Measured: 4 steps at 256×192×17 took 27 s (835 584 voxels / 6.75 s per step).
WAN_CPU_VOXELS_PER_STEP_S = 123_000
# Measured: VAE decode 69.45 s for 17 frames at 256×192 (~4.1 s per frame).
WAN_CPU_VAE_S_PER_FRAME_AT_256X192 = 4.1
WAN_CPU_TIME_OVERHEAD_S = 60

MAX_VIDEO_SOURCE_SIZE = 768
WAN_MAX_VIDEO_SOURCE_SIZE = 832



def resize_video_source_image(
    image_data: str | None, max_size: int = MAX_VIDEO_SOURCE_SIZE
) -> tuple[str | None, dict[str, Any]]:
    """Downscale a base64 source image when its longer side exceeds ``max_size``.

    Returns (image_data, resized_info) where resized_info mirrors the resize
    metadata used for user-facing notices.
    """
    resized_info: dict[str, Any] = {"resized": False, "original_size": None, "new_size": None}
    if not image_data:
        return image_data, resized_info
    try:
        img_bytes = base64.b64decode(image_data)
        img = Image.open(BytesIO(img_bytes))
        w, h = img.size
        if max(w, h) > max_size:
            ratio = max_size / max(w, h)
            new_w, new_h = int(w * ratio), int(h * ratio)
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)  # type: ignore[assignment]
            if img.mode in ("RGBA", "LA", "P"):
                rgb_img = Image.new("RGB", img.size, (255, 255, 255))
                rgb_img.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                img = rgb_img  # type: ignore[assignment]
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=90)
            image_data = base64.b64encode(buf.getvalue()).decode("utf-8")
            resized_info = {"resized": True, "original_size": (w, h), "new_size": (new_w, new_h)}
    except Exception:
        pass
    return image_data, resized_info


class VideoModule(TranslationMixin):
    """Video generation facade over the LTX and Wan backends."""

    CPU_FALLBACK_PARAMS: list[dict[str, int]] = [
        {"width": 384, "height": 256, "num_frames": 120, "frame_rate": 12},
        {"width": 256, "height": 192, "num_frames": 57, "frame_rate": 6},
        # Last resort: ~4 s of video; finishes even when the host is heavily
        # loaded and the VAE decode degrades several-fold (24 frames).
        {"width": 256, "height": 192, "num_frames": 24, "frame_rate": 6},
    ]

    # Denoising steps in ltvx-2b-0.9.8-distilled.yaml (default pipeline).
    CPU_STEPS_DEFAULT = 8
    # Calibrated CPU throughput: 384×256×120 ≈ 495 s per step on a 12-core
    # host (lenovo-book) → ~24 000 voxels rendered per step per second.
    CPU_VOXELS_PER_STEP_S = 24_000
    # Fixed per-generation overhead: T5 text encode + upscaler + I/O.
    CPU_TIME_OVERHEAD_S = 300
    # Calibrated CPU video decode/assembly: a 256×192×57 run on the idle
    # 12-core host took ~43 min of VAE decode + mp4 encode (≈45 s per frame).
    # Under concurrent host load the causal VAE decode runs single-threaded
    # (measured ~120 s/frame on 2026-10-09) — the constant carries a ~1.3×
    # margin over the idle measurement so the planner stays honest.
    CPU_VAE_SECONDS_PER_FRAME = 60

    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.wrapper_url = None
        self.available = False
        self.timeout = 600
        self.backend = BACKEND_LTX
        self.model_type = "ltxv-2b-0.9.8-distilled"
        self.multimodal_module = None
        # Backend-specific sampling defaults; LTX reads them from its pipeline
        # YAML and ignores the payload fields.
        self.steps = self.CPU_STEPS_DEFAULT
        self.cfg_scale = 1.0
        self.flow_shift = 3.0
        self.allow_cpu = False

        if app:
            self.init_app(app)

    def init_app(self, app):
        """Initialize module with Flask app"""
        self.app = app
        self._refresh_backend()

        self.logger.info(
            f"VideoModule initialized: backend={self.backend}, wrapper={self.wrapper_url}, "
            f"model={self.model_type}, timeout={self.timeout}s"
        )

        self.available = wait_for_service(
            self.check_availability, self.logger, self.service_name, retries=1, delay=1
        )

        if self.available:
            self.logger.info(f"VideoModule initialized and available. Timeout: {self.timeout}s")
        else:
            self.logger.warning(
                f"VideoModule initialized, but {self.service_name} unavailable ({self.wrapper_url}). "
                "Will retry on each request."
            )

    def _refresh_backend(self) -> None:
        """Re-read the backend selection from the live config.

        ``VIDEO_BACKEND`` can be saved in Admin Panel → Settings, which updates
        ``app.config`` without a restart (``settings_store.save_values``). Caching
        the wrapper URL, model and timeout at boot would keep sending requests to
        the previous container, so every entry point refreshes them first.
        """
        app = self.app
        backend = str(app.config.get("VIDEO_BACKEND", BACKEND_LTX)).strip().lower()
        if backend not in VIDEO_BACKENDS:
            self.logger.warning(f"Unknown VIDEO_BACKEND '{backend}' — falling back to '{BACKEND_LTX}'")
            backend = BACKEND_LTX
        self.backend = backend

        if backend == BACKEND_WAN:
            self.wrapper_url = app.config.get("WAN_VIDEO_URL", "http://flai-sd:7861")
            self.timeout = app.config.get("WAN_VIDEO_TIMEOUT", 1800)
            self.model_type = app.config.get("WAN_VIDEO_MODEL", "wan2.2-ti2v-5b-turbo-Q4_K_M.gguf")
            self.steps = app.config.get("WAN_VIDEO_STEPS", 4)
            self.cfg_scale = app.config.get("WAN_VIDEO_CFG_SCALE", 1.0)
            self.flow_shift = app.config.get("WAN_VIDEO_FLOW_SHIFT", 3.0)
            self.allow_cpu = bool(app.config.get("WAN_VIDEO_ALLOW_CPU", False))
            self.service_name = "sd-wrapper"
        else:
            self.wrapper_url = app.config.get("LTX_VIDEO_WRAPPER_URL", "http://flai-ltxvideo:7872")
            self.timeout = app.config.get("LTX_VIDEO_TIMEOUT", 600)
            self.model_type = app.config.get("LTX_VIDEO_MODEL", "ltxv-2b-0.9.8-distilled")
            self.steps = self.CPU_STEPS_DEFAULT
            self.allow_cpu = False
            self.service_name = "ltx-wrapper"

    @property
    def default_params(self) -> dict[str, Any]:
        """Default generation parameters for the active backend."""
        params = WAN_DEFAULT_PARAMS if self.backend == BACKEND_WAN else LTX_DEFAULT_PARAMS
        return dict(params)

    @property
    def vram_margin_mb(self) -> int:
        """Safety margin on top of the estimated peak (MB).

        Wan's figure is a measured peak — 15 003 MB of pipeline against 15 511 MB
        free on the reference 16 GB card — so a 1 GB margin would reject a clip
        that demonstrably runs. LTX keeps 1 GB because its estimate comes from
        the pipeline's measured total, not from the exact requested resolution.
        """
        return 300 if self.backend == BACKEND_WAN else 1000

    @property
    def max_frames(self) -> int:
        """Longest clip the backend was trained on (240 for LTX, 121 for Wan)."""
        return WAN_MAX_FRAMES if self.backend == BACKEND_WAN else 240


    def set_multimodal_module(self, multimodal_module):
        """Set reference to multimodal module (for prompt generation)."""
        self.multimodal_module = multimodal_module

    def check_availability(self):
        """Check the active backend's wrapper availability."""
        self._refresh_backend()
        if not self.wrapper_url:
            self.logger.error(f"{self.service_name} URL not configured")
            return False

        try:
            response = requests.get(f"{self.wrapper_url.rstrip('/')}/health", timeout=5)
            if response.status_code == 200:
                self.available = True
                return True
            else:
                self.logger.warning(f"{self.service_name} returned status {response.status_code}")
                self.available = False
                return False
        except Exception as e:
            self.logger.error(f"Error connecting to {self.service_name}: {e}")
            self.available = False
            return False

    def estimate_vram_mb(
        self, prompt_data: dict[str, Any] | None = None, total_vram_mb: int | None = None
    ) -> int:
        """VRAM the active backend needs for the requested clip (MB).

        LTX uses the measured pipeline peak (``model_vram_estimates``); Wan is
        computed from the requested resolution because its footprint grows
        visibly between 480p and 720p. The value is the model's own peak — the
        caller adds the safety margin.
        """
        from app.resource_manager import get_resource_manager

        self._refresh_backend()
        if self.backend == BACKEND_WAN:
            params = prompt_data or WAN_DEFAULT_PARAMS
            total_vram = total_vram_mb
            if total_vram is None:
                total_vram = get_resource_manager().hardware.total_vram_mb
            # Mirrors the wrapper: below 12 GB the text encoder is streamed
            # from RAM instead of being resident on the card.
            encoder_out = isinstance(total_vram, int) and 0 < total_vram < 12_000
            return wan_video_vram_mb(
                int(params.get("width", WAN_DEFAULT_PARAMS["width"])),
                int(params.get("height", WAN_DEFAULT_PARAMS["height"])),
                int(params.get("num_frames", WAN_DEFAULT_PARAMS["num_frames"])),
                encoder_out_of_vram=encoder_out,
            )
        try:
            return get_resource_manager().estimate_video_vram_needed()
        except Exception:
            return 8000

    @staticmethod
    def _estimate_video_vram_mb() -> int:
        """Backward-compatible alias for the LTX pipeline peak."""
        try:
            from app.resource_manager import get_resource_manager

            return get_resource_manager().estimate_video_vram_needed()
        except Exception:
            return 8000


    @classmethod
    def estimate_peak_ram_mb(
        cls, width: int, height: int, num_frames: int, backend: str = BACKEND_LTX
    ) -> int:
        """Estimate peak container RAM for a video generation on CPU (MB).

        LTX latent volume: spatial /32 each axis, temporal (+8 → +1 frame).
        Static model footprint dominates (~17.4 GB); activations grow ~linearly
        with latent volume — calibrated against a 384×256×49 probe (19.2 GiB peak).

        Wan (sd.cpp) has a smaller static footprint (Q4 transformer + umt5 +
        VAE ≈ 11 GB) but a 16-pixel VAE stride, so its activations grow faster.
        """
        if backend == BACKEND_WAN:
            spatial_wan = max(1, width // 16) * max(1, height // 16)
            temporal_wan = max(1, (num_frames - 1) // 4 + 1)
            return int(WAN_RAM_STATIC_MB + spatial_wan * temporal_wan * WAN_RAM_ACTIVATIONS_MB_PER_UNIT)
        spatial = max(1, width // 32) * max(1, height // 32)
        temporal = max(1, (num_frames - 2) // 8 + 1)
        units = spatial * temporal
        return int(VIDEO_RAM_STATIC_MB + units * VIDEO_RAM_ACTIVATIONS_MB_PER_UNIT)

    @classmethod
    def estimate_cpu_generation_time_s(
        cls,
        width: int,
        height: int,
        num_frames: int,
        steps: int | None = None,
        backend: str = BACKEND_LTX,
    ) -> int:
        """Estimate wall-clock time for a CPU generation (seconds).

        LTX: per-step time is ~linear in the latent volume (width×height×frames):
        measured ~495 s/step for 384×256×120 on a 12-core CPU host. VAE
        decode + mp4 assembly scales with the number of frames (~45 s/frame)
        and a fixed overhead covers T5 encode, upscaling and I/O.

        Wan: measured 4 steps at 256×192×17 in 27 s (≈123 000 voxels per step
        per second) and 69.45 s of VAE decode (≈4.1 s per frame at that size,
        scaling with the frame area).
        """
        if backend == BACKEND_WAN:
            step_count = steps if steps is not None else 4
            voxels_wan = max(1, width) * max(1, height) * max(1, num_frames)
            area_ratio = (max(1, width) * max(1, height)) / (256 * 192)
            decode_s = max(1, num_frames) * WAN_CPU_VAE_S_PER_FRAME_AT_256X192 * area_ratio
            return int(step_count * voxels_wan / WAN_CPU_VOXELS_PER_STEP_S + decode_s + WAN_CPU_TIME_OVERHEAD_S)

        step_count = steps if steps is not None else cls.CPU_STEPS_DEFAULT
        voxels = max(1, width) * max(1, height) * max(1, num_frames)
        return (
            int(step_count * voxels // cls.CPU_VOXELS_PER_STEP_S)
            + int(max(1, num_frames) * cls.CPU_VAE_SECONDS_PER_FRAME)
            + cls.CPU_TIME_OVERHEAD_S
        )

    def plan_cpu_generation(
        self, prompt_data: dict[str, Any], lang: str = "ru"
    ) -> tuple[dict[str, Any] | None, str | None, str | None]:
        """Decide video parameters on CPU based on available RAM AND time.

        Memory budget = min(host free RAM, container memory cap), the cap read
        from LTX_VIDEO_RAM_LIMIT_MB / WAN_VIDEO_RAM_LIMIT_MB (set in the CPU
        compose file). Time budget defaults to 85% of the generation timeout so
        generation reliably finishes before the client request times out;
        override with LTX_VIDEO_CPU_TIME_BUDGET_S / WAN_VIDEO_CPU_TIME_BUDGET_S.
        The largest parameters satisfying BOTH constraints win — the requested
        size first, then the fallback chain.

        Returns (override, notice, error):
          - ({}, None, None)        → proceed with requested params
          - (fallback_params, notice, None) → degrade to smaller resolution + notice
          - (None, None, error)     → impossible even with fallback
        """
        from app.resource_manager import get_resource_manager

        rm = get_resource_manager()
        if rm.hardware.platform != "cpu":
            return {}, None, None

        if self.backend == BACKEND_WAN and not self.allow_cpu:
            # Measured: the smallest useful Wan clip (512×288×49) needs more
            # than 12 GB of RAM and a 5-second 480p clip takes ~90 minutes on
            # 8 threads. LTX is the CPU backend; Wan needs a GPU.
            return None, None, self._(
                "Wan video generation requires a GPU. This host runs on CPU — "
                "switch VIDEO_BACKEND to 'ltx' or enable WAN_VIDEO_ALLOW_CPU=1 "
                "to attempt it anyway (very slow).",
                lang,
            )

        # Generation happens in the video container, so the binding memory
        # constraint is the SMALLER of host free RAM and that container's
        # memory cap. A plan fitting host RAM but exceeding the container cap
        # would end in an OOM-kill of the container despite available host memory.
        host_available_mb = int(rm._detect_available_ram_mb())
        limit_env = "WAN_VIDEO_RAM_LIMIT_MB" if self.backend == BACKEND_WAN else "LTX_VIDEO_RAM_LIMIT_MB"
        container_limit_mb = int(os.environ.get(limit_env, str(host_available_mb)))
        available_mb = min(host_available_mb, container_limit_mb)
        budget_env = (
            "WAN_VIDEO_CPU_TIME_BUDGET_S" if self.backend == BACKEND_WAN else "LTX_VIDEO_CPU_TIME_BUDGET_S"
        )
        time_budget_s = int(os.environ.get(budget_env, "0")) or max(1, int(self.timeout * 0.85))

        defaults = self.default_params
        req_width = int(prompt_data.get("width", defaults["width"]))
        req_height = int(prompt_data.get("height", defaults["height"]))
        req_frames = int(prompt_data.get("num_frames", defaults["num_frames"]))
        request_params = {"width": req_width, "height": req_height, "num_frames": req_frames}
        if self.backend == BACKEND_WAN:
            candidates = [request_params, *[dict(p) for p in WAN_FALLBACK_PARAMS]]
        else:
            candidates = [request_params, *self.CPU_FALLBACK_PARAMS]

        def fits(params: dict[str, int]) -> bool:
            ram_ok = (
                self.estimate_peak_ram_mb(
                    params["width"], params["height"], params["num_frames"], backend=self.backend
                )
                + VIDEO_RAM_SAFETY_MARGIN_MB
                <= available_mb
            )
            time_ok = (
                self.estimate_cpu_generation_time_s(
                    params["width"],
                    params["height"],
                    params["num_frames"],
                    steps=self.steps,
                    backend=self.backend,
                )
                <= time_budget_s
            )
            return ram_ok and time_ok

        for candidate in candidates:
            if not fits(candidate):
                continue
            if candidate is request_params:
                return {}, None, None
            fps_units = self._("fps", lang)
            params = (
                f"{candidate['width']}×{candidate['height']}×{candidate['num_frames']}, "
                f"{candidate.get('frame_rate', 24)} {fps_units}"
            )
            estimate_s = self.estimate_cpu_generation_time_s(
                req_width, req_height, req_frames, steps=self.steps, backend=self.backend
            )
            if estimate_s > time_budget_s:
                notice = self._(
                    "Estimated generation time ({estimate_s}s) exceeds the limit ({budget_s}s). "
                    "Generating at lower resolution: {params}.",
                    lang,
                ).format(estimate_s=estimate_s, budget_s=time_budget_s, params=params)
            else:
                notice = self._("Not enough memory. Generating at lower resolution: {params}.", lang).format(
                    params=params
                )
            return dict(candidate), notice, None

        # Nothing fits: report the binding constraint of the smallest candidate —
        # the reason the "last resort" step itself is impossible.
        smallest = candidates[-1]
        ram_ok_smallest = (
            self.estimate_peak_ram_mb(
                smallest["width"], smallest["height"], smallest["num_frames"], backend=self.backend
            )
            + VIDEO_RAM_SAFETY_MARGIN_MB
            <= available_mb
        )
        if not ram_ok_smallest:
            error = self._(
                "Not enough memory. Generation is not possible even at lower resolution.",
                lang,
            )
        else:
            estimate_s = self.estimate_cpu_generation_time_s(
                req_width, req_height, req_frames, steps=self.steps, backend=self.backend
            )
            error = self._(
                "Estimated generation time ({estimate_s}s) exceeds the limit ({budget_s}s) "
                "even at the lowest resolution.",
                lang,
            ).format(estimate_s=estimate_s, budget_s=time_budget_s)
        return None, None, error

    def _resolve_use_gpu(self, rm, prompt_data: dict[str, Any] | None = None) -> bool:
        """Determine if GPU can be used. Returns False if VRAM insufficient.

        The threshold is the backend's own peak plus a 1 GB safety margin: LTX
        measures ~3.1 GB, Wan needs the requested resolution's footprint.
        """
        if not rm.hardware.cuda_detected:
            return False
        rm._poll_vram()
        available = rm.hardware.available_vram_mb
        needed = self.estimate_vram_mb(prompt_data) + self.vram_margin_mb
        if available > 0 and available < needed:
            self.logger.warning(f"VRAM too low for video ({available}MB available, ~{needed}MB needed)")
            return False
        return True

    def apply_backend_limits(
        self, prompt_data: dict[str, Any], lang: str = "ru"
    ) -> tuple[dict[str, Any], str | None]:
        """Clamp requested parameters to what the backend was trained for.

        Wan 2.2 5B was trained on 121 frames (5 s at 24 fps). A 10-second
        request would run outside that range — motion degrades and generation
        time doubles — so the clip is shortened with a user-facing notice.
        LTX accepts up to 240 frames (10 s) as is.
        """
        self._refresh_backend()
        params = dict(prompt_data)
        if self.backend != BACKEND_WAN:
            return params, None

        requested = int(params.get("num_frames", WAN_DEFAULT_PARAMS["num_frames"]))
        if requested <= WAN_MAX_FRAMES:
            return params, None

        fps = int(params.get("frame_rate", WAN_DEFAULT_PARAMS["frame_rate"])) or 24
        params["num_frames"] = WAN_MAX_FRAMES
        max_seconds = max(1, round(WAN_MAX_FRAMES / fps))
        requested_seconds = max(1, round(requested / fps))
        notice = self._(
            "Wan generates clips up to {max_seconds} seconds — shortened the video "
            "from {requested_seconds} s to {actual_seconds} s.",
            lang,
        ).format(max_seconds=max_seconds, requested_seconds=requested_seconds, actual_seconds=max_seconds)
        self.logger.info(f"Wan frame cap: {requested} → {WAN_MAX_FRAMES} frames ({notice})")
        return params, notice

    def _build_payload(
        self,
        prompt_data: dict[str, Any],
        image_data: str | None,
        user_id: str | None,
        session_id: str | None,
        task_id: str | None,
    ) -> dict[str, Any]:
        """Build the backend request body (both wrappers share this contract)."""
        defaults = self.default_params
        payload: dict[str, Any] = {
            "prompt": prompt_data.get("prompt", ""),
            "negative_prompt": prompt_data.get("negative_prompt", DEFAULT_VIDEO_NEGATIVE_PROMPT),
            "width": prompt_data.get("width", defaults["width"]),
            "height": prompt_data.get("height", defaults["height"]),
            "num_frames": prompt_data.get("num_frames", defaults["num_frames"]),
            "frame_rate": prompt_data.get("frame_rate", defaults["frame_rate"]),
            "seed": prompt_data.get("seed", -1),
            "image_data": image_data,
            "user_id": user_id,
            "session_id": session_id,
            "task_id": task_id,
        }
        if self.backend == BACKEND_WAN:
            # Sampling lives in the sd.cpp command line, not in a pipeline YAML.
            payload["steps"] = self.steps
            payload["cfg_scale"] = self.cfg_scale
            payload["flow_shift"] = self.flow_shift
        return payload

    def _cap_ltx_to_vram_tier(self, rm, prompt_data: dict[str, Any]) -> dict[str, Any]:
        """LTX: cap the clip on 8/10 GB cards or when VRAM stays fragmented.

        Default policy: 240 frames at 768×512 (10-sec video @ 24fps).
        Cap to 120 frames at 512×512 only if:
          (a) total_vram_mb < 10000 (8/10 GB tier GPU), OR
          (b) available_vram_mb < 6000 (12+ GB tier, fragmented after multimodal unload)
        """
        total_vram = rm.hardware.total_vram_mb
        if isinstance(total_vram, int) and total_vram > 0 and total_vram < 10000:
            reason = "VRAM tier 8GB"
        elif isinstance(rm.hardware.available_vram_mb, int) and 0 < rm.hardware.available_vram_mb < 6000:
            reason = f"VRAM soft-cap (available={rm.hardware.available_vram_mb}MB)"
        else:
            return prompt_data

        old_w = prompt_data.get("width", LTX_DEFAULT_PARAMS["width"])
        old_h = prompt_data.get("height", LTX_DEFAULT_PARAMS["height"])
        old_frames = prompt_data.get("num_frames", LTX_DEFAULT_PARAMS["num_frames"])
        prompt_data["width"] = min(old_w, 512)
        prompt_data["height"] = min(old_h, 512)
        prompt_data["num_frames"] = min(old_frames, 120)
        if (old_w, old_h, old_frames) != (prompt_data["width"], prompt_data["height"], prompt_data["num_frames"]):
            self.logger.info(
                f"{reason}: capped video from {old_w}×{old_h}×{old_frames}f "
                f"to {prompt_data['width']}×{prompt_data['height']}×{prompt_data['num_frames']}f"
            )
        return prompt_data

    def _fit_wan_to_vram(self, rm, prompt_data: dict[str, Any]) -> dict[str, Any]:
        """Wan: step down the preset chain until the clip fits the free VRAM.

        Wan's footprint grows with resolution (480p → ~11 GB, 720p → beyond a
        16 GB card), so the same clip has three usable sizes. The chain never
        upscales a smaller request. If nothing fits, the clip is left untouched
        and the VRAM gate above reports the refusal with real numbers.
        """
        free = rm.hardware.available_vram_mb
        total = rm.hardware.total_vram_mb
        # The gate empties the card before generation, so the budget is what the
        # card can offer once the LLM is gone — not what is free right now.
        if isinstance(total, int) and total > 0:
            # 500 MB covers the idle desktop context (measured 337 MB) instead of
            # the 1 GB reserve the LTX path uses.
            free = max(free if isinstance(free, int) else 0, total - 500)
        if not isinstance(free, int) or free <= 0:
            return prompt_data

        requested_w = int(prompt_data.get("width", WAN_DEFAULT_PARAMS["width"]))
        requested_frames = int(prompt_data.get("num_frames", WAN_DEFAULT_PARAMS["num_frames"]))
        for preset in [None, *WAN_FALLBACK_PARAMS]:
            params = dict(prompt_data) if preset is None else {**prompt_data, **preset}
            if preset is not None and (
                params["width"] > requested_w or params["num_frames"] > requested_frames
            ):
                continue
            if self.estimate_vram_mb(params, total_vram_mb=total) + self.vram_margin_mb <= free:
                if preset is not None:
                    self.logger.info(
                        f"Wan VRAM fit ({free}MB free): reduced video to "
                        f"{params['width']}×{params['height']}×{params['num_frames']}f"
                    )
                return params
        return prompt_data

    def generate_video(
        self,
        prompt_data: dict[str, Any],
        image_data: str | None = None,
        lang: str = "ru",
        user_id: str | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Generate a video through the active backend.

        Before starting, unloads llama.cpp models from VRAM to avoid OOM; after
        the run it frees the video pipeline and re-unloads anything respawned.
        """
        from app.resource_manager import get_resource_manager

        self._refresh_backend()
        rm = get_resource_manager()

        llamacpp_url = self.app.config.get("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")
        rm.unload_llamacpp_model(llamacpp_url)

        if self.backend == BACKEND_WAN:
            # Clamp the clip to the card BEFORE the VRAM gate: the gate waits for
            # the requested footprint, and a 720p request on a 16 GB card would
            # never see that much free VRAM — it would time out instead of
            # stepping down to a preset that fits.
            prompt_data = self._fit_wan_to_vram(rm, prompt_data)

        if rm.hardware.platform == "cpu":
            # CPU platform: no GPU memory to manage — the wrapper runs on device=cpu.
            self.logger.info("Video generation on CPU platform — skipping the VRAM gate")
        else:
            # CRITICAL: Verify llama-swap has NO models loaded (not just VRAM check).
            # A VRAM-only check with threshold ~10GB can pass while multimodal
            # (~5GB) is still loaded on a 15GB GPU (15-5=10 ≥ 10 → false positive).
            # Threshold = measured ltx-video peak + 1 GB safety margin, consistent
            # with _resolve_use_gpu() below.
            swap_url = self.app.config.get("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")
            # A busy GPU is waited out, not reported as a failure: a hardcoded
            # 15 s deadline failed video generation while a 12 GB reasoning model
            # was still unloading (same class of bug as ensure_vram_for).
            timeout = int(self.app.config.get("VRAM_WAIT_TIMEOUT_S", 120))
            deadline = time.time() + timeout
            video_needed = self.estimate_vram_mb(prompt_data) + self.vram_margin_mb
            while time.time() < deadline:
                rm._poll_vram()
                try:
                    resp = requests.get(f"{swap_url.rstrip('/')}/running", timeout=5)
                    loaded = resp.json().get("running", []) if resp.status_code == 200 else ["?"]
                except Exception:
                    loaded = ["?"]
                free = rm.hardware.available_vram_mb
                if not isinstance(free, int):
                    free = 0
                if len(loaded) == 0 and free >= video_needed:
                    self.logger.info(f"VRAM ready: {free}MB free, 0 LLM models loaded, need ≥{video_needed}MB")
                    break
                self.logger.info(
                    f"VRAM: {free}MB free, {len(loaded)} LLM model(s) loaded, "
                    f"need ≥{video_needed}MB — waiting for full unload..."
                )
                time.sleep(2)
            else:
                err_msg = self._(
                    "Video generation requires GPU; available VRAM ({free} MB) "
                    "is below safe threshold ({need} MB). Please try again later or simplify the request.",
                    lang,
                ).format(free=free, need=video_needed)
                self.logger.warning(f"VRAM wait timeout ({timeout}s) — free={free}MB, models={loaded}")
                return {"success": False, "error": err_msg}

            use_gpu = self._resolve_use_gpu(rm, prompt_data)
            if not use_gpu:
                err_msg = self._(
                    "Video generation requires GPU; available VRAM ({free} MB) "
                    "is below safe threshold ({need} MB). Please try again later or simplify the request.",
                    lang,
                ).format(
                    free=rm.hardware.available_vram_mb,
                    need=int(self.estimate_vram_mb(prompt_data) + self.vram_margin_mb),
                )
                self.logger.warning(f"Video generation skipped: {err_msg}")
                return {"success": False, "error": err_msg}
            self.logger.info(
                f"VRAM: {rm.hardware.available_vram_mb}MB available, "
                f"~{self.estimate_vram_mb(prompt_data)}MB needed ({self.backend}) — using GPU"
            )

        if self.backend != BACKEND_WAN:
            prompt_data = self._cap_ltx_to_vram_tier(rm, prompt_data)

        # Resize large source images to avoid OOM and reduce network transfer
        source_limit = WAN_MAX_VIDEO_SOURCE_SIZE if self.backend == BACKEND_WAN else MAX_VIDEO_SOURCE_SIZE
        image_data, resized_info = resize_video_source_image(image_data, source_limit)

        try:
            rm.mark_video_busy()
            payload = self._build_payload(prompt_data, image_data, user_id, session_id, task_id)

            self.logger.info(
                f"Sending request to {self.service_name} ({self.backend}/{self.model_type}), "
                f"width={payload['width']}, height={payload['height']}, "
                f"frames={payload['num_frames']}, timeout: {self.timeout}s"
            )
            self.logger.info(f"Video prompt: '{payload['prompt'][:100]}...'")

            response = requests.post(
                f"{self.wrapper_url.rstrip('/')}/v1/video/generations",
                json=payload,
                timeout=self.timeout,
            )

            if response.status_code == 200:
                result = response.json()
                if "error" in result:
                    err_msg = result["error"]
                    self.logger.error(f"{self.service_name} error: {err_msg}")
                    return {"success": False, "error": err_msg}

                if not result.get("video_data"):
                    return {"success": False, "error": self._("Video service returned no video data", lang)}

                filename = result.get(
                    "file_name",
                    f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.mp4",
                )

                return {
                    "success": True,
                    "video_data": result["video_data"],
                    "file_name": filename,
                    "file_size": result.get("file_size", 0),
                    "file_type": "video/mp4",
                    "generation_time": result.get("generation_time", 0),
                    "seed": result.get("seed", 0),
                    "metadata": result.get("metadata", {}),
                    "preview_data": result.get("preview_data", ""),
                    "resized": resized_info["resized"],
                    "original_size": resized_info["original_size"],
                    "new_size": resized_info["new_size"],
                }
            else:
                error_body = response.text[:500]
                self.logger.error(f"{self.service_name} error: {response.status_code} - {error_body}")
                try:
                    err_data = response.json()
                    err_msg = err_data.get("error", error_body)
                except Exception:
                    err_msg = error_body
                if "Internal Server Error" in err_msg:
                    # Gunicorn served its default HTML error page (worker crashed).
                    # Do not leak raw English text to the user — use the localized key.
                    err_msg = self._("Internal server error", lang)
                template = self._("Video generation failed: {error}", lang)
                return {"success": False, "error": template.format(error=err_msg)}

        except requests.exceptions.Timeout:
            self.logger.error(f"Timeout ({self.timeout}s) during video generation")
            template = self._("Video generation timeout ({timeout}s)", lang)
            return {"success": False, "error": template.format(timeout=self.timeout)}
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": self._("Could not connect to video generation service", lang)}
        except Exception as e:
            self.logger.error(f"Error calling {self.service_name}: {e}")
            return {"success": False, "error": f"{self._('Error', lang)}: {str(e)}"}
        finally:
            rm.mark_video_idle()
            # Re-unload any LLM processes that may have been restarted
            # during video generation (e.g. by config reload in admin panel),
            # and let GPU state settle before next request
            unload_success = rm.unload_llamacpp_model(llamacpp_url)
            if not unload_success:
                time.sleep(2)
                rm.unload_llamacpp_model(llamacpp_url)

            time.sleep(1)
            rm.log_gpu_memory("video-post-cleanup")
