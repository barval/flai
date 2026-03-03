# modules/image.py
import logging
import requests
import base64
from datetime import datetime
import os

class ImageModule:
    """Модуль для генерации изображений через Automatic1111"""
    
    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.automatic1111_url = None
        self.model_name = None
        self.available = False
        self.multimodal_module = None  # Будет установлен извне
        self.timeout = 180  # Значение по умолчанию
        
        if app:
            self.init_app(app)
    
    def init_app(self, app):
        """Инициализация модуля с приложением Flask"""
        self.automatic1111_url = app.config.get('AUTOMATIC1111_URL')
        self.model_name = app.config.get('AUTOMATIC1111_MODEL')
        self.timeout = app.config.get('AUTOMATIC1111_TIMEOUT', 180)
        
        self.check_availability()
        
        if self.available:
            self.logger.info(f"ImageModule инициализирован и доступен. Таймаут: {self.timeout}с")
        else:
            self.logger.warning("ImageModule инициализирован, но Automatic1111 недоступен")
    
    def set_multimodal_module(self, multimodal_module):
        """Установка ссылки на мультимодальный модуль"""
        self.multimodal_module = multimodal_module
    
    def check_availability(self):
        """Проверка доступности модуля"""
        if not self.automatic1111_url:
            self.logger.error("AUTOMATIC1111_URL не настроен")
            return False
        
        try:
            # Проверяем доступность Automatic1111
            response = requests.get(f"{self.automatic1111_url}/sdapi/v1/progress", timeout=5)
            if response.status_code == 200:
                self.available = True
                return True
        except Exception as e:
            self.logger.error(f"Ошибка подключения к Automatic1111: {str(e)}")
        
        return False
    
    def generate_image(self, user_query, start_time=None):
        """Генерация изображения по запросу пользователя"""
        if not self.available:
            return {
                'success': False,
                'error': "Сервис генерации изображений недоступен"
            }
        
        if not self.multimodal_module or not self.multimodal_module.available:
            return {
                'success': False,
                'error': "Мультимодальный модуль недоступен (требуется для генерации параметров)"
            }
        
        # Генерируем параметры через мультимодальную модель
        prompt_data, error = self.multimodal_module.generate_image_params(user_query)
        
        if error:
            return {
                'success': False,
                'error': error
            }
        
        # Отправляем запрос в Automatic1111
        return self._call_automatic1111(prompt_data)
    
    def _call_automatic1111(self, prompt_data):
        """Вызов Automatic1111 API с настраиваемым таймаутом"""
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
            
            # Добавляем модель, если указана
            if self.model_name:
                payload["override_settings"] = {
                    "sd_model_checkpoint": self.model_name
                }
            
            self.logger.info(f"Отправка запроса в Automatic1111, таймаут: {self.timeout}с")
            
            response = requests.post(
                f"{self.automatic1111_url}/sdapi/v1/txt2img",
                json=payload,
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('images') and len(result['images']) > 0:
                    image_data = result['images'][0]
                    
                    # Вычисляем размер файла
                    file_size_bytes = int((len(image_data) * 3) / 4)
                    
                    # Генерируем имя файла
                    filename = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.jpg"
                    
                    return {
                        'success': True,
                        'image_data': image_data,
                        'file_name': filename,
                        'file_size': file_size_bytes,
                        'file_type': 'image/jpeg',
                        'mm_time': None,  # Будет заполнено в app.py
                        'gen_time': None,  # Будет заполнено в app.py
                        'mm_model': None,  # Будет заполнено в app.py
                        'gen_model': self.model_name or "Stable Diffusion"
                    }
                else:
                    return {
                        'success': False,
                        'error': "Automatic1111 не вернул изображение"
                    }
            else:
                return {
                    'success': False,
                    'error': f"Ошибка Automatic1111: {response.status_code}"
                }
                
        except requests.exceptions.Timeout:
            self.logger.error(f"Таймаут ({self.timeout}с) при генерации изображения")
            return {
                'success': False,
                'error': f"Превышено время ожидания генерации изображения ({self.timeout}с)"
            }
        except requests.exceptions.ConnectionError:
            return {
                'success': False,
                'error': "Не удалось подключиться к Automatic1111"
            }
        except Exception as e:
            self.logger.error(f"Ошибка вызова Automatic1111: {str(e)}")
            return {
                'success': False,
                'error': f"Ошибка: {str(e)}"
            }