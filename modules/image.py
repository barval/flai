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
        self.resource_manager = None
        self.cleanup_lock = threading.Lock()
        
        # Настройки очистки памяти
        self.aggressive_cleanup = True
        self.cleanup_delay = 2  # секунд после генерации
        
        if app:
            self.init_app(app)
    
    def init_app(self, app):
        """Инициализация модуля с приложением Flask"""
        self.automatic1111_url = app.config.get('AUTOMATIC1111_URL')
        self.model_name = app.config.get('AUTOMATIC1111_MODEL')
        
        # Получаем resource_manager из app, если есть
        self.resource_manager = getattr(app, 'resource_manager', None)
        
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
                return True
        except Exception as e:
            self.logger.error(f"Ошибка подключения к Automatic1111: {str(e)}")
        
        return False
    
    def log_vram_usage(self, stage=""):
        """Логирование использования VRAM через API Automatic1111"""
        try:
            response = requests.get(f"{self.automatic1111_url}/sdapi/v1/memory", timeout=2)
            if response.status_code == 200:
                data = response.json()
                if 'cuda' in data:
                    cuda = data['cuda']
                    used_gb = cuda.get('used', 0) / 1024
                    total_gb = cuda.get('total', 0) / 1024
                    free_gb = cuda.get('free', 0) / 1024
                    self.logger.info(f"VRAM {stage}: used={used_gb:.1f}GB, free={free_gb:.1f}GB, total={total_gb:.1f}GB")
        except Exception as e:
            self.logger.debug(f"Не удалось получить информацию о памяти: {e}")
    
    def free_memory(self):
        """Принудительная очистка памяти Automatic1111"""
        with self.cleanup_lock:
            try:
                self.logger.info("Запуск очистки памяти Automatic1111")
                self.log_vram_usage("BEFORE_CLEANUP")
                
                # Выгружаем модель из VRAM
                response = requests.post(
                    f"{self.automatic1111_url}/sdapi/v1/unload-checkpoint",
                    timeout=10
                )
                
                if response.status_code == 200:
                    self.logger.info("Модель успешно выгружена из VRAM")
                    
                    # Ждём немного для освобождения памяти
                    time.sleep(2)
                    
                    # Опционально: перезагружаем контрольную точку (не обязательно)
                    # Это освободит память, но модель придётся загружать заново
                    if self.aggressive_cleanup:
                        response = requests.post(
                            f"{self.automatic1111_url}/sdapi/v1/reload-checkpoint",
                            timeout=5
                        )
                        if response.status_code == 200:
                            self.logger.info("Контрольная точка перезагружена")
                        else:
                            self.logger.warning(f"Не удалось перезагрузить контрольную точку: {response.status_code}")
                    
                    self.log_vram_usage("AFTER_CLEANUP")
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
                # Если прогресс 0 и нет активной задачи - готов
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
        
        # Отправляем запрос в Automatic1111
        return self._call_automatic1111(prompt_data)
    
    def _call_automatic1111(self, prompt_data):
        """Вызов Automatic1111 API с последующей очисткой памяти"""
        
        # Проверяем готовность
        is_ready, status = self.is_ready()
        if not is_ready and status != "ready":
            return {
                'success': False,
                'error': f"Automatic1111 не готов: {status}"
            }
        
        self.log_vram_usage("BEFORE_GENERATION")
        
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
            
            self.logger.info(f"Отправка запроса в Automatic1111")
            
            response = requests.post(
                f"{self.automatic1111_url}/sdapi/v1/txt2img",
                json=payload,
                timeout=120
            )
            
            self.log_vram_usage("AFTER_GENERATION")
            
            if response.status_code == 200:
                result = response.json()
                if result.get('images') and len(result['images']) > 0:
                    image_data = result['images'][0]
                    
                    # Вычисляем размер файла
                    file_size_bytes = int((len(image_data) * 3) / 4)
                    
                    # Генерируем имя файла
                    filename = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.jpg"
                    
                    # Запускаем очистку памяти в фоне (не блокируем ответ)
                    threading.Thread(target=self._delayed_cleanup, daemon=True).start()
                    
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
                        'error': "Automatic1111 не вернул изображение"
                    }
            else:
                return {
                    'success': False,
                    'error': f"Ошибка Automatic1111: {response.status_code}"
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
    
    def _delayed_cleanup(self):
        """Очистка памяти с задержкой (чтобы не блокировать ответ)"""
        time.sleep(self.cleanup_delay)
        self.free_memory()