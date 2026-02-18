# modules/hardware_detector.py

import re
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
    LOCAL = "local"
    REMOTE = "remote"
    UNKNOWN = "unknown"

class ServiceHealth(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"

class HardwareDetector:
    """Минимальный детектор для определения локации сервисов"""
    
    def __init__(self):
        self.host_ip = self._get_host_ip()
        logger.info("HardwareDetector инициализирован (упрощенная версия)")
    
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
        
        local_indicators = ['localhost', '127.0.0.1', '0.0.0.0', 'host.docker.internal']
        return any(indicator in url for indicator in local_indicators)