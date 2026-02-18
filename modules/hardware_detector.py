# modules/hardware_detector.py

import re
import requests
import socket
import logging
from enum import Enum

logger = logging.getLogger(__name__)

class ProcessingMode(Enum):
    GPU_ONLY = "gpu_only"
    CPU_ONLY = "cpu_only"
    HYBRID = "hybrid"
    UNKNOWN = "unknown"

class ServiceLocation(Enum):
    LOCAL = "local"      # На том же сервере
    REMOTE = "remote"    # На другом сервере
    UNKNOWN = "unknown"

class ServiceHealth(Enum):
    HEALTHY = "healthy"      # Работает нормально
    DEGRADED = "degraded"    # Работает, но с проблемами
    DOWN = "down"            # Недоступен
    UNKNOWN = "unknown"      # Статус неизвестен

class HardwareDetector:
    """Детектирование аппаратного обеспечения и режимов работы сервисов"""
    
    def __init__(self):
        self.host_ip = self._get_host_ip()
        # Убираем детектирование локального GPU - оно нам не нужно
        logger.info(f"HardwareDetector инициализирован")
    
    def _get_host_ip(self):
        """Получение IP адреса хоста"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "127.0.0.1"
    
    def is_local_url(self, url):
        """Определение, находится ли URL на локальном сервере"""
        if not url or not isinstance(url, str):
            return False
        
        match = re.search(r'://([^:/]+)', url)
        if not match:
            return False
        
        host = match.group(1)
        
        if host in ['localhost', '127.0.0.1', '0.0.0.0']:
            return True
        
        if host == 'host.docker.internal':
            return True
        
        try:
            host_ip = socket.gethostbyname(host)
            if host_ip == self.host_ip or host_ip == '127.0.0.1':
                return True
        except:
            pass
        
        return False
    
    def detect_ollama_mode(self, ollama_url):
        """Определение режима работы Ollama (GPU/CPU) через API"""
        if not ollama_url:
            return ProcessingMode.UNKNOWN
        
        try:
            # Пробуем получить информацию о загруженных моделях
            response = requests.get(f"{ollama_url}/api/ps", timeout=3)
            if response.status_code == 200:
                data = response.json()
                models = data.get('models', [])
                
                if models:
                    # Смотрим первую модель
                    model = models[0]
                    details = model.get('details', {})
                    
                    # В новой версии Ollama может быть информация о device
                    if 'device' in details:
                        device = details['device']
                        if isinstance(device, str):
                            if 'gpu' in device.lower() or 'cuda' in device.lower():
                                return ProcessingMode.GPU_ONLY
                            elif 'cpu' in device.lower():
                                return ProcessingMode.CPU_ONLY
                    
                    # Альтернативный способ - проверяем, есть ли в названии модели GPU-оптимизация
                    model_name = model.get('name', '').lower()
                    if 'cuda' in model_name or 'gpu' in model_name:
                        return ProcessingMode.GPU_ONLY
                    
                    # По умолчанию считаем, что если модель загружена, то она работает
                    # в том режиме, который поддерживает сервер
                    return ProcessingMode.HYBRID
                else:
                    # Нет загруженных моделей, но сервис работает
                    return ProcessingMode.HYBRID
            else:
                # Сервис недоступен
                return ProcessingMode.UNKNOWN
        except Exception as e:
            logger.debug(f"Ошибка при определении режима Ollama через API: {e}")
            return ProcessingMode.UNKNOWN
    
    def detect_automatic1111_mode(self, automatic1111_url):
        """Определение режима работы Automatic1111 (GPU/CPU) через API"""
        if not automatic1111_url:
            return ProcessingMode.UNKNOWN
        
        try:
            # Проверяем наличие GPU в Automatic1111 через API памяти
            response = requests.get(f"{automatic1111_url}/sdapi/v1/memory", timeout=3)
            if response.status_code == 200:
                data = response.json()
                
                # Если есть информация о CUDA, значит использует GPU
                if 'cuda' in data:
                    return ProcessingMode.GPU_ONLY
                elif 'ram' in data:
                    return ProcessingMode.CPU_ONLY
                else:
                    return ProcessingMode.HYBRID
            else:
                # Пробуем другой эндпоинт
                response = requests.get(f"{automatic1111_url}/sdapi/v1/progress", timeout=3)
                if response.status_code == 200:
                    # Automatic1111 работает, но не дал информацию о режиме
                    return ProcessingMode.HYBRID
                else:
                    return ProcessingMode.UNKNOWN
        except Exception as e:
            logger.debug(f"Ошибка при определении режима Automatic1111: {e}")
            return ProcessingMode.UNKNOWN
    
    def estimate_model_vram(self, model_name):
        """Оценка потребления VRAM моделью в GB (только для информации)"""
        if not model_name:
            return 4.0
            
        model_vram = {
            'cyberrealisticXL_v90.safetensors': 8.0,
            'cyberrealisticXL_v80.safetensors': 8.0,
            'sd_xl_base': 7.0,
            'sd_v1.5': 5.0,
            'qwen3-vl:8b-instruct-q4_K_M': 5.5,
            'qwen3:8b-q4_K_M': 5.0,
            'qwen3:4b-instruct-2507-q4_K_M': 3.0,
            'llama3:8b': 5.0,
            'mistral:7b': 4.5,
        }
        
        for key, vram in model_vram.items():
            if key in str(model_name):
                return vram
        
        return 4.0