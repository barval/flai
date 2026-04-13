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
from typing import Dict, Any
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
        self.model_type = app.config.get('SD_MODEL_TYPE', 'z_image_turbo')

        self.logger.info(
            f"SdCppModule initialized with wrapper URL: {self.wrapper_url}, "
            f"model_type: {self.model_type}, timeout: {self.timeout}s"
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
        """Call sd-wrapper HTTP API to generate image.
        Before starting, unloads llama.cpp model from VRAM to avoid OOM.
        """
        # Signal Resource Manager that sd-cli is using GPU
        from app.resource_manager import get_resource_manager
        rm = get_resource_manager()

        # Unload llama.cpp model to free ALL VRAM for sd-cli
        llamacpp_url = self.app.config.get('LLAMACPP_URL', 'http://flai-llamacpp:8033')
        unload_success = rm.unload_llamacpp_model(llamacpp_url)
        if not unload_success:
            self.logger.warning(
                "Failed to unload llama.cpp model before generation — OOM risk"
            )

        rm.mark_sd_busy()

        try:
            self.logger.info(
                f"Sending request to sd-wrapper ({self.model_type}), "
                f"cfg_scale={prompt_data.get('cfg_scale', 'auto')}, "
                f"steps={prompt_data.get('steps', 'auto')}, "
                f"timeout: {self.timeout}s"
            )
            self.logger.info(
                f"sd.cpp prompt: '{prompt_data.get('prompt', '')[:100]}...'"
            )

            # Parameters from template — each model type has its own defaults
            payload = {
                'prompt': prompt_data.get('prompt', ''),
                'steps': prompt_data.get('steps', 10),
                'width': prompt_data.get('width', 1024),
                'height': prompt_data.get('height', 1024),
                'cfg_scale': prompt_data.get('cfg_scale', 1.0),
                'flow_shift': prompt_data.get('flow_shift', 2.0),
            }
            # Optional sampler (required for qwen_image)
            if prompt_data.get('sampling_method'):
                payload['sampling_method'] = prompt_data['sampling_method']

            response = requests.post(
                f"{self.wrapper_url.rstrip('/')}/v1/images/generations",
                json=payload,
                timeout=self.timeout
            )

            if response.status_code == 200:
                result = response.json()
                # Check for error in response
                if 'error' in result:
                    err_msg = result['error']
                    self.logger.error(f"sd-wrapper error: {err_msg}")
                    from app.utils import translate_sd_error
                    user_error = translate_sd_error(err_msg, self._, lang, timeout=self.timeout)
                    return {
                        'success': False,
                        'error': user_error
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
        finally:
            rm.mark_sd_idle()

    def edit_image(self, edit_prompt_data: Dict[str, Any], image_base64: str, lang: str = 'ru') -> Dict[str, Any]:
        """Edit an existing image using Flux.2 Klein 4B model.
        Before starting, unloads llama.cpp model from VRAM to avoid OOM.
        Resizes large images to max 1024px to fit 16GB VRAM.
        Returns dict with 'resized', 'original_size', 'new_size' if resize occurred.
        """
        from app.resource_manager import get_resource_manager
        from PIL import Image
        from io import BytesIO

        rm = get_resource_manager()

        # Unload llama.cpp model to free ALL VRAM for sd-cli
        llamacpp_url = self.app.config.get('LLAMACPP_URL', 'http://flai-llamacpp:8033')
        unload_success = rm.unload_llamacpp_model(llamacpp_url)
        if not unload_success:
            self.logger.warning(
                "Failed to unload llama.cpp model before editing — OOM risk"
            )

        # Resize large images to avoid OOM on 16GB VRAM
        max_edit_size = 1024
        resized_info = {'resized': False, 'original_size': None, 'new_size': None}
        try:
            img_bytes = base64.b64decode(image_base64)
            img = Image.open(BytesIO(img_bytes))
            w, h = img.size
            if w > max_edit_size or h > max_edit_size:
                ratio = max_edit_size / max(w, h)
                new_w, new_h = int(w * ratio), int(h * ratio)
                img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                if img.mode in ('RGBA', 'LA', 'P'):
                    rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                    rgb_img.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                    img = rgb_img
                buf = BytesIO()
                img.save(buf, format='JPEG', quality=90)
                image_base64 = base64.b64encode(buf.getvalue()).decode('utf-8')
                resized_info = {'resized': True, 'original_size': (w, h), 'new_size': (new_w, new_h)}
                self.logger.info(f"Edit: resized image from {w}x{h} to {new_w}x{new_h}")
        except Exception as e:
            self.logger.warning(f"Edit: failed to resize image: {e}")

        rm.mark_sd_busy()

        self.logger.info(
            f"Sending edit request to sd-wrapper, "
            f"timeout: {self.timeout}s"
        )
        self.logger.info(
            f"sd.cpp edit prompt: '{edit_prompt_data.get('edit_prompt', '')[:100]}...'"
        )

        payload = {
            'edit_prompt': edit_prompt_data.get('edit_prompt', ''),
            'image_data': image_base64,
            'strength': edit_prompt_data.get('strength', 0.7),
            'width': edit_prompt_data.get('width', 1024),
            'height': edit_prompt_data.get('height', 1024),
        }
        if edit_prompt_data.get('mask'):
            payload['mask'] = edit_prompt_data['mask']
        if edit_prompt_data.get('preserve'):
            payload['preserve'] = edit_prompt_data['preserve']

        try:
            response = requests.post(
                f"{self.wrapper_url.rstrip('/')}/v1/images/edits",
                json=payload,
                timeout=self.timeout
            )

            if response.status_code == 200:
                result = response.json()
                if 'error' in result:
                    err_msg = result['error']
                    self.logger.error(f"sd-wrapper edit error: {err_msg}")
                    from app.utils import translate_sd_error
                    user_error = translate_sd_error(err_msg, self._, lang, timeout=self.timeout)
                    return {
                        'success': False,
                        'error': user_error
                    }

                images = result.get('data', [])
                if images and len(images) > 0:
                    image_data = images[0].get('b64_json', '')
                    if not image_data:
                        return {
                            'success': False,
                            'error': self._('Edit result has no image data.', lang)
                        }

                    file_size_bytes = int((len(image_data) * 3) / 4)
                    filename = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_edit.png"

                    return {
                        'success': True,
                        'image_data': image_data,
                        'file_name': filename,
                        'file_size': file_size_bytes,
                        'file_type': 'image/png',
                        'mm_time': None,
                        'gen_time': None,
                        'mm_model': None,
                        'gen_model': 'flux-2-klein-4b',
                        'resized': resized_info['resized'],
                        'original_size': resized_info['original_size'],
                        'new_size': resized_info['new_size'],
                    }
                else:
                    return {
                        'success': False,
                        'error': self._('Edit returned no image.', lang)
                    }
            else:
                self.logger.error(
                    f"sd-wrapper edit error: {response.status_code} - {response.text[:500]}"
                )
                return {
                    'success': False,
                    'error': f"sd-wrapper edit error: {response.status_code}"
                }

        except requests.exceptions.Timeout:
            self.logger.error(f"Edit timeout ({self.timeout}s)")
            return {
                'success': False,
                'error': self._('Editing timeout ({timeout}s)', lang).format(timeout=self.timeout)
            }
        except requests.exceptions.ConnectionError:
            return {
                'success': False,
                'error': self._('Could not connect to editor.', lang)
            }
        except Exception as e:
            self.logger.error(f"Edit error: {str(e)}")
            return {
                'success': False,
                'error': f"{self._('Error', lang)}: {str(e)}"
            }
        finally:
            rm.mark_sd_idle()
