# modules/video.py
"""
Module for video generation via Wan2.2-TI2V-5B-Turbo.

Uses the sd-wrapper HTTP API (sd_wrapper.py running in the flai-sd container)
which runs sd-cli -M vid_gen natively with the Wan2.2 pipeline.
"""

import base64
import logging
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
    "height": 448,
    "num_frames": 121,
    "frame_rate": 24,
}

# RAM budgeting for CPU generation (no VRAM to rely on).
# Wan2.2-TI2V-5B-Turbo: diffusion Q4_K_M ~3.6 GB + UMT5-XXL ~6 GB + VAE ~1.5 GB
# ≈ 11 GB static. Activations scale ~linearly with latent volume
# (spatial//32 × spatial//32 × temporal//4).
VIDEO_RAM_STATIC_MB = 11_000
VIDEO_RAM_ACTIVATIONS_MB_PER_UNIT = 2.0
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


def center_crop_image_to_preset(
    image_data: str, width: int, height: int, max_source_size: int = MAX_VIDEO_SOURCE_SIZE
) -> tuple[str, tuple[int, int], tuple[int, int]]:
    """Downscale to <= max_source_size longest side, then center-crop and
    resize the image to exactly width×height (the Wan preset).

    Returns (re-encoded base64 image, original_size, new_size). Output matches
    the preset dims exactly so sd-cli's --init-img lines up with -W/-H.
    """
    try:
        img_bytes = base64.b64decode(image_data)
        img = Image.open(BytesIO(img_bytes))
        orig_w, orig_h = img.size
        if img.mode in ("RGBA", "LA", "P"):
            rgb_img = Image.new("RGB", img.size, (255, 255, 255))
            rgb_img.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
            img = rgb_img  # type: ignore[assignment]

        # 1. Downscale to <= max_source_size on the longest side (proportional).
        if max(orig_w, orig_h) > max_source_size:
            ratio = max_source_size / max(orig_w, orig_h)
            img = img.resize((int(orig_w * ratio), int(orig_h * ratio)), Image.Resampling.LANCZOS)  # type: ignore[assignment]

        # 2. Center-crop to the exact preset aspect.
        cw, ch = img.size
        target_ratio = width / height
        cur_ratio = cw / ch
        if cur_ratio > target_ratio:
            new_w = int(ch * target_ratio)
            left = (cw - new_w) // 2
            img = img.crop((left, 0, left + new_w, ch))  # type: ignore[assignment]
        elif cur_ratio < target_ratio:
            new_h = int(cw / target_ratio)
            top = (ch - new_h) // 2
            img = img.crop((0, top, cw, top + new_h))  # type: ignore[assignment]

        # 3. Resize to the exact preset dimensions.
        if img.size != (width, height):
            img = img.resize((width, height), Image.Resampling.LANCZOS)  # type: ignore[assignment]

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return base64.b64encode(buf.getvalue()).decode("utf-8"), (orig_w, orig_h), (width, height)
    except Exception:
        return image_data, (0, 0), (0, 0)


def sanitize_video_params(prompt_data: dict[str, Any]) -> dict[str, Any]:
    """Enforce Wan2.2 constraints: num_frames<=121, dims % 32 == 0.

    num_frames is also snapped to the 4k+1 sequence the Wan temporal
    pipeline accepts (1, 5, 9, ... 121). Returns a new dict; does not mutate
    the input.
    """
    d = dict(prompt_data)
    d["num_frames"] = min(int(d.get("num_frames", 121)), 121)
    d["num_frames"] = max(1, (d["num_frames"] - 1) // 4 * 4 + 1)
    for axis in ("width", "height"):
        d[axis] = max(32, int(d.get(axis, {axis: 768, "height": 448}[axis])) // 32 * 32)
    return d


class VideoModule(TranslationMixin):
    """Module for video generation via Wan2.2 (sd-wrapper)."""

    CPU_FALLBACK_PARAMS: list[dict[str, int]] = [
        {"width": 384, "height": 256, "num_frames": 81, "frame_rate": 24},
        {"width": 320, "height": 192, "num_frames": 57, "frame_rate": 24},
        {"width": 256, "height": 160, "num_frames": 49, "frame_rate": 24},
    ]

    # Aspect-class presets for image-to-video (I2V). The source image is
    # first downscaled to <= MAX_VIDEO_SOURCE_SIZE on its longest side, then
    # center-cropped to the chosen preset's aspect. Frame counts are the
    # Wan 4k+1 sequence; all dims are multiples of 32.
    GPU_ASPECT_PRESETS: dict[str, dict[str, int]] = {
        "landscape": {"width": 768, "height": 448, "num_frames": 121, "frame_rate": 24},
        "portrait": {"width": 448, "height": 768, "num_frames": 121, "frame_rate": 24},
        "square": {"width": 512, "height": 512, "num_frames": 121, "frame_rate": 24},
    }

    # CPU cascades per aspect class: each list is ordered best -> smallest,
    # tried in order until the RAM estimate fits available memory.
    CPU_ASPECT_PRESETS: dict[str, list[dict[str, int]]] = {
        "landscape": [
            {"width": 384, "height": 256, "num_frames": 81, "frame_rate": 24},
            {"width": 320, "height": 192, "num_frames": 57, "frame_rate": 24},
            {"width": 256, "height": 160, "num_frames": 49, "frame_rate": 24},
        ],
        "portrait": [
            {"width": 256, "height": 384, "num_frames": 81, "frame_rate": 24},
            {"width": 192, "height": 320, "num_frames": 57, "frame_rate": 24},
            {"width": 160, "height": 256, "num_frames": 49, "frame_rate": 24},
        ],
        "square": [
            {"width": 256, "height": 256, "num_frames": 81, "frame_rate": 24},
            {"width": 224, "height": 224, "num_frames": 57, "frame_rate": 24},
            {"width": 192, "height": 192, "num_frames": 49, "frame_rate": 24},
        ],
    }

    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.wrapper_url = None
        self.available = False
        self.timeout = 1200
        self.model_type = "wan2.2-ti2v-5b-turbo"
        self.multimodal_module = None

        if app:
            self.init_app(app)

    def init_app(self, app):
        """Initialize module with Flask app"""
        self.app = app
        self.wrapper_url = app.config.get("VIDEO_WRAPPER_URL", "http://flai-sd:7861")
        self.timeout = app.config.get("VIDEO_TIMEOUT", 1200)
        self.model_type = app.config.get("VIDEO_MODEL", "wan2.2-ti2v-5b-turbo")

        self.logger.info(
            f"VideoModule initialized with wrapper URL: {self.wrapper_url}, "
            f"model: {self.model_type}, timeout: {self.timeout}s"
        )

        self.available = wait_for_service(self.check_availability, self.logger, "sd-wrapper", retries=1, delay=1)

        if self.available:
            self.logger.info(f"VideoModule initialized and available. Timeout: {self.timeout}s")
        else:
            self.logger.warning(
                f"VideoModule initialized, but sd-wrapper unavailable ({self.wrapper_url}). Will retry on each request."
            )

    def set_multimodal_module(self, multimodal_module):
        """Set reference to multimodal module (for prompt generation)."""
        self.multimodal_module = multimodal_module

    def check_availability(self):
        """Check sd-wrapper availability."""
        if not self.wrapper_url:
            self.logger.error("VIDEO_WRAPPER_URL not configured")
            return False

        try:
            response = requests.get(f"{self.wrapper_url.rstrip('/')}/health", timeout=5)
            if response.status_code == 200:
                self.available = True
                return True
            else:
                self.logger.warning(f"sd-wrapper returned status {response.status_code}")
                self.available = False
                return False
        except Exception as e:
            self.logger.error(f"Error connecting to sd-wrapper: {e}")
            self.available = False
            return False

    @staticmethod
    def _estimate_video_vram_mb() -> int:
        """Dynamic VRAM estimate for the video model in MB.

        Reads the estimate from resource_manager.estimate_video_vram_needed()
        (measured peaks / per-model constants). Falls back to 8000 MB if the
        lookup fails.
        """
        try:
            from app.resource_manager import get_resource_manager

            return get_resource_manager().estimate_video_vram_needed()
        except Exception:
            return 8000

    @classmethod
    def estimate_peak_ram_mb(cls, width: int, height: int, num_frames: int) -> int:
        """Estimate peak container RAM for a Wan video generation on CPU (MB).

        Wan latent volume: spatial /32 each axis, temporal (frames-1)//4+1.
        Static model footprint dominates (~11 GB); activations grow ~linearly
        with latent volume.
        """
        spatial = max(1, width // 32) * max(1, height // 32)
        temporal = max(1, (num_frames - 1) // 4 + 1)
        units = spatial * temporal
        return int(VIDEO_RAM_STATIC_MB + units * VIDEO_RAM_ACTIVATIONS_MB_PER_UNIT)

    @staticmethod
    def _detect_aspect_class(width: int, height: int) -> str:
        """Map a source image aspect to a Wan preset class."""
        if width <= 0 or height <= 0:
            return "landscape"
        aspect = width / height
        if aspect > 1.2:
            return "landscape"
        if aspect < 0.8:
            return "portrait"
        return "square"

    def pick_i2v_preset(
        self, src_width: int, src_height: int, platform: str = "gpu", lang: str = "ru"
    ) -> tuple[dict[str, int], str | None, str | None]:
        """Pick the I2V preset (Wan dims + frames) for a source image.

        GPU: fixed 768×448 / 448×768 / 512×512 @121.
        CPU: aspect cascade degraded to fit available RAM.

        Returns (preset, notice|None, error|None).
        """
        cls = self._detect_aspect_class(src_width, src_height)
        if platform == "cpu":
            from app.resource_manager import get_resource_manager

            rm = get_resource_manager()
            available_mb = int(rm._detect_available_ram_mb())
            cascade = self.CPU_ASPECT_PRESETS[cls]
            for preset in cascade:
                if (
                    self.estimate_peak_ram_mb(preset["width"], preset["height"], preset["num_frames"])
                    + VIDEO_RAM_SAFETY_MARGIN_MB
                    <= available_mb
                ):
                    if preset is not cascade[0]:
                        notice = self._("Not enough memory. Generating at lower resolution: {params}.", lang).format(
                            params=f"{preset['width']}×{preset['height']}×{preset['num_frames']}, "
                            f"{preset.get('frame_rate', 24)} {self._('fps', lang)}"
                        )
                        return dict(preset), notice, None
                    return dict(preset), None, None
            error = self._("Not enough memory. Generation is not possible even at lower resolution.", lang)
            return dict(cascade[-1]), None, error
        return dict(self.GPU_ASPECT_PRESETS[cls]), None, None

    def plan_cpu_generation(
        self, prompt_data: dict[str, Any], lang: str = "ru"
    ) -> tuple[dict[str, Any] | None, str | None, str | None]:
        """Decide video parameters on CPU based on available RAM.

        Returns (override, notice, error):
          - ({}, None, None)        → proceed with requested params
          - (fallback_params, notice, None) → degrade to smaller resolution + notice
          - (None, None, error)     → impossible even with fallback
        """
        from app.resource_manager import get_resource_manager

        rm = get_resource_manager()
        if rm.hardware.platform != "cpu":
            return {}, None, None

        available_mb = int(rm._detect_available_ram_mb())
        req_width = int(prompt_data.get("width", 768))
        req_height = int(prompt_data.get("height", 448))
        req_frames = int(prompt_data.get("num_frames", 121))

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

        Uses a 1000 MB safety margin above the estimated video peak so we do
        not false-trigger "forcing CPU" on 16 GB GPUs.
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
        """Generate video via the Wan sd-wrapper.
        Before starting, unloads llama.cpp model from VRAM to avoid OOM.
        """
        from app.resource_manager import get_resource_manager

        rm = get_resource_manager()
        use_gpu = False

        llamacpp_url = self.app.config.get("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")
        rm.unload_llamacpp_model(llamacpp_url)

        if rm.hardware.platform == "cpu":
            # CPU platform: no GPU memory to manage — sd-wrapper runs with te=cpu.
            self.logger.info("Video generation on CPU platform — skipping the VRAM gate")
        else:
            # CRITICAL: Verify llama-swap has NO models loaded (not just VRAM check).
            # A VRAM-only check with threshold ~10GB can pass while multimodal
            # (~5GB) is still loaded on a 15GB GPU (15-5=10 ≥ 10 → false positive).
            # Threshold = video pipeline estimate + 1 GB safety margin, consistent
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

        i2v_notice: str | None = None

        # I2V: pick the preset from the source image aspect (GPU fixed presets
        # or CPU RAM cascade), downscale to <= max source size and center-crop
        # the image to the preset aspect. The wrapper no longer derives dims.
        if image_data:
            try:
                img = Image.open(BytesIO(base64.b64decode(image_data)))
                src_w, src_h = img.size
            except Exception as e:
                self.logger.warning(f"Failed to read source image for I2V: {e}")
                src_w, src_h = 0, 0
            platform = "cpu" if rm.hardware.platform == "cpu" else "gpu"
            i2v_preset, i2v_notice, i2v_error = self.pick_i2v_preset(src_w, src_h, platform=platform, lang=lang)
            if i2v_error:
                return {"success": False, "error": i2v_error}
            prompt_data["width"] = i2v_preset["width"]
            prompt_data["height"] = i2v_preset["height"]
            prompt_data["num_frames"] = i2v_preset["num_frames"]
            prompt_data["frame_rate"] = i2v_preset.get("frame_rate", 24)
            image_data, orig_size, new_size = center_crop_image_to_preset(
                image_data, prompt_data["width"], prompt_data["height"]
            )
            resized_info = {
                "resized": max(src_w, src_h) > MAX_VIDEO_SOURCE_SIZE,
                "original_size": orig_size,
                "new_size": new_size,
            }
            if i2v_notice:
                self.logger.info(f"I2V preset notice: {i2v_notice}")
            self.logger.info(
                f"I2V: source {src_w}x{src_h} -> preset {prompt_data['width']}x{prompt_data['height']}"
                f"x{prompt_data['num_frames']}, cropped image {orig_size} -> {new_size}"
            )
        else:
            # Wan preset chain: max 121 frames (4k+1 snapping), dims % 32 == 0.
            prompt_data = sanitize_video_params(prompt_data)

            # Cap to the first degradation preset (640×384@121) only on low-VRAM
            # tiers (total VRAM < 10000 MB, i.e. 8/10 GB GPUs).
            total_vram = rm.hardware.total_vram_mb
            if isinstance(total_vram, int) and total_vram > 0 and total_vram < 10000:
                old_w = prompt_data["width"]
                old_h = prompt_data["height"]
                prompt_data["width"] = min(old_w, 640)
                prompt_data["height"] = min(old_h, 384)
                if (old_w, old_h) != (prompt_data["width"], prompt_data["height"]):
                    self.logger.info(
                        f"VRAM tier 8GB: capped video from {old_w}×{old_h}×{prompt_data['num_frames']}f "
                        f"to {prompt_data['width']}×{prompt_data['height']}×{prompt_data['num_frames']}f"
                    )

            # Resize large T2V source images (none in practice) defensively.
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
                "use_gpu": use_gpu,
                "image_data": image_data,
                "user_id": user_id,
                "session_id": session_id,
                "task_id": task_id,
            }

            self.logger.info(
                f"Sending request to sd-wrapper ({self.model_type}), "
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
                    self.logger.error(f"sd-wrapper error: {err_msg}")
                    return {"success": False, "error": err_msg}

                if not result.get("video_data"):
                    return {"success": False, "error": self._("video service returned no video data", lang)}

                filename = result.get(
                    "file_name",
                    f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.webm",
                )

                return {
                    "success": True,
                    "video_data": result["video_data"],
                    "file_name": filename,
                    "file_size": result.get("file_size", 0),
                    "file_type": result.get("file_type", "video/webm"),
                    "generation_time": result.get("generation_time", 0),
                    "seed": result.get("seed", 0),
                    "metadata": result.get("metadata", {}),
                    "resized": resized_info["resized"],
                    "original_size": resized_info["original_size"],
                    "new_size": resized_info["new_size"],
                    **({"notice": i2v_notice} if image_data and i2v_notice else {}),
                }
            else:
                error_body = response.text[:500]
                self.logger.error(f"sd-wrapper error: {response.status_code} - {error_body}")
                try:
                    err_data = response.json()
                    err_msg = err_data.get("error", error_body)
                except Exception:
                    err_msg = error_body
                if "Internal Server Error" in err_msg:
                    # The wrapper served its default HTML error page (worker crashed).
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
            self.logger.error(f"Error calling sd-wrapper: {e}")
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
