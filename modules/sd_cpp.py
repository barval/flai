# modules/sd_cpp.py
"""
Module for image generation via stable-diffusion.cpp server.

Replaces ImageModule (Automatic1111). Communicates via OpenAI-compatible API:
  - POST /v1/images/generations

Parameters supported (minimal set):
  - prompt
  - negative_prompt
  - width
  - height
  - steps
"""

import logging
import requests
import base64
from datetime import datetime
import os
from flask_babel import gettext as _
from flask_babel import force_locale


class SdCppModule:
    """Module for image generation via stable-diffusion.cpp server."""

    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.sd_cpp_url = None
        self.model_name = None
        self.available = False
        self.timeout = 180
        self.multimodal_module = None

        if app:
            self.init_app(app)

    def _(self, key, lang='ru', **kwargs):
        with self.app.app_context():
            with force_locale(lang):
                return _(key, **kwargs)

    def init_app(self, app):
        """Initialize module with Flask app"""
        self.app = app
        self.sd_cpp_url = app.config.get('SD_CPP_URL')
        self.model_name = app.config.get('SD_CPP_MODEL')
        self.timeout = app.config.get('SD_CPP_TIMEOUT', 180)

        # Default generation parameters from config
        self.default_width = app.config.get('SD_CPP_DEFAULT_WIDTH', 1024)
        self.default_height = app.config.get('SD_CPP_DEFAULT_HEIGHT', 1024)
        self.default_cfg_scale = app.config.get('SD_CPP_DEFAULT_CFG_SCALE', 1.0)
        self.default_steps = app.config.get('SD_CPP_DEFAULT_STEPS', 10)

        # For Z_image_turbo/Qwen_image: cfg_scale=1.0 means no negative prompt
        self.use_negative_prompt = self.default_cfg_scale > 1.0

        self.logger.info(
            f"Initializing SdCppModule with SD URL: {self.sd_cpp_url}, "
            f"cfg_scale={self.default_cfg_scale}, steps={self.default_steps}, "
            f"{self.default_width}x{self.default_height}, "
            f"negative_prompt={'yes' if self.use_negative_prompt else 'no'}"
        )

        # Initial availability check with retries
        max_retries = app.config.get('SERVICE_RETRY_ATTEMPTS', 5)
        retry_delay = app.config.get('SERVICE_RETRY_DELAY', 2)

        for attempt in range(1, max_retries + 1):
            if self.check_availability():
                break
            if attempt < max_retries:
                self.logger.warning(
                    f"sd.cpp server not ready (attempt {attempt}/{max_retries}), "
                    f"retrying in {retry_delay}s..."
                )
                import time
                time.sleep(retry_delay)
            else:
                self.logger.warning(
                    f"sd.cpp server not available after {max_retries} attempts"
                )

        if self.available:
            self.logger.info(
                f"SdCppModule initialized and available. Timeout: {self.timeout}s"
            )
        else:
            self.logger.warning(
                f"SdCppModule initialized, but sd.cpp server unavailable "
                f"({self.sd_cpp_url}). Will retry on each request."
            )

    def set_multimodal_module(self, multimodal_module):
        """Set reference to multimodal module (for prompt generation)."""
        self.multimodal_module = multimodal_module

    def check_availability(self):
        """Check module availability."""
        if not self.sd_cpp_url:
            self.logger.error("SD_CPP_URL not configured")
            return False

        try:
            # sd.cpp server may not have a health endpoint, try root
            response = requests.get(self.sd_cpp_url.rstrip('/'), timeout=5)
            # 200, 404, 405 all indicate the server is running
            if response.status_code in (200, 404, 405):
                if not self.available:
                    self.logger.info(
                        f"sd.cpp server is now available at {self.sd_cpp_url}"
                    )
                self.available = True
                return True
            else:
                self.logger.warning(
                    f"sd.cpp server returned status {response.status_code}"
                )
                self.available = False
                return False
        except Exception as e:
            self.logger.error(f"Error connecting to sd.cpp server: {str(e)}")
            self.available = False
            return False

    def generate_image(self, user_query, start_time=None, lang='ru'):
        """Generate image from user query."""
        # Always re-check availability
        self.logger.info(
            f"Checking sd.cpp availability before generation... "
            f"(current available={self.available})"
        )
        was_available = self.available
        self.check_availability()
        if was_available != self.available:
            self.logger.info(
                f"sd.cpp server availability changed: {was_available} -> {self.available}"
            )

        if not self.available:
            return {
                'success': False,
                'error': self._('Image generation service unavailable', lang)
            }

        if not self.multimodal_module or not self.multimodal_module.available:
            return {
                'success': False,
                'error': self._(
                    'Multimodal module unavailable (required for parameter generation)',
                    lang
                )
            }

        # Generate parameters via multimodal module
        prompt_data, error = self.multimodal_module.generate_image_params(
            user_query, lang=lang
        )

        if error:
            return {
                'success': False,
                'error': error
            }

        return self._call_sd_cpp(prompt_data, lang)

    def _call_sd_cpp(self, prompt_data, lang='ru'):
        """Call sd.cpp server with image generation parameters.

        Supports two model types:
        1. Z_image_turbo / Qwen_image (flow matching):
           - cfg_scale=1.0, no negative_prompt, steps=10-30, 1024x1024
           - sampling_method: euler (Qwen) or default (Z_image_turbo)
           - flow_shift: 2-3
        2. Classic SD (traditional diffusion):
           - cfg_scale~7, negative_prompt supported, steps~30, 512x512

        The model type is determined by cfg_scale value in config.
        """
        try:
            # Use defaults from config, override with prompt_data if present
            cfg_scale = float(prompt_data.get("cfg_scale", self.default_cfg_scale))
            steps = int(prompt_data.get("steps", self.default_steps))
            width = int(prompt_data.get("width", self.default_width))
            height = int(prompt_data.get("height", self.default_height))

            payload = {
                "prompt": prompt_data.get("prompt", ""),
                "steps": steps,
                "width": width,
                "height": height,
                "cfg_scale": cfg_scale,
                "seed": int(prompt_data.get("seed", -1)),  # -1 = random
            }

            # Flow-matching specific parameters (Z_image_turbo, Qwen_image)
            if cfg_scale <= 1.0:
                if prompt_data.get("sampling_method"):
                    payload["sampling_method"] = prompt_data["sampling_method"]
                if prompt_data.get("flow_shift") is not None:
                    payload["flow_shift"] = float(prompt_data["flow_shift"])
            # Classic SD parameters
            elif prompt_data.get("sample_method"):
                payload["sample_method"] = prompt_data["sample_method"]

            # Only include negative_prompt for classic SD models (cfg_scale > 1.0)
            if self.use_negative_prompt and prompt_data.get("negative_prompt"):
                payload["negative_prompt"] = prompt_data["negative_prompt"]

            # Optional model override
            if self.model_name:
                payload["model"] = self.model_name

            self.logger.info(
                f"Sending request to sd.cpp server, cfg_scale={payload['cfg_scale']}, "
                f"steps={payload['steps']}, timeout: {self.timeout}s"
            )

            response = requests.post(
                f"{self.sd_cpp_url.rstrip('/')}/v1/images/generations",
                json=payload,
                timeout=self.timeout
            )

            if response.status_code == 200:
                result = response.json()
                # OpenAI format: {"data": [{"url": "..."} or {"b64_json": "..."}]}
                images = result.get('data', [])
                if images and len(images) > 0:
                    image_data = images[0].get('b64_json', '')
                    if not image_data and images[0].get('url'):
                        # If URL, download it
                        url = images[0]['url']
                        img_resp = requests.get(url, timeout=10)
                        if img_resp.status_code == 200:
                            image_data = base64.b64encode(img_resp.content).decode()
                        else:
                            return {
                                'success': False,
                                'error': self._('Failed to download generated image', lang)
                            }

                    if not image_data:
                        return {
                            'success': False,
                            'error': self._('sd.cpp returned no image data', lang)
                        }

                    file_size_bytes = int((len(image_data) * 3) / 4)
                    filename = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.png"

                    return {
                        'success': True,
                        'image_data': image_data,
                        'file_name': filename,
                        'file_size': file_size_bytes,
                        'file_type': 'image/png',
                        'mm_time': None,
                        'gen_time': None,
                        'mm_model': None,
                        'gen_model': self.model_name or "Stable Diffusion (sd.cpp)"
                    }
                else:
                    return {
                        'success': False,
                        'error': self._('sd.cpp returned no image', lang)
                    }
            else:
                self.logger.error(
                    f"sd.cpp server error: {response.status_code} - {response.text[:500]}"
                )
                return {
                    'success': False,
                    'error': f"sd.cpp error: {response.status_code}"
                }

        except requests.exceptions.Timeout:
            self.logger.error(f"Timeout ({self.timeout}s) during image generation")
            template = self._('Image generation timeout ({timeout}s)', lang)
            return {
                'success': False,
                'error': template.format(timeout=self.timeout)
            }
        except requests.exceptions.ConnectionError:
            return {
                'success': False,
                'error': self._('Could not connect to sd.cpp server', lang)
            }
        except Exception as e:
            self.logger.error(f"Error calling sd.cpp server: {str(e)}")
            return {
                'success': False,
                'error': f"{self._('Error', lang)}: {str(e)}"
            }
