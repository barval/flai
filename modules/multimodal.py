# modules/multimodal.py
import logging
import json
import requests
import base64
from PIL import Image
from io import BytesIO
import os
from flask import current_app
from flask_babel import gettext as _
from flask_babel import force_locale

from app.utils import format_prompt
from app.db import get_session_text_history
from app.model_config import get_model_config

class MultimodalModule:
    """Module for multimodal model (image processing)"""
    
    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.available = False
        self.image_settings = {}
        self.token_chars = 3
        self.context_history_percent = 75
        
        if app:
            self.init_app(app)

    def _(self, key, lang='ru', **kwargs):
        with self.app.app_context():
            with force_locale(lang):
                return _(key, **kwargs)
    
    def init_app(self, app):
        """Initialize module with Flask app"""
        self.app = app
        self.ollama_url = app.config.get('OLLAMA_URL')
        
        self.image_settings = {
            'max_width': app.config.get('MAX_IMAGE_WIDTH', 3840),
            'max_height': app.config.get('MAX_IMAGE_HEIGHT', 2160),
            'max_size_mb': app.config.get('MAX_IMAGE_SIZE_MB', 5),
            'max_size_bytes': app.config.get('MAX_IMAGE_SIZE_MB', 5) * 1024 * 1024,
            'supported_mimetypes': {
                'image/jpeg', 'image/jpg', 'image/jpe',
                'image/png', 'image/bmp', 'image/x-ms-bmp',
                'image/webp', 'image/tiff', 'image/tif'
            },
            'supported_extensions': {
                '.jpg', '.jpeg', '.jpe', '.png',
                '.bmp', '.webp', '.tif', '.tiff'
            }
        }
        
        # Token estimation settings
        self.token_chars = app.config.get('TOKEN_CHARS', 3)
        self.context_history_percent = app.config.get('CONTEXT_HISTORY_PERCENT', 75)
        
        self.check_availability()
        
        if self.available:
            self.logger.info(f"MultimodalModule initialized and available.")
        else:
            self.logger.warning("MultimodalModule initialized, but multimodal model unavailable")
    
    def check_availability(self):
        """Check module availability"""
        if not self.ollama_url:
            self.logger.error("OLLAMA_URL not configured")
            return False
        
        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if response.status_code == 200:
                models = response.json().get('models', [])
                available_models = [m['name'] for m in models]
                
                # Get configured multimodal model name from DB config
                model_config = self._get_model_config()
                multimodal_model = model_config.get('model_name') if model_config else None
                
                if multimodal_model and multimodal_model not in available_models:
                    self.logger.warning(f"Multimodal model {multimodal_model} not found in Ollama")
                    return False
                
                self.available = True
                return True
        except Exception as e:
            self.logger.error(f"Error connecting to Ollama: {str(e)}")
        
        return False
    
    def _get_model_config(self):
        """Retrieve multimodal model configuration directly from the database."""
        return get_model_config('multimodal')
    
    def validate_image(self, file_data, file_type, file_name, file_size, lang='ru'):
        """Validate image against requirements"""
        if file_size > self.image_settings['max_size_bytes']:
            template = self._('Maximum file size {max_size} MB', lang)
            return False, template.format(max_size=self.image_settings['max_size_mb'])
        
        if file_type not in self.image_settings['supported_mimetypes']:
            ext = os.path.splitext(file_name)[1].lower()
            if ext not in self.image_settings['supported_extensions']:
                return False, self._('Unsupported file type', lang)
        
        try:
            image_bytes = base64.b64decode(file_data)
            img = Image.open(BytesIO(image_bytes))
            width, height = img.size
            
            if width > self.image_settings['max_width'] or height > self.image_settings['max_height']:
                template = self._('Maximum resolution {max_width}x{max_height}', lang)
                return False, template.format(max_width=self.image_settings['max_width'], max_height=self.image_settings['max_height'])
            
            return True, None
        except Exception as e:
            self.logger.error(f"Error validating image: {str(e)}")
            return False, self._('Could not process image file', lang)
    
    # --- Context handling ---
    def _estimate_tokens(self, text):
        return len(text) // self.token_chars + 1
    
    def _build_context_prompt(self, history, lang='ru'):
        if not history:
            return ""
        lines = []
        for msg in history:
            role = self._("User", lang) if msg['role'] == 'user' else self._("Assistant", lang)
            lines.append(f"{role}: {msg['content']}")
        return "\n".join(lines)
    
    def _get_context_for_model(self, session_id, current_query, lang='ru'):
        """Retrieve text-only history for multimodal model, limited to configured percent of its context window."""
        if not session_id:
            return ""
        
        model_config = self._get_model_config()
        if not model_config:
            return ""
        max_context_tokens = model_config.get('context_length', 32768)
        available_tokens = int(max_context_tokens * (self.context_history_percent / 100.0))
        
        overhead = 500  # prompt overhead
        query_tokens = self._estimate_tokens(current_query)
        remaining_for_history = available_tokens - query_tokens - overhead
        if remaining_for_history <= 0:
            return ""
        
        history_msgs = get_session_text_history(session_id, remaining_for_history)
        return self._build_context_prompt(history_msgs, lang)
    
    def process_image_with_text(self, image_data, user_text, current_time_str, lang='ru', session_id=None):
        """Process image with text, including conversation history."""
        if not self.check_availability():
            return None, self._('Multimodal model unavailable', lang)
        
        response_language = 'Russian' if lang == 'ru' else 'English'
        
        # Get context history
        context_str = self._get_context_for_model(session_id, user_text, lang)
        
        if user_text.strip():
            prompt = format_prompt('image_text.template', {
                'current_time_str': current_time_str,
                'user_query': user_text,
                'response_language': response_language,
                'conversation_history': context_str
            }, lang=lang)
        else:
            prompt = format_prompt('image.template', {
                'current_time_str': current_time_str,
                'response_language': response_language,
                'conversation_history': context_str
            }, lang=lang)
        
        if not prompt:
            return None, self._('Error loading prompt template', lang)
        
        messages = [{
            'role': 'user',
            'content': prompt,
            'images': [image_data]
        }]
        
        response = self._call_multimodal(messages, lang=lang)
        return response, None
    
    def generate_image_params(self, user_query, lang='ru'):
        """Generate parameters for image creation (no context needed)."""
        if not self.check_availability():
            return None, self._('Multimodal model unavailable', lang)
        
        response_language = 'English'  # Always English for generation prompts
        create_prompt = format_prompt('create_image.template', {
            'image_query': user_query,
            'response_language': response_language
        }, lang=lang)
        
        if not create_prompt:
            return None, self._('Error loading prompt template', lang)
        
        messages = [
            {
                'role': 'system',
                'content': 'You are an image generation parameter generator. Always respond with valid JSON only, no explanations.'
            },
            {'role': 'user', 'content': create_prompt}
        ]
        
        response = self._call_multimodal(messages, lang=lang)
        
        self.logger.info(f"Multimodal model response for parameter generation: {response}")
        
        try:
            import re
            json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response, re.DOTALL)
            if json_match:
                json_str = json_match.group()
                prompt_data = json.loads(json_str)
                
                if 'prompt' not in prompt_data:
                    prompt_data['prompt'] = user_query
                if 'negative_prompt' not in prompt_data:
                    prompt_data['negative_prompt'] = ""
                
                return prompt_data, None
            else:
                return None, self._('Could not find JSON in model response', lang)
        except Exception as e:
            self.logger.error(f"JSON parsing error: {str(e)}")
            return None, self._('JSON parsing error: {error}', lang, error=str(e))
    
    def _call_multimodal(self, messages, lang='ru'):
        """Call multimodal model with configuration from database."""
        if not self.available:
            return self._('Multimodal model unavailable', lang)
        
        # Get model config from DB
        model_config = self._get_model_config()
        if not model_config:
            return self._('Multimodal model not configured', lang)
        
        model = model_config.get('model_name')
        timeout = model_config.get('timeout', 120)
        context = model_config.get('context_length', 32768)
        temperature = model_config.get('temperature', 0.7)
        top_p = model_config.get('top_p', 0.9)
        
        if not model:
            return self._('Multimodal model not configured', lang)
        
        try:
            payload = {
                'model': model,
                'messages': messages,
                'stream': False,
                'options': {
                    'num_ctx': context,
                    'temperature': temperature,
                    'top_p': top_p,
                }
            }
            
            self.logger.info(f"Sending request to multimodal model: {model}, timeout: {timeout}s")
            
            response = requests.post(
                f"{self.ollama_url}/api/chat",
                json=payload,
                timeout=timeout
            )
            
            if response.status_code == 200:
                result = response.json()
                return result['message']['content'].strip()
            else:
                self.logger.error(f"Multimodal model error: {response.status_code}")
                return f"{self._('Error', lang)}: {response.status_code}"
                
        except requests.exceptions.Timeout:
            self.logger.error(f"Timeout ({timeout}s) for multimodal model")
            template = self._('Timeout ({timeout}s) when calling multimodal model', lang)
            return template.format(timeout=timeout)
        except requests.exceptions.ConnectionError:
            self.logger.error(f"Connection error to Ollama at {self.ollama_url}")
            return self._('Could not connect to Ollama', lang)
        except Exception as e:
            self.logger.error(f"Error calling multimodal model: {str(e)}")
            return f"{self._('Error', lang)}: {str(e)}"