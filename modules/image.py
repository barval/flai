# modules/image.py
# modules/image.py
import logging
import requests
import base64
from datetime import datetime
import os
from flask import current_app
from flask_babel import gettext as _
from flask_babel import force_locale

class ImageModule:
    """Module for image generation via Automatic1111"""
    
    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.automatic1111_url = None
        self.model_name = None
        self.available = False
        self.multimodal_module = None
        self.timeout = 180
        
        if app:
            self.init_app(app)

    def _(self, key, lang='ru', **kwargs):
        with self.app.app_context():
            with force_locale(lang):
                return _(key, **kwargs)
    
    def init_app(self, app):
        """Initialize module with Flask app"""
        self.app = app
        self.automatic1111_url = app.config.get('AUTOMATIC1111_URL')
        self.model_name = app.config.get('AUTOMATIC1111_MODEL')
        self.timeout = app.config.get('AUTOMATIC1111_TIMEOUT', 180)
        
        self.check_availability()
        
        if self.available:
            self.logger.info(f"ImageModule initialized and available. Timeout: {self.timeout}s")
        else:
            self.logger.warning("ImageModule initialized, but Automatic1111 unavailable")
    
    def set_multimodal_module(self, multimodal_module):
        """Set reference to multimodal module"""
        self.multimodal_module = multimodal_module
    
    def check_availability(self):
        """Check module availability"""
        if not self.automatic1111_url:
            self.logger.error("AUTOMATIC1111_URL not configured")
            return False
        
        try:
            response = requests.get(f"{self.automatic1111_url}/sdapi/v1/progress", timeout=5)
            if response.status_code == 200:
                self.available = True
                return True
        except Exception as e:
            self.logger.error(f"Error connecting to Automatic1111: {str(e)}")
        
        return False
    
    def generate_image(self, user_query, start_time=None, lang='ru'):
        """Generate image from user query"""
        if not self.available:
            return {
                'success': False,
                'error': self._('Image generation service unavailable', lang)
            }
        
        if not self.multimodal_module or not self.multimodal_module.available:
            return {
                'success': False,
                'error': self._('Multimodal module unavailable (required for parameter generation)', lang)
            }
        
        prompt_data, error = self.multimodal_module.generate_image_params(user_query, lang=lang)
        
        if error:
            return {
                'success': False,
                'error': error
            }
        
        return self._call_automatic1111(prompt_data, lang)
    
    def _call_automatic1111(self, prompt_data, lang='ru'):
        """Call Automatic1111 API with configurable timeout"""
        try:
            payload = {
                "prompt": prompt_data.get("prompt", ""),
                "negative_prompt": prompt_data.get("negative_prompt", ""),
                "steps": int(prompt_data.get("steps", 40)),
                "width": int(prompt_data.get("width", 512)),
                "height": int(prompt_data.get("height", 512)),
                "cfg_scale": float(prompt_data.get("cfg_scale", 7)),
                "sampler_name": prompt_data.get("sampler_name", "DPM++ 2M Karras"),
                "batch_size": int(prompt_data.get("batch_size", 1)),
                "enable_hr": prompt_data.get("enable_hr") == "true",
                "hr_scale": float(prompt_data.get("hr_scale", 2)),
                "hr_upscaler": prompt_data.get("hr_upscaler", "Latent (nearest)"),
                "denoising_strength": float(prompt_data.get("denoising_strength", 0.7)),
                "hr_second_pass_steps": int(prompt_data.get("hr_second_pass_steps", 25))
            }
            
            if self.model_name:
                payload["override_settings"] = {
                    "sd_model_checkpoint": self.model_name
                }
            
            self.logger.info(f"Sending request to Automatic1111, timeout: {self.timeout}s")
            
            response = requests.post(
                f"{self.automatic1111_url}/sdapi/v1/txt2img",
                json=payload,
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('images') and len(result['images']) > 0:
                    image_data = result['images'][0]
                    
                    file_size_bytes = int((len(image_data) * 3) / 4)
                    filename = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.jpg"
                    
                    return {
                        'success': True,
                        'image_data': image_data,
                        'file_name': filename,
                        'file_size': file_size_bytes,
                        'file_type': 'image/jpeg',
                        'mm_time': None,
                        'gen_time': None,
                        'mm_model': None,
                        'gen_model': self.model_name or "Stable Diffusion"
                    }
                else:
                    return {
                        'success': False,
                        'error': self._('Automatic1111 returned no image', lang)
                    }
            else:
                return {
                    'success': False,
                    'error': f"Automatic1111 error: {response.status_code}"
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
                'error': self._('Could not connect to Automatic1111', lang)
            }
        except Exception as e:
            self.logger.error(f"Error calling Automatic1111: {str(e)}")
            return {
                'success': False,
                'error': f"{self._('Error', lang)}: {str(e)}"
            }