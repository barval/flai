# modules/video.py
"""
Module for video generation via LTX-Video.

Uses ltx-wrapper HTTP API (ltx_wrapper.py running in ltxvideo container)
which runs LTX-Video pipeline natively with PyTorch.
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
from modules.base import wait_for_service

# Defaults shared with multimodal.py video parameter generation
DEFAULT_VIDEO_NEGATIVE_PROMPT = "worst quality, inconsistent motion, blurry, jittery, distorted"
DEFAULT_VIDEO_PARAMS = {
    "negative_prompt": DEFAULT_VIDEO_NEGATIVE_PROMPT,
    "width": 768,
    "height": 512,
    "num_frames": 240,
    "frame_rate": 24,
}

# RAM budgeting for CPU generation (no VRAM to rely on).
# Measured: 384×256×49 peaked at ~19.2 GiB cgroup usage (~17400 MB static
# models + ~2.2 GiB activations). Activations scale ~linearly with latent volume
# (spatial//32 × spatial//32 × temporal//8), fit to that measurement with margin.
VIDEO_RAM_STATIC_MB = 17_400
VIDEO_RAM_ACTIVATIONS_MB_PER_UNIT = 3.0
VIDEO_RAM_SAFETY_MARGIN_MB = 1024

MAX_VIDEO_SOURCE_SIZE = 768


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
    """Module for video generation via LTX-Video wrapper."""

    CPU_FALLBACK_PARAMS: list[dict[str, int]] = [
        {"width": 384, "height": 256, "num_frames": 120, "frame_rate": 12},
        {"width": 256, "height": 192, "num_frames": 57, "frame_rate": 6},
    ]

    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.wrapper_url = None
        self.available = False
        self.timeout = 600
        self.model_type = "ltxv-2b-0.9.8-distilled"
        self.multimodal_module = None

        if app:
            self.init_app(app)

    def init_app(self, app):
        """Initialize module with Flask app"""
        self.app = app
        self.wrapper_url = app.config.get("LTX_VIDEO_WRAPPER_URL", "http://flai-ltxvideo:7872")
        self.timeout = app.config.get("LTX_VIDEO_TIMEOUT", 600)
        self.model_type = app.config.get("LTX_VIDEO_MODEL", "ltxv-2b-0.9.8-distilled")

        self.logger.info(
            f"VideoModule initialized with wrapper URL: {self.wrapper_url}, "
            f"model: {self.model_type}, timeout: {self.timeout}s"
        )

        self.available = wait_for_service(self.check_availability, self.logger, "ltx-wrapper", retries=1, delay=1)

        if self.available:
            self.logger.info(f"VideoModule initialized and available. Timeout: {self.timeout}s")
        else:
            self.logger.warning(
                f"VideoModule initialized, but ltx-wrapper unavailable ({self.wrapper_url}). "
                "Will retry on each request."
            )

    def set_multimodal_module(self, multimodal_module):
        """Set reference to multimodal module (for prompt generation)."""
        self.multimodal_module = multimodal_module

    def check_availability(self):
        """Check ltx-wrapper availability."""
        if not self.wrapper_url:
            self.logger.error("LTX_VIDEO_WRAPPER_URL not configured")
            return False

        try:
            response = requests.get(f"{self.wrapper_url.rstrip('/')}/health", timeout=5)
            if response.status_code == 200:
                self.available = True
                return True
            else:
                self.logger.warning(f"ltx-wrapper returned status {response.status_code}")
                self.available = False
                return False
        except Exception as e:
            self.logger.error(f"Error connecting to ltx-wrapper: {e}")
            self.available = False
            return False

    @staticmethod
    def _estimate_video_vram_mb() -> int:
        """Dynamic VRAM estimate for LTX-Video model in MB.

        Reads the measured peak from model_vram_estimates (10+ measurements
        stored by resource_manager.measure_video_vram_peak). On our hardware
        the actual peak is ~3057 MB, not the 8000 MB the hardcoded value
        assumed. Falls back to 8000 MB if the DB query fails.
        """
        try:
            from app.resource_manager import get_resource_manager

            return get_resource_manager().estimate_video_vram_needed()
        except Exception:
            return 8000

    @classmethod
    def estimate_peak_ram_mb(cls, width: int, height: int, num_frames: int) -> int:
        """Estimate peak container RAM for a video generation on CPU (MB).

        LTX latent volume: spatial /32 each axis, temporal (+8 → +1 frame).
        Static model footprint dominates (~17.4 GB); activations grow ~linearly
        with latent volume — calibrated against a 384×256×49 probe (19.2 GiB peak).
        """
        spatial = max(1, width // 32) * max(1, height // 32)
        temporal = max(1, (num_frames - 2) // 8 + 1)
        units = spatial * temporal
        return int(VIDEO_RAM_STATIC_MB + units * VIDEO_RAM_ACTIVATIONS_MB_PER_UNIT)

    def plan_cpu_generation(
        self, prompt_data: dict[str, Any], lang: str = "ru"
    ) -> tuple[dict[str, Any] | None, str | None, str | None]:
        """Decide video parameters on CPU based on available RAM.

        Budget = min(host free RAM, ltxvideo container memory cap). The cap is
        read from LTX_VIDEO_RAM_LIMIT_MB (set in the CPU compose file).

        Returns (override, notice, error):
          - ({}, None, None)        → proceed with requested params
          - (fallback_params, notice, None) → degrade to smaller resolution + notice
          - (None, None, error)     → impossible even with fallback
        """
        from app.resource_manager import get_resource_manager

        rm = get_resource_manager()
        if rm.hardware.platform != "cpu":
            return {}, None, None

        # Generation happens in the ltxvideo container, so the binding
        # constraint is the SMALLER of host free RAM and that container's
        # memory cap (LTX_VIDEO_RAM_LIMIT_MB from docker-compose.cpu.yml).
        # A plan fitting host RAM but exceeding the container cap would end in
        # an OOM-kill of the container despite available host memory.
        host_available_mb = int(rm._detect_available_ram_mb())
        container_limit_mb = int(os.environ.get("LTX_VIDEO_RAM_LIMIT_MB", str(host_available_mb)))
        available_mb = min(host_available_mb, container_limit_mb)
        req_width = int(prompt_data.get("width", 768))
        req_height = int(prompt_data.get("height", 512))
        req_frames = int(prompt_data.get("num_frames", 240))

        if self.estimate_peak_ram_mb(req_width, req_height, req_frames) + VIDEO_RAM_SAFETY_MARGIN_MB <= available_mb:
            return {}, None, None

        for fb in self.CPU_FALLBACK_PARAMS:
            if self.estimate_peak_ram_mb(fb["width"], fb["height"], fb["num_frames"]) + VIDEO_RAM_SAFETY_MARGIN_MB <= (
                available_mb
            ):
                fps_units = self._("fps", lang)
                params = f"{fb['width']}×{fb['height']}×{fb['num_frames']}, {fb.get('frame_rate', 24)} {fps_units}"
                notice = self._("Not enough memory. Generating at lower resolution: {params}.", lang).format(
                    params=params
                )
                return dict(fb), notice, None

        error = self._(
            "Not enough memory. Generation is not possible even at lower resolution.",
            lang,
        )
        return None, None, error

    def _resolve_use_gpu(self, rm) -> bool:
        """Determine if GPU can be used. Returns False if VRAM insufficient.

        Buffer reduced 3000->1000 MB: measured ltx-video peak is 3057 MB,
        +1 GB for KV cache = 4057 MB threshold. With 1000 MB safety margin
        we no longer false-trigger "forcing CPU" on 16 GB GPUs.
        """
        if not rm.hardware.cuda_detected:
            return False
        rm._poll_vram()
        available = rm.hardware.available_vram_mb
        needed = self._estimate_video_vram_mb() + 1000
        if available > 0 and available < needed:
            self.logger.warning(f"VRAM too low for video ({available}MB available, ~{needed}MB needed)")
            return False
        return True

    def generate_video(
        self,
        prompt_data: dict[str, Any],
        image_data: str | None = None,
        lang: str = "ru",
        user_id: str | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Generate video via ltx-wrapper.
        Before starting, unloads llama.cpp model from VRAM to avoid OOM.
        """
        from app.resource_manager import get_resource_manager

        rm = get_resource_manager()

        llamacpp_url = self.app.config.get("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")
        rm.unload_llamacpp_model(llamacpp_url)

        if rm.hardware.platform == "cpu":
            # CPU platform: no GPU memory to manage — ltx-wrapper runs on device=cpu.
            self.logger.info("Video generation on CPU platform — skipping the VRAM gate")
        else:
            # CRITICAL: Verify llama-swap has NO models loaded (not just VRAM check).
            # A VRAM-only check with threshold ~10GB can pass while multimodal
            # (~5GB) is still loaded on a 15GB GPU (15-5=10 ≥ 10 → false positive).
            # Threshold = measured ltx-video peak + 1 GB safety margin, consistent
            # with _resolve_use_gpu() below.
            swap_url = self.app.config.get("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")
            deadline = time.time() + 15
            video_needed = rm.estimate_video_vram_needed() + 1000
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
                self.logger.warning(f"VRAM wait timeout (15s) — free={free}MB, models={loaded}")
                return {"success": False, "error": err_msg}

            use_gpu = self._resolve_use_gpu(rm)
            if not use_gpu:
                err_msg = self._(
                    "Video generation requires GPU; available VRAM ({free} MB) "
                    "is below safe threshold ({need} MB). Please try again later or simplify the request.",
                    lang,
                ).format(
                    free=rm.hardware.available_vram_mb,
                    need=int(self._estimate_video_vram_mb() + 1000),
                )
                self.logger.warning(f"Video generation skipped: {err_msg}")
                return {"success": False, "error": err_msg}
            self.logger.info(
                f"VRAM: {rm.hardware.available_vram_mb}MB available, ~{self._estimate_video_vram_mb()}MB needed — using GPU"
            )

        rm.mark_video_busy()

        # Cap video resolution ONLY when VRAM is insufficient.
        # Default policy: 240 frames at 768×512 (10-sec video @ 24fps).
        # Cap to 120 frames at 512×512 only if:
        #   (a) total_vram_mb < 10000 (8/10 GB tier GPU), OR
        #   (b) available_vram_mb < 6000 (12+ GB tier, fragmented after multimodal unload)
        total_vram = rm.hardware.total_vram_mb
        if isinstance(total_vram, int) and total_vram > 0 and total_vram < 10000:
            old_w = prompt_data.get("width", 768)
            old_h = prompt_data.get("height", 512)
            old_frames = prompt_data.get("num_frames", 240)
            prompt_data["width"] = min(old_w, 512)
            prompt_data["height"] = min(old_h, 512)
            prompt_data["num_frames"] = min(old_frames, 120)
            if (old_w, old_h, old_frames) != (prompt_data["width"], prompt_data["height"], prompt_data["num_frames"]):
                self.logger.info(
                    f"VRAM tier 8GB: capped video from {old_w}×{old_h}×{old_frames}f "
                    f"to {prompt_data['width']}×{prompt_data['height']}×{prompt_data['num_frames']}f"
                )
        elif isinstance(rm.hardware.available_vram_mb, int) and 0 < rm.hardware.available_vram_mb < 6000:
            old_frames = prompt_data.get("num_frames", 240)
            old_w = prompt_data.get("width", 768)
            old_h = prompt_data.get("height", 512)
            prompt_data["width"] = min(old_w, 512)
            prompt_data["height"] = min(old_h, 512)
            prompt_data["num_frames"] = min(old_frames, 120)
            if (old_w, old_h, old_frames) != (prompt_data["width"], prompt_data["height"], prompt_data["num_frames"]):
                self.logger.info(
                    f"VRAM soft-cap (available={rm.hardware.available_vram_mb}MB): "
                    f"reduced video from {old_w}×{old_h}×{old_frames}f "
                    f"to {prompt_data['width']}×{prompt_data['height']}×{prompt_data['num_frames']}f"
                )

        # Resize large source images to avoid OOM and reduce network transfer
        image_data, resized_info = resize_video_source_image(image_data)

        try:
            payload = {
                "prompt": prompt_data.get("prompt", ""),
                "negative_prompt": prompt_data.get("negative_prompt", DEFAULT_VIDEO_NEGATIVE_PROMPT),
                "width": prompt_data.get("width", DEFAULT_VIDEO_PARAMS["width"]),
                "height": prompt_data.get("height", DEFAULT_VIDEO_PARAMS["height"]),
                "num_frames": prompt_data.get("num_frames", DEFAULT_VIDEO_PARAMS["num_frames"]),
                "frame_rate": prompt_data.get("frame_rate", DEFAULT_VIDEO_PARAMS["frame_rate"]),
                "seed": prompt_data.get("seed", -1),
                "image_data": image_data,
                "user_id": user_id,
                "session_id": session_id,
                "task_id": task_id,
            }

            self.logger.info(
                f"Sending request to ltx-wrapper ({self.model_type}), "
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
                    self.logger.error(f"ltx-wrapper error: {err_msg}")
                    return {"success": False, "error": err_msg}

                if not result.get("video_data"):
                    return {"success": False, "error": self._("ltx-wrapper returned no video data", lang)}

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
                    "resized": resized_info["resized"],
                    "original_size": resized_info["original_size"],
                    "new_size": resized_info["new_size"],
                }
            else:
                error_body = response.text[:500]
                self.logger.error(f"ltx-wrapper error: {response.status_code} - {error_body}")
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
            self.logger.error(f"Error calling ltx-wrapper: {e}")
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
