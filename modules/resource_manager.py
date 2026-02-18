# modules/resource_manager.py

import time
import threading
import requests
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class ServiceHealth:
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"

class ServiceLocation:
    LOCAL = "local"
    REMOTE = "remote"
    UNKNOWN = "unknown"

class ServiceStatus:
    """Простой статус сервиса"""
    def __init__(self, name):
        self.name = name
        self.location = ServiceLocation.UNKNOWN
        self.health = ServiceHealth.UNKNOWN
        self.available = False
        self.url = ""
        self.last_update = None
        self.error = None

class ResourceManager:
    """Упрощенный менеджер ресурсов - только проверка доступности и наличия активных запросов"""
    
    def __init__(self, app_config):
        self.config = {
            'OLLAMA_URL': app_config.get('OLLAMA_URL'),
            'AUTOMATIC1111_URL': app_config.get('AUTOMATIC1111_URL'),
            'CAMERA_API_URL': app_config.get('CAMERA_API_URL'),
        }
        
        # Статусы сервисов
        self.statuses = {
            'ollama': ServiceStatus('Ollama'),
            'automatic1111': ServiceStatus('Automatic1111'),
            'camera_api': ServiceStatus('Camera API')
        }
        
        # Кэш статусов
        self.cache_ttl = 5  # секунд
        self.last_check = 0
        
        # Счетчики активных запросов (будет обновляться извне)
        self.active_requests = {
            'ollama': 0,
            'automatic1111': 0,
            'camera_api': 0
        }
        
        logger.info("Упрощенный ResourceManager инициализирован")
    
    def update_active_requests(self, service, count):
        """Обновление счетчика активных запросов для сервиса"""
        if service in self.active_requests:
            self.active_requests[service] = max(0, count)
    
    def is_local_url(self, url):
        """Определение, находится ли URL на локальном сервере"""
        if not url or not isinstance(url, str):
            return False
        
        local_indicators = ['localhost', '127.0.0.1', '0.0.0.0', 'host.docker.internal']
        return any(indicator in url for indicator in local_indicators)
    
    def check_service_availability(self, service_name):
        """Проверка доступности конкретного сервиса"""
        status = self.statuses.get(service_name)
        if not status:
            return False
        
        url = self.config.get(f'{service_name.upper()}_URL' if service_name != 'camera_api' else 'CAMERA_API_URL')
        
        if not url:
            status.available = False
            status.health = ServiceHealth.DOWN
            status.error = 'URL не настроен'
            return False
        
        status.url = url
        status.location = ServiceLocation.LOCAL if self.is_local_url(url) else ServiceLocation.REMOTE
        
        try:
            # Разные эндпоинты для разных сервисов
            if service_name == 'ollama':
                response = requests.get(f"{url}/api/tags", timeout=2)
            elif service_name == 'automatic1111':
                response = requests.get(f"{url}/sdapi/v1/progress", timeout=2)
            else:  # camera_api
                response = requests.get(f"{url}/health", timeout=2)
            
            if response.status_code == 200:
                status.available = True
                status.health = ServiceHealth.HEALTHY
                status.error = None
            else:
                status.available = False
                status.health = ServiceHealth.DOWN
                status.error = f'HTTP {response.status_code}'
                
        except requests.exceptions.ConnectionError:
            status.available = False
            status.health = ServiceHealth.DOWN
            status.error = 'Ошибка подключения'
        except Exception as e:
            status.available = False
            status.health = ServiceHealth.DOWN
            status.error = str(e)
        
        status.last_update = datetime.now()
        return status.available
    
    def check_all_services(self):
        """Проверка всех сервисов"""
        for service in self.statuses.keys():
            self.check_service_availability(service)
        self.last_check = time.time()
    
    def can_process_request(self, request_type):
        """
        Упрощенная проверка возможности обработки запроса
        Возвращает: (можно_ли, причина)
        """
        # Обновляем статусы, если кэш устарел
        if time.time() - self.last_check > self.cache_ttl:
            self.check_all_services()
        
        if request_type == 'image':
            return self._can_process_automatic1111()
        elif request_type == 'camera':
            return self._can_process_camera()
        else:  # text, multimodal
            return self._can_process_ollama()
    
    def _can_process_ollama(self):
        """Проверка возможности обработки запроса Ollama"""
        status = self.statuses['ollama']
        
        if not status.available:
            return False, "Ollama недоступна"
        
        # Проверяем, есть ли активные запросы к Automatic1111 (если он локальный)
        auto_status = self.statuses['automatic1111']
        if auto_status.available and auto_status.location == ServiceLocation.LOCAL:
            if self.active_requests['automatic1111'] > 0:
                return False, f"Automatic1111 обрабатывает {self.active_requests['automatic1111']} запросов"
        
        return True, "ready"
    
    def _can_process_automatic1111(self):
        """Проверка возможности обработки запроса Automatic1111"""
        status = self.statuses['automatic1111']
        
        if not status.available:
            return False, "Automatic1111 недоступен"
        
        # Проверяем, есть ли активные запросы к Ollama (если она локальная)
        ollama_status = self.statuses['ollama']
        if ollama_status.available and ollama_status.location == ServiceLocation.LOCAL:
            if self.active_requests['ollama'] > 0:
                return False, f"Ollama обрабатывает {self.active_requests['ollama']} запросов"
        
        # Проверяем, не занят ли сам Automatic1111
        try:
            response = requests.get(f"{status.url}/sdapi/v1/progress", timeout=2)
            if response.status_code == 200:
                data = response.json()
                if data.get('progress', 0) > 0 or data.get('state', {}).get('active'):
                    return False, "Automatic1111 занят генерацией"
        except:
            pass
        
        return True, "ready"
    
    def _can_process_camera(self):
        """Проверка возможности обработки запроса к камерам"""
        status = self.statuses['camera_api']
        
        if not status.available:
            return False, "Сервис видеонаблюдения недоступен"
        
        # Проверяем, есть ли активные запросы к API камер
        if self.active_requests['camera_api'] > 0:
            return False, f"API камер обрабатывает {self.active_requests['camera_api']} запросов"
        
        return True, "ready"
    
    def get_status(self, service_name=None):
        """Получение статуса сервиса(ов)"""
        if time.time() - self.last_check > self.cache_ttl:
            # Запускаем проверку в фоне
            threading.Thread(target=self.check_all_services, daemon=True).start()
        
        if service_name:
            return self.statuses.get(service_name)
        
        return self.statuses