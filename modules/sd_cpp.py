# modules/sd_cpp.py
"""
Module for image generation via stable-diffusion.cpp.

Uses sd-wrapper HTTP API (sd_wrapper.py running in sd container)
which calls sd-cli internally. The sd-server's OpenAI endpoint
doesn't properly use loaded models.
"""

import logging
import requests
import base64
from datetime import datetime
from flask_babel import gettext as _
from flask_babel import force_locale


class SdCppModule:
    """Module for image generation via stable-diffusion.cpp sd-wrapper."""

    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.wrapper_url = None
        self.available = False
        self.timeout = 300
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
        # sd-wrapper runs on port 7861 in sd container
        self.wrapper_url = app.config.get('SD_WRAPPER_URL', 'http://flai-sd:7861')
        self.timeout = app.config.get('SD_CPP_TIMEOUT', 300)

        # Default generation parameters
        self.default_width = app.config.get('SD_CPP_DEFAULT_WIDTH', 1024)
        self.default_height = app.config.get('SD_CPP_DEFAULT_HEIGHT', 1024)
        self.default_cfg_scale = app.config.get('SD_CPP_DEFAULT_CFG_SCALE', 1.0)
        self.default_steps = app.config.get('SD_CPP_DEFAULT_STEPS', 10)

        self.logger.info(
            f"SdCppModule initialized with wrapper URL: {self.wrapper_url}, "
            f"timeout: {self.timeout}s"
        )

        # Initial availability check with reduced retries (don't block startup)
        max_retries = 1
        retry_delay = 1

        for attempt in range(1, max_retries + 1):
            if self.check_availability():
                break
            if attempt < max_retries:
                self.logger.warning(
                    f"sd-wrapper not ready (attempt {attempt}/{max_retries}), "
                    f"retrying in {retry_delay}s..."
                )
                import time
                time.sleep(retry_delay)
            else:
                self.logger.warning(
                    f"sd-wrapper not available after {max_retries} attempts"
                )

        if self.available:
            self.logger.info(
                f"SdCppModule initialized and available. Timeout: {self.timeout}s"
            )
        else:
            self.logger.warning(
                f"SdCppModule initialized, but sd-wrapper unavailable "
                f"({self.wrapper_url}). Will retry on each request."
            )

    def set_multimodal_module(self, multimodal_module):
        """Set reference to multimodal module (for prompt generation)."""
        self.multimodal_module = multimodal_module

    def check_availability(self):
        """Check sd-wrapper availability."""
        if not self.wrapper_url:
            self.logger.error("SD_WRAPPER_URL not configured")
            return False

        try:
            response = requests.get(f"{self.wrapper_url.rstrip('/')}/health", timeout=5)
            if response.status_code == 200:
                self.available = True
                return True
            else:
                self.logger.warning(
                    f"sd-wrapper returned status {response.status_code}"
                )
                self.available = False
                return False
        except Exception as e:
            self.logger.error(f"Error connecting to sd-wrapper: {str(e)}")
            self.available = False
            return False

    def generate_image(self, user_query, start_time=None, lang='ru'):
        """Generate image from user query."""
        # Always re-check availability
        self.logger.info(
            f"Checking sd-wrapper availability... (current available={self.available})"
        )
        was_available = self.available
        self.check_availability()
        if was_available != self.available:
            self.logger.info(
                f"sd-wrapper availability changed: {was_available} -> {self.available}"
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

        return self._call_wrapper(prompt_data, lang)

    def _call_wrapper(self, prompt_data, lang='ru'):
        """Call sd-wrapper HTTP API to generate image."""
        # Use defaults from config, override with prompt_data if present
        cfg_scale = float(prompt_data.get('cfg_scale', self.default_cfg_scale))
        steps = int(prompt_data.get('steps', self.default_steps))
        width = int(prompt_data.get('width', self.default_width))
        height = int(prompt_data.get('height', self.default_height))
        flow_shift = float(prompt_data.get('flow_shift', 2.0))

        payload = {
            'prompt': prompt_data.get('prompt', ''),
            'steps': steps,
            'width': width,
            'height': height,
            'cfg_scale': cfg_scale,
            'flow_shift': flow_shift,
        }

        self.logger.info(
            f"Sending request to sd-wrapper, cfg_scale={cfg_scale}, "
            f"steps={steps}, {width}x{height}, timeout: {self.timeout}s"
        )
        self.logger.info(
            f"sd.cpp prompt: '{payload['prompt'][:100]}...'"
        )

        try:
            response = requests.post(
                f"{self.wrapper_url.rstrip('/')}/v1/images/generations",
                json=payload,
                timeout=self.timeout
            )

            if response.status_code == 200:
                result = response.json()
                # Check for error in response
                if 'error' in result:
                    return {
                        'success': False,
                        'error': result['error']
                    }

                images = result.get('data', [])
                if images and len(images) > 0:
                    image_data = images[0].get('b64_json', '')
                    if not image_data:
                        return {
                            'success': False,
                            'error': self._('sd-wrapper returned no image data', lang)
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
                        'gen_model': 'Z_image_turbo'
                    }
                else:
                    return {
                        'success': False,
                        'error': self._('sd-wrapper returned no image', lang)
                    }
            else:
                self.logger.error(
                    f"sd-wrapper error: {response.status_code} - {response.text[:500]}"
                )
                return {
                    'success': False,
                    'error': f"sd-wrapper error: {response.status_code}"
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
                'error': self._('Could not connect to sd-wrapper', lang)
            }
        except Exception as e:
            self.logger.error(f"Error calling sd-wrapper: {str(e)}")
            return {
                'success': False,
                'error': f"{self._('Error', lang)}: {str(e)}"
            }
