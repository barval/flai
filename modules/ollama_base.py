# modules/ollama_base.py

import logging
import requests
import time

logger = logging.getLogger(__name__)

class OllamaBaseModule:
    """Базовый класс для всех модулей, использующих Ollama, с поддержкой повторов при ошибках"""
    
    def __init__(self):
        self.consecutive_errors = 0
        self.max_consecutive_errors = 3
        self.last_error_time = 0
        self.error_cooldown = 60  # секунд
        self.models_config = {}  # Будет установлено в дочерних классах
        self.ollama_url = None
        self.logger = logger
    
    def call_with_retry(self, func, *args, **kwargs):
        """
        Универсальный метод для вызова функций Ollama с повтором при ошибках
        Для Ollama делаем только повтор без очистки памяти
        """
        # Первая попытка
        result = func(*args, **kwargs)
        
        # Проверяем, не ошибка ли это
        if not self._is_error_response(result):
            self.consecutive_errors = 0
            return result
        
        # Если ошибка - проверяем, не слишком ли часто они происходят
        current_time = time.time()
        if current_time - self.last_error_time < self.error_cooldown:
            self.consecutive_errors += 1
        else:
            self.consecutive_errors = 1
        
        self.last_error_time = current_time
        
        # Если слишком много ошибок подряд - не пытаемся повторить
        if self.consecutive_errors > self.max_consecutive_errors:
            self.logger.error(f"Слишком много последовательных ошибок Ollama ({self.consecutive_errors}), прекращаем попытки")
            return result
        
        self.logger.warning(f"Ошибка Ollama, пробуем повторить запрос через 2 секунды...")
        time.sleep(2)  # Небольшая пауза перед повтором
        
        # Повторная попытка (просто повтор, без очистки)
        self.logger.info("Повторный запрос к Ollama")
        retry_result = func(*args, **kwargs)
        
        if self._is_error_response(retry_result):
            self.logger.error("Повторный запрос к Ollama также завершился ошибкой")
            self.consecutive_errors += 1
        else:
            self.logger.info("Повторный запрос к Ollama успешен")
            self.consecutive_errors = 0
        
        return retry_result
    
    def _is_error_response(self, response):
        """
        Проверка, является ли ответ ошибкой
        Для строковых ответов - проверяем наличие признаков ошибки
        """
        if response is None:
            return True
        
        if isinstance(response, str):
            # Проверяем типичные признаки ошибок
            error_indicators = [
                '⚠️', 'Ошибка', 'error', 'timeout', 'timed out',
                'connection refused', 'connection error', 'unavailable',
                '500', '503', '504', 'failed to connect', 'no route to host'
            ]
            response_lower = response.lower()
            return any(indicator.lower() in response_lower for indicator in error_indicators)
        
        if isinstance(response, dict):
            return response.get('error') is not None or not response.get('success', True)
        
        return False
    
    def unload_model(self, model_name=None):
        """
        Принудительная выгрузка модели из памяти Ollama
        (опционально, для экстренных случаев)
        """
        if not model_name:
            # Если модель не указана, используем текущую из конфига
            model_name = self.models_config.get('chat', {}).get('model')
            if not model_name:
                model_name = self.models_config.get('multimodal', {}).get('model')
        
        if not model_name or not self.ollama_url:
            self.logger.warning("Не указана модель или URL для выгрузки")
            return False
        
        try:
            url = f"{self.ollama_url}/api/generate"
            payload = {
                "model": model_name,
                "keep_alive": 0
            }
            
            self.logger.info(f"Выгрузка модели {model_name} из памяти Ollama")
            response = requests.post(url, json=payload, timeout=5)
            
            if response.status_code == 200:
                self.logger.info(f"Модель {model_name} успешно выгружена")
                time.sleep(1)
                return True
            else:
                self.logger.warning(f"Ошибка при выгрузке модели: {response.status_code}")
                return False
                
        except Exception as e:
            self.logger.error(f"Ошибка при выгрузке модели: {e}")
            return False
    
    def get_loaded_models(self):
        """Получение списка загруженных в память моделей"""
        if not self.ollama_url:
            return []
        
        try:
            response = requests.get(f"{self.ollama_url}/api/ps", timeout=3)
            if response.status_code == 200:
                data = response.json()
                return data.get('models', [])
        except Exception as e:
            self.logger.error(f"Ошибка при получении списка загруженных моделей: {e}")
        
        return []