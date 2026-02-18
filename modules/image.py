# modules/image.py

import logging
import requests
import base64
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)

class ImageModule:
    """Модуль для генерации изображений через Automatic1111"""
    
    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.automatic1111_url = None
        self.model_name = None
        self.available = False
        self.multimodal_module = None
        
        # Счетчик для отслеживания последовательных ошибок
        self.consecutive_errors = 0
        self.max_consecutive_errors = 3
        
        if app:
            self.init_app(app)
    
    def init_app(self, app):
        """Инициализация модуля с приложением Flask"""
        self.automatic1111_url = app.config.get('AUTOMATIC1111_URL')
        self.model_name = app.config.get('AUTOMATIC1111_MODEL')
        
        self.check_availability()
        
        if self.available:
            self.logger.info("ImageModule инициализирован и доступен")
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
            response = requests.get(f"{self.automatic1111_url}/sdapi/v1/progress", timeout=5)
            if response.status_code == 200:
                self.available = True
                self.consecutive_errors = 0  # Сброс счетчика ошибок
                return True
        except Exception as e:
            self.logger.error(f"Ошибка подключения к Automatic1111: {str(e)}")
        
        self.available = False
        return False
    
    def free_memory(self):
        """Очистка памяти Automatic1111 (выгрузка модели)"""
        try:
            self.logger.info("Очистка памяти Automatic1111")
            
            response = requests.post(
                f"{self.automatic1111_url}/sdapi/v1/unload-checkpoint",
                timeout=10
            )
            
            if response.status_code == 200:
                self.logger.info("Модель успешно выгружена из памяти")
                time.sleep(1)  # Небольшая пауза для освобождения памяти
                
                # После очистки сбрасываем счетчик ошибок
                self.consecutive_errors = 0
                return True
            else:
                self.logger.warning(f"Ошибка при выгрузке модели: {response.status_code}")
                return False
                
        except Exception as e:
            self.logger.error(f"Ошибка при очистке памяти: {e}")
            return False
    
    def is_ready(self):
        """Проверка, готов ли Automatic1111 к новому заданию"""
        try:
            response = requests.get(
                f"{self.automatic1111_url}/sdapi/v1/progress",
                timeout=3
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('progress') == 0 and not data.get('state', {}).get('active'):
                    return True, "ready"
                else:
                    progress = data.get('progress', 0) * 100
                    return False, f"busy: {progress:.0f}%"
        except Exception as e:
            return False, f"error: {e}"
        
        return False, "unknown"
    
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
        
        # Отправляем запрос в Automatic1111 (с возможностью повтора при ошибке)
        return self._call_automatic1111_with_retry(prompt_data)
    
    def _call_automatic1111_with_retry(self, prompt_data):
        """
        Вызов Automatic1111 с повтором при ошибке
        Если первый запрос завершился ошибкой, делаем очистку памяти и пробуем снова
        """
        # Первая попытка
        result = self._call_automatic1111(prompt_data)
        
        # Если успех или это не ошибка 500 (или сервис недоступен) - возвращаем результат
        if result['success'] or result.get('status_code') != 500:
            return result
        
        # Если ошибка 500 - пробуем очистить память и повторить
        self.logger.warning(f"Ошибка 500 при генерации, пробуем очистить память и повторить")
        
        # Очищаем память
        if self.free_memory():
            self.logger.info("Память очищена, повторяем запрос")
            # Повторяем запрос
            second_result = self._call_automatic1111(prompt_data)
            
            if second_result['success']:
                self.logger.info("Повторный запрос успешен после очистки памяти")
                return second_result
            else:
                self.logger.error(f"Повторный запрос также завершился ошибкой: {second_result.get('error')}")
                return second_result
        else:
            self.logger.error("Не удалось очистить память")
            return result
    
    def _call_automatic1111(self, prompt_data):
        """Вызов Automatic1111 API"""
        
        # Проверяем готовность
        is_ready, status = self.is_ready()
        if not is_ready:
            return {
                'success': False,
                'error': f"Automatic1111 не готов: {status}",
                'status_code': 503
            }
        
        try:
            payload = {
                "prompt": prompt_data.get("prompt", ""),
                "negative_prompt": prompt_data.get("negative_prompt", ""),
                "steps": int(prompt_data.get("steps", 30)),
                "width": int(prompt_data.get("width", 512)),
                "height": int(prompt_data.get("height", 512)),
                "cfg_scale": float(prompt_data.get("cfg_scale", 7)),
                "sampler_name": prompt_data.get("sampler_name", "DPM++ 2M Karras"),
                "batch_size": 1,  # Всегда 1 для экономии памяти
                "enable_hr": False,  # Отключаем Hi-Res для стабильности
            }
            
            # Добавляем модель, если указана
            if self.model_name:
                payload["override_settings"] = {
                    "sd_model_checkpoint": self.model_name
                }
            
            self.logger.info(f"Отправка запроса в Automatic1111")
            
            response = requests.post(
                f"{self.automatic1111_url}/sdapi/v1/txt2img",
                json=payload,
                timeout=120
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('images') and len(result['images']) > 0:
                    image_data = result['images'][0]
                    
                    # Вычисляем размер файла
                    file_size_bytes = int((len(image_data) * 3) / 4)
                    
                    # Генерируем имя файла
                    filename = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.jpg"
                    
                    # Сбрасываем счетчик ошибок при успехе
                    self.consecutive_errors = 0
                    
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
                    self.consecutive_errors += 1
                    return {
                        'success': False,
                        'error': "Automatic1111 не вернул изображение",
                        'status_code': 500
                    }
            else:
                self.consecutive_errors += 1
                error_text = f"Ошибка Automatic1111: {response.status_code}"
                try:
                    error_data = response.json()
                    if 'error' in error_data:
                        error_text += f" - {error_data['error']}"
                except:
                    pass
                
                self.logger.error(error_text)
                return {
                    'success': False,
                    'error': error_text,
                    'status_code': response.status_code
                }
                
        except requests.exceptions.ConnectionError:
            self.consecutive_errors += 1
            self.available = False  # Помечаем как недоступный
            return {
                'success': False,
                'error': "Не удалось подключиться к Automatic1111",
                'status_code': 503
            }
        except Exception as e:
            self.consecutive_errors += 1
            self.logger.error(f"Ошибка вызова Automatic1111: {str(e)}")
            return {
                'success': False,
                'error': f"Ошибка: {str(e)}",
                'status_code': 500
            }