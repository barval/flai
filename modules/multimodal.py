# modules/multimodal.py
import logging
import json
import requests
import base64
from PIL import Image
from io import BytesIO
import os

from app.utils import format_prompt

class MultimodalModule:
    """Module for multimodal model (image processing)"""
    
    def __init__(self, app=None, ollama_url=None, models_config=None):
        self.logger = logging.getLogger(__name__)
        self.ollama_url = ollama_url
        self.models_config = models_config or {}
        self.available = False
        self.image_settings = {}
        self.timeout = 120
        
        self.messages = {
            'ru': {
                'model_unavailable': '⚠️ Мультимодальная модель недоступна',
                'prompt_load_error': 'Ошибка загрузки шаблона промпта',
                'image_too_large': 'Максимальный размер файла {max_size} Мб',
                'unsupported_type': 'Неподдерживаемый тип файла',
                'image_too_big_resolution': 'Максимальное разрешение {max_width}×{max_height}',
                'image_processing_error': 'Не удалось обработать файл изображения',
                'timeout': '⚠️ Превышено время ожидания ответа от мультимодальной модели ({timeout}с)',
                'connection_error': '⚠️ Не удалось подключиться к Ollama',
                'error_prefix': '⚠️ Ошибка',
                'json_parse_error': 'Не удалось найти JSON в ответе модели',
                'json_parse_error_detail': 'Ошибка парсинга JSON: {error}'
            },
            'en': {
                'model_unavailable': '⚠️ Multimodal model unavailable',
                'prompt_load_error': 'Error loading prompt template',
                'image_too_large': 'Maximum file size {max_size} MB',
                'unsupported_type': 'Unsupported file type',
                'image_too_big_resolution': 'Maximum resolution {max_width}×{max_height}',
                'image_processing_error': 'Could not process image file',
                'timeout': '⚠️ Timeout ({timeout}s) when calling multimodal model',
                'connection_error': '⚠️ Could not connect to Ollama',
                'error_prefix': '⚠️ Error',
                'json_parse_error': 'Could not find JSON in model response',
                'json_parse_error_detail': 'JSON parsing error: {error}'
            }
        }
        
        if app:
            self.init_app(app)
    
    def get_message(self, key, lang='ru', **kwargs):
        msg_dict = self.messages.get(lang, self.messages['ru'])
        msg = msg_dict.get(key, key)
        if kwargs:
            try:
                return msg.format(**kwargs)
            except KeyError:
                return msg
        return msg
    
    def init_app(self, app):
        """Initialize module with Flask app"""
        self.ollama_url = app.config.get('OLLAMA_URL')
        self.timeout = app.config.get('LLM_MULTIMODAL_TIMEOUT', 120)
        
        self.models_config = {
            'multimodal': {
                'model': app.config.get('LLM_MULTIMODAL_MODEL'),
                'context': app.config.get('LLM_MULTIMODAL_MODEL_CONTEXT_WINDOW', 32768),
                'temperature': app.config.get('LLM_MULTIMODAL_TEMPERATURE', 0.7),
                'top_p': app.config.get('LLM_MULTIMODAL_TOP_P', 0.9),
                'timeout': self.timeout
            }
        }
        
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
        
        self.check_availability()
        
        if self.available:
            self.logger.info(f"MultimodalModule initialized and available. Timeout: {self.timeout}s")
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
                
                multimodal_model = self.models_config['multimodal']['model']
                
                if multimodal_model not in available_models:
                    self.logger.warning(f"Multimodal model {multimodal_model} not found in Ollama")
                    return False
                
                self.available = True
                return True
        except Exception as e:
            self.logger.error(f"Error connecting to Ollama: {str(e)}")
        
        return False
    
    def validate_image(self, file_data, file_type, file_name, file_size):
        """Validate image against requirements"""
        if file_size > self.image_settings['max_size_bytes']:
            return False, self.get_message('image_too_large', max_size=self.image_settings['max_size_mb'])
        
        if file_type not in self.image_settings['supported_mimetypes']:
            ext = os.path.splitext(file_name)[1].lower()
            if ext not in self.image_settings['supported_extensions']:
                return False, self.get_message('unsupported_type')
        
        try:
            image_bytes = base64.b64decode(file_data)
            img = Image.open(BytesIO(image_bytes))
            width, height = img.size
            
            if width > self.image_settings['max_width'] or height > self.image_settings['max_height']:
                return False, self.get_message('image_too_big_resolution', max_width=self.image_settings['max_width'], max_height=self.image_settings['max_height'])
            
            return True, None
        except Exception as e:
            self.logger.error(f"Error validating image: {str(e)}")
            return False, self.get_message('image_processing_error')
    
    def process_image_with_text(self, image_data, user_text, current_time_str, lang='ru'):
        """Process image with text"""
        if not self.check_availability():
            return None, self.get_message('model_unavailable', lang)
        
        response_language = 'Russian' if lang == 'ru' else 'English'
        if user_text.strip():
            prompt = format_prompt('image_text.template', {
                'current_time_str': current_time_str,
                'user_query': user_text,
                'response_language': response_language
            })
        else:
            prompt = format_prompt('image.template', {
                'current_time_str': current_time_str,
                'response_language': response_language
            })
        
        if not prompt:
            return None, self.get_message('prompt_load_error', lang)
        
        messages = [{
            'role': 'user',
            'content': prompt,
            'images': [image_data]
        }]
        
        response = self._call_multimodal(messages, lang=lang)
        return response, None
    
    def generate_image_params(self, user_query, lang='ru'):
        """Generate parameters for image creation"""
        if not self.check_availability():
            return None, self.get_message('model_unavailable', lang)
        
        response_language = 'English'  # Always English for generation prompts
        create_prompt = format_prompt('create_image.template', {
            'image_query': user_query,
            'response_language': response_language
        })
        
        if not create_prompt:
            return None, self.get_message('prompt_load_error', lang)
        
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
                return None, self.get_message('json_parse_error', lang)
        except Exception as e:
            self.logger.error(f"JSON parsing error: {str(e)}")
            return None, self.get_message('json_parse_error_detail', lang, error=str(e))
    
    def _call_multimodal(self, messages, lang='ru'):
        """Call multimodal model with configurable timeout"""
        if not self.available:
            return self.get_message('model_unavailable', lang)
        
        model_config = self.models_config['multimodal']
        model = model_config['model']
        timeout = model_config.get('timeout', 120)
        
        try:
            payload = {
                'model': model,
                'messages': messages,
                'stream': False,
                'options': {
                    'num_ctx': model_config['context'],
                    'temperature': model_config['temperature'],
                    'top_p': model_config['top_p'],
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
                return f"{self.get_message('error_prefix', lang)}: {response.status_code}"
                
        except requests.exceptions.Timeout:
            self.logger.error(f"Timeout ({timeout}s) for multimodal model")
            return self.get_message('timeout', lang, timeout=timeout)
        except requests.exceptions.ConnectionError:
            self.logger.error(f"Connection error to Ollama at {self.ollama_url}")
            return self.get_message('connection_error', lang)
        except Exception as e:
            self.logger.error(f"Error calling multimodal model: {str(e)}")
            return f"{self.get_message('error_prefix', lang)}: {str(e)}"