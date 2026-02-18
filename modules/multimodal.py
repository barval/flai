# modules/multimodal.py
import logging
import json
import requests
import base64
from PIL import Image
from io import BytesIO
import os
import re
from .ollama_base import OllamaBaseModule

class MultimodalModule(OllamaBaseModule):
    """Модуль для работы с мультимодальной моделью (изображения)"""
    
    def __init__(self, app=None, ollama_url=None, models_config=None):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.ollama_url = ollama_url
        self.models_config = models_config or {}
        self.available = False
        self.image_settings = {}
        
        if app:
            self.init_app(app)
    
    def init_app(self, app):
        """Инициализация модуля с приложением Flask"""
        self.ollama_url = app.config.get('OLLAMA_URL')
        self.models_config = {
            'multimodal': {
                'model': app.config.get('LLM_MULTIMODAL_MODEL'),
                'context': app.config.get('LLM_MULTIMODAL_MODEL_CONTEXT_WINDOW', 32768),
                'temperature': app.config.get('LLM_MULTIMODAL_TEMPERATURE', 0.7),
                'top_p': app.config.get('LLM_MULTIMODAL_TOP_P', 0.9)
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
            self.logger.info("MultimodalModule инициализирован и доступен")
        else:
            self.logger.warning("MultimodalModule инициализирован, но мультимодальная модель недоступна")
    
    def check_availability(self):
        """Проверка доступности модуля"""
        if not self.ollama_url:
            self.logger.error("OLLAMA_URL не настроен")
            return False
        
        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if response.status_code == 200:
                models = response.json().get('models', [])
                available_models = [m['name'] for m in models]
                
                multimodal_model = self.models_config['multimodal']['model']
                
                if multimodal_model not in available_models:
                    self.logger.warning(f"Мультимодальная модель {multimodal_model} не найдена в Ollama")
                    return False
                
                self.available = True
                return True
        except Exception as e:
            self.logger.error(f"Ошибка подключения к Ollama: {str(e)}")
        
        return False
    
    def validate_image(self, file_data, file_type, file_name, file_size):
        """Проверка изображения на соответствие требованиям"""
        if file_size > self.image_settings['max_size_bytes']:
            return False, f"Максимальный размер файла {self.image_settings['max_size_mb']} Мб"
        
        if file_type not in self.image_settings['supported_mimetypes']:
            ext = os.path.splitext(file_name)[1].lower()
            if ext not in self.image_settings['supported_extensions']:
                return False, "Неподдерживаемый тип файла"
        
        try:
            image_bytes = base64.b64decode(file_data)
            img = Image.open(BytesIO(image_bytes))
            width, height = img.size
            
            if width > self.image_settings['max_width'] or height > self.image_settings['max_height']:
                return False, f"Максимальное разрешение {self.image_settings['max_width']}×{self.image_settings['max_height']}"
            
            return True, None
        except Exception as e:
            self.logger.error(f"Ошибка при проверке изображения: {str(e)}")
            return False, "Не удалось обработать файл изображения"
    
    def process_image_with_text(self, image_data, user_text, current_time_str):
        """Обработка изображения с текстом"""
        from app import format_prompt
        
        if user_text.strip():
            prompt = format_prompt('image_text.template', {
                'current_time_str': current_time_str,
                'user_query': user_text
            })
        else:
            prompt = format_prompt('image.template', {
                'current_time_str': current_time_str
            })
        
        if not prompt:
            return None, "Ошибка загрузки шаблона промпта"
        
        messages = [{
            'role': 'user',
            'content': prompt,
            'images': [image_data]
        }]
        
        response = self.call_with_retry(self._call_multimodal_impl, messages)
        return response, None
    
    def _call_multimodal_impl(self, messages):
        """Внутренняя реализация вызова мультимодальной модели"""
        if not self.available:
            return "⚠️ Мультимодальная модель недоступна"
        
        model_config = self.models_config['multimodal']
        model = model_config['model']
        
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
            
            # Оптимизация памяти
            if self.consecutive_errors > 2:
                payload['keep_alive'] = '0'
            else:
                payload['keep_alive'] = '30s'
            
            self.logger.info(f"Отправка запроса к мультимодальной модели: {model}")
            
            response = requests.post(
                f"{self.ollama_url}/api/chat",
                json=payload,
                timeout=120
            )
            
            if response.status_code == 200:
                result = response.json()
                return result['message']['content'].strip()
            else:
                self.logger.error(f"Ошибка мультимодальной модели: {response.status_code}")
                return f"⚠️ Ошибка: {response.status_code}"
                
        except Exception as e:
            self.logger.error(f"Ошибка вызова мультимодальной модели: {str(e)}")
            return f"⚠️ Ошибка: {str(e)}"
    
    def generate_image_params(self, user_query):
        """Генерация параметров для создания изображения"""
        from app import format_prompt
        
        create_prompt = format_prompt('create_image.template', {
            'image_query': user_query
        })
        
        if not create_prompt:
            return None, "Ошибка загрузки шаблона для генерации изображения"
        
        messages = [
            {
                'role': 'system',
                'content': 'You are an image generation parameter generator. Always respond with valid JSON only, no explanations.'
            },
            {'role': 'user', 'content': create_prompt}
        ]
        
        response = self.call_with_retry(self._call_multimodal_impl, messages)
        
        try:
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
                return None, "Не удалось найти JSON в ответе модели"
        except Exception as e:
            self.logger.error(f"Ошибка парсинга JSON: {str(e)}")
            return None, f"Ошибка парсинга JSON: {str(e)}"