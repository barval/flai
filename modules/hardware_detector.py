# modules/hardware_detector.py

import subprocess
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
        self.gpu_info = self._detect_gpu()
        logger.info(f"HardwareDetector инициализирован. GPU: {self.gpu_info}")
    
    def _get_host_ip(self):
        """Получение IP адреса хоста"""
        try:
            # Пытаемся получить реальный IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "127.0.0.1"
    
    def _detect_gpu(self):
        """Обнаружение GPU на сервере"""
        gpu_info = {
            'available': False,
            'count': 0,
            'type': None,  # 'nvidia', 'amd', 'intel'
            'memory_mb': [],  # Список с информацией о памяти каждой GPU
            'details': []
        }
        
        # Проверка NVIDIA GPU
        try:
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                if lines and lines[0]:
                    gpu_info['available'] = True
                    gpu_info['type'] = 'nvidia'
                    gpu_info['count'] = len(lines)
                    
                    for line in lines:
                        if ',' in line:
                            name, mem = line.split(',')
                            mem_mb = int(re.sub(r'[^0-9]', '', mem))
                            gpu_info['memory_mb'].append(mem_mb)
                            gpu_info['details'].append({
                                'name': name.strip(),
                                'memory_mb': mem_mb
                            })
        except:
            pass
        
        return gpu_info
    
    def is_local_url(self, url):
        """Определение, находится ли URL на локальном сервере"""
        if not url:
            return False
        
        # Извлекаем хост из URL
        match = re.search(r'://([^:/]+)', url)
        if not match:
            return False
        
        host = match.group(1)
        
        # Проверяем localhost варианты
        if host in ['localhost', '127.0.0.1', '0.0.0.0']:
            return True
        
        # Проверяем host.docker.internal (особый случай для Docker)
        if host == 'host.docker.internal':
            return True
        
        # Проверяем, совпадает ли с IP хоста
        try:
            host_ip = socket.gethostbyname(host)
            if host_ip == self.host_ip or host_ip == '127.0.0.1':
                return True
        except:
            pass
        
        return False
    
    def detect_ollama_mode(self, ollama_url):
        """Определение режима работы Ollama (GPU/CPU)"""
        try:
            response = requests.get(f"{ollama_url}/api/ps", timeout=3)
            if response.status_code == 200:
                data = response.json()
                
                # Проверяем, на каком устройстве загружены модели
                models = data.get('models', [])
                if models:
                    # Смотрим первую модель
                    model = models[0]
                    details = model.get('details', {})
                    
                    # В Ollama можно определить по параметру 'device'
                    if 'device' in details:
                        device = details['device']
                        if 'gpu' in device.lower() or 'cuda' in device.lower():
                            return ProcessingMode.GPU_ONLY
                        elif 'cpu' in device.lower():
                            return ProcessingMode.CPU_ONLY
                    
                    # Альтернативный способ - проверяем размер модели и наличие GPU
                    if self.gpu_info['available']:
                        return ProcessingMode.HYBRID
                    else:
                        return ProcessingMode.CPU_ONLY
            else:
                # Не удалось получить информацию - проверяем наличие GPU
                if self.gpu_info['available']:
                    return ProcessingMode.HYBRID
                else:
                    return ProcessingMode.CPU_ONLY
        except:
            pass
        
        # Если не удалось определить, возвращаем на основе наличия GPU
        if self.gpu_info['available']:
            return ProcessingMode.HYBRID
        else:
            return ProcessingMode.CPU_ONLY
    
    def detect_automatic1111_mode(self, automatic1111_url):
        """Определение режима работы Automatic1111 (GPU/CPU)"""
        try:
            # Проверяем наличие GPU в Automatic1111 через API
            response = requests.get(f"{automatic1111_url}/sdapi/v1/memory", timeout=3)
            if response.status_code == 200:
                data = response.json()
                
                # Если есть информация о CUDA, значит использует GPU
                if 'cuda' in data:
                    return ProcessingMode.GPU_ONLY
                else:
                    return ProcessingMode.CPU_ONLY
            else:
                # Пробуем другой эндпоинт
                response = requests.get(f"{automatic1111_url}/sdapi/v1/progress", timeout=3)
                if response.status_code == 200:
                    # Automatic1111 работает, но не дал информацию о памяти
                    # Проверяем наличие GPU на сервере
                    if self.gpu_info['available'] and self.is_local_url(automatic1111_url):
                        return ProcessingMode.GPU_ONLY
                    else:
                        return ProcessingMode.CPU_ONLY
        except:
            pass
        
        return ProcessingMode.UNKNOWN
    
    def estimate_model_vram(self, model_name):
        """Оценка потребления VRAM моделью в GB"""
        # База знаний о моделях
        model_vram = {
            # Automatic1111 модели
            'cyberrealisticXL_v90.safetensors': 8.0,
            'cyberrealisticXL_v80.safetensors': 8.0,
            'sd_xl_base': 7.0,
            'sd_v1.5': 5.0,
            
            # Ollama модели
            'qwen3-vl:8b-instruct-q4_K_M': 5.5,
            'qwen3:8b-q4_K_M': 5.0,
            'qwen3:4b-instruct-2507-q4_K_M': 3.0,
            'llama3:8b': 5.0,
            'mistral:7b': 4.5,
        }
        
        for key, vram in model_vram.items():
            if model_name and key in model_name:
                return vram
        
        # Возвращаем значение по умолчанию в зависимости от типа
        if model_name and ('safetensors' in model_name or 'sd' in model_name.lower()):
            return 6.0  # Automatic1111 модель по умолчанию
        else:
            return 4.0  # Ollama модель по умолчанию