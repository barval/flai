# modules/video.py
"""
Module for video generation via LTX-Video.

Uses ltx-wrapper HTTP API (ltx_wrapper.py running in ltxvideo container)
which runs LTX-Video pipeline natively with PyTorch.
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


class VideoModule(TranslationMixin):
    """Module for video generation via LTX-Video wrapper."""

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

        max_retries = 1
        retry_delay = 1

        for attempt in range(1, max_retries + 1):
            if self.check_availability():
                break
            if attempt < max_retries:
                self.logger.warning(
                    f"ltx-wrapper not ready (attempt {attempt}/{max_retries}), retrying in {retry_delay}s..."
                )
                import time

                time.sleep(retry_delay)
            else:
                self.logger.warning(f"ltx-wrapper not available after {max_retries} attempts")

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
        max_video_inpaint_size = 768
        resized_info: dict[str, Any] = {"resized": False, "original_size": None, "new_size": None}
        if image_data:
            try:
                img_bytes = base64.b64decode(image_data)
                img = Image.open(BytesIO(img_bytes))
                w, h = img.size
                if w > max_video_inpaint_size or h > max_video_inpaint_size:
                    ratio = max_video_inpaint_size / max(w, h)
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
                    self.logger.info(f"Video source image resized from {w}x{h} to {new_w}x{new_h}")
            except Exception as e:
                self.logger.warning(f"Failed to resize video source image: {e}")

        try:
            payload = {
                "prompt": prompt_data.get("prompt", ""),
                "negative_prompt": prompt_data.get(
                    "negative_prompt", "worst quality, inconsistent motion, blurry, jittery, distorted"
                ),
                "width": prompt_data.get("width", 768),
                "height": prompt_data.get("height", 512),
                "num_frames": prompt_data.get("num_frames", 240),
                "frame_rate": prompt_data.get("frame_rate", 24),
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

            # Clear CUDA cache to prevent fragmentation after video generation
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    torch.cuda.synchronize()
                    self.logger.info("CUDA cache cleared after video generation")
            except ImportError:
                pass

            time.sleep(1)
            rm.log_gpu_memory("video-post-cleanup")
