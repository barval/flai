# modules/resource_manager.py

import time
import threading
import requests
import logging
import re
import subprocess
from datetime import datetime
from .hardware_detector import HardwareDetector, ProcessingMode, ServiceLocation, ServiceHealth

logger = logging.getLogger(__name__)

class ServiceStatus:
    """Статус сервиса для отображения"""
    
    def __init__(self, name):
        self.name = name
        self.location = ServiceLocation.UNKNOWN
        self.health = ServiceHealth.UNKNOWN
        self.available = False
        self.url = ""
        self.memory = {
            'total_gb': 0,
            'used_gb': 0,
            'free_gb': 0,
            'used_percent': 0,
            'warning_threshold': 80,    # Жёлтый при 80%
            'critical_threshold': 90     # Красный при 90%
        }
        self.load = 0                    # Загрузка в процентах
        self.current_model = None        # Текущая загруженная модель
        self.last_update = None
        self.details = {}                 # Дополнительная информация

class ResourceManager:
    """Менеджер ресурсов с учётом аппаратных возможностей"""
    
    def __init__(self, app_config):
        # Сохраняем конфигурацию как обычный словарь, а не как Flask-приложение
        self.config = {
            'OLLAMA_URL': app_config.get('OLLAMA_URL'),
            'AUTOMATIC1111_URL': app_config.get('AUTOMATIC1111_URL'),
            'CAMERA_API_URL': app_config.get('CAMERA_API_URL'),
            'LLM_CHAT_MODEL': app_config.get('LLM_CHAT_MODEL'),
            'LLM_MULTIMODAL_MODEL': app_config.get('LLM_MULTIMODAL_MODEL'),
            'LLM_REASONING_MODEL': app_config.get('LLM_REASONING_MODEL'),
            'AUTOMATIC1111_MODEL': app_config.get('AUTOMATIC1111_MODEL')
        }
        
        self.hardware = HardwareDetector()
        
        # Определяем режимы работы сервисов через их API
        self.ollama_mode = self.hardware.detect_ollama_mode(
            self.config.get('OLLAMA_URL')
        )
        
        self.automatic1111_mode = self.hardware.detect_automatic1111_mode(
            self.config.get('AUTOMATIC1111_URL')
        )
        
        # Статусы сервисов
        self.statuses = {
            'ollama': ServiceStatus('Ollama'),
            'automatic1111': ServiceStatus('Automatic1111'),
            'camera_api': ServiceStatus('Camera API')
        }
        
        # Кэш статусов
        self.cache_ttl = 5  # секунд
        self.last_check = 0
        self.monitoring_active = True
        
        # Системные ресурсы (только для сервера с веб-приложением)
        self.cpu_load = 0
        self.ram_available = 0
        
        # Запускаем мониторинг
        self.start_monitoring()
        
        logger.info(f"ResourceManager инициализирован. Ollama mode: {self.ollama_mode}, "
                   f"Auto1111 mode: {self.automatic1111_mode}")
    
    def start_monitoring(self):
        """Запуск фонового мониторинга"""
        def monitor_loop():
            while self.monitoring_active:
                try:
                    self.check_all_services()
                    self._update_system_resources()
                except Exception as e:
                    logger.error(f"Ошибка в мониторинге: {e}")
                time.sleep(self.cache_ttl)
        
        thread = threading.Thread(target=monitor_loop, daemon=True)
        thread.start()
    
    def stop_monitoring(self):
        """Остановка мониторинга"""
        self.monitoring_active = False
    
    def _update_system_resources(self):
        """Обновление информации о системных ресурсах (только для сервера с веб-приложением)"""
        try:
            import psutil
            self.cpu_load = psutil.cpu_percent(interval=1)
            self.ram_available = psutil.virtual_memory().available / (1024**3)  # в GB
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"Ошибка при получении системных ресурсов: {e}")
    
    def check_all_services(self):
        """Проверка всех сервисов"""
        self._check_ollama()
        self._check_automatic1111()
        self._check_camera_api()
        self.last_check = time.time()
    
    def _check_ollama(self):
        """Проверка статуса Ollama через API"""
        status = self.statuses['ollama']
        url = self.config.get('OLLAMA_URL')
        
        if not url:
            status.health = ServiceHealth.DOWN
            status.available = False
            status.details['error'] = 'URL не настроен'
            return
        
        # Определяем локацию
        status.location = ServiceLocation.LOCAL if self.hardware.is_local_url(url) else ServiceLocation.REMOTE
        status.url = url
        
        try:
            # Проверяем доступность
            response = requests.get(f"{url}/api/tags", timeout=3)
            
            if response.status_code == 200:
                status.available = True
                data = response.json()
                
                # Получаем список моделей
                models = data.get('models', [])
                status.details['models'] = [m.get('name', 'unknown') for m in models if isinstance(m, dict)]
                
                # Получаем информацию о загруженных моделях
                try:
                    ps_response = requests.get(f"{url}/api/ps", timeout=2)
                    if ps_response.status_code == 200:
                        ps_data = ps_response.json()
                        loaded_models = ps_data.get('models', [])
                        
                        if loaded_models and isinstance(loaded_models[0], dict):
                            status.current_model = loaded_models[0].get('name')
                            
                            # Обновляем режим работы на основе актуальных данных
                            details = loaded_models[0].get('details', {})
                            if 'device' in details:
                                device = details['device']
                                if isinstance(device, str):
                                    if 'gpu' in device.lower() or 'cuda' in device.lower():
                                        self.ollama_mode = ProcessingMode.GPU_ONLY
                                    elif 'cpu' in device.lower():
                                        self.ollama_mode = ProcessingMode.CPU_ONLY
                            
                            # Оценка использования VRAM
                            size = loaded_models[0].get('size', 0)
                            if size:
                                status.memory['used_gb'] = round(size / (1024**3), 1)
                except Exception as e:
                    logger.debug(f"Ошибка при получении информации о загруженных моделях Ollama: {e}")
                
                # Оцениваем здоровье
                if len(models) == 0:
                    status.health = ServiceHealth.DEGRADED
                    status.details['warning'] = 'Нет загруженных моделей'
                else:
                    status.health = ServiceHealth.HEALTHY
                
                # Обновляем информацию о памяти (оценочно)
                self._update_ollama_memory_estimate(status)
                
            else:
                status.available = False
                status.health = ServiceHealth.DOWN
                status.details['error'] = f'HTTP {response.status_code}'
                
        except requests.exceptions.ConnectionError:
            status.available = False
            status.health = ServiceHealth.DOWN
            status.details['error'] = 'Ошибка подключения'
        except Exception as e:
            status.available = False
            status.health = ServiceHealth.DOWN
            status.details['error'] = str(e)
        
        status.last_update = datetime.now()
    
    def _update_ollama_memory_estimate(self, status):
        """Оценка использования памяти Ollama (только для информации)"""
        # Если нет информации о памяти, оставляем как есть
        if status.memory['used_gb'] == 0:
            return
        
        # Пытаемся оценить общую доступную память на основе модели
        if status.current_model:
            estimated_total = self.hardware.estimate_model_vram(status.current_model) * 1.5
            status.memory['total_gb'] = round(estimated_total, 1)
            status.memory['free_gb'] = round(max(0, status.memory['total_gb'] - status.memory['used_gb']), 1)
            status.memory['used_percent'] = round(
                (status.memory['used_gb'] / status.memory['total_gb'] * 100) 
                if status.memory['total_gb'] > 0 else 0, 1
            )
    
    def _check_automatic1111(self):
        """Проверка статуса Automatic1111 через API"""
        status = self.statuses['automatic1111']
        url = self.config.get('AUTOMATIC1111_URL')
        
        if not url:
            status.health = ServiceHealth.DOWN
            status.available = False
            status.details['error'] = 'URL не настроен'
            return
        
        status.location = ServiceLocation.LOCAL if self.hardware.is_local_url(url) else ServiceLocation.REMOTE
        status.url = url
        
        try:
            # Проверяем доступность и получаем информацию о памяти
            response = requests.get(f"{url}/sdapi/v1/memory", timeout=3)
            
            if response.status_code == 200:
                status.available = True
                data = response.json()
                
                # Парсим информацию о памяти
                if 'cuda' in data:
                    cuda = data['cuda']
                    if isinstance(cuda, dict):
                        status.memory['total_gb'] = round(cuda.get('total', 0) / 1024, 1)
                        status.memory['free_gb'] = round(cuda.get('free', 0) / 1024, 1)
                        status.memory['used_gb'] = round(status.memory['total_gb'] - status.memory['free_gb'], 1)
                        status.memory['used_percent'] = round(
                            (status.memory['used_gb'] / status.memory['total_gb'] * 100) 
                            if status.memory['total_gb'] > 0 else 0, 1
                        )
                    
                    # Обновляем режим работы на GPU
                    self.automatic1111_mode = ProcessingMode.GPU_ONLY
                
                # Получаем информацию о текущей загрузке
                progress_response = requests.get(f"{url}/sdapi/v1/progress", timeout=2)
                if progress_response.status_code == 200:
                    progress_data = progress_response.json()
                    if isinstance(progress_data, dict):
                        status.load = progress_data.get('progress', 0) * 100
                        
                        if status.load > 0:
                            status.current_model = "Генерация..."
                
                # Получаем информацию о текущей модели
                options_response = requests.get(f"{url}/sdapi/v1/options", timeout=2)
                if options_response.status_code == 200:
                    options_data = options_response.json()
                    if isinstance(options_data, dict):
                        status.current_model = options_data.get('sd_model_checkpoint', status.current_model)
                
                # Определяем здоровье
                if status.memory['used_percent'] > status.memory['critical_threshold']:
                    status.health = ServiceHealth.DEGRADED
                    status.details['warning'] = f'Критическое использование памяти: {status.memory["used_percent"]}%'
                elif status.memory['used_percent'] > status.memory['warning_threshold']:
                    status.health = ServiceHealth.DEGRADED
                    status.details['warning'] = f'Высокое использование памяти: {status.memory["used_percent"]}%'
                else:
                    status.health = ServiceHealth.HEALTHY
                
            else:
                # Пробуем альтернативный эндпоинт
                response = requests.get(f"{url}/sdapi/v1/progress", timeout=2)
                if response.status_code == 200:
                    status.available = True
                    status.health = ServiceHealth.HEALTHY
                    status.details['note'] = 'API памяти недоступно'
                    
                    # Не можем определить режим точно
                    if self.automatic1111_mode == ProcessingMode.UNKNOWN:
                        self.automatic1111_mode = ProcessingMode.HYBRID
                else:
                    status.available = False
                    status.health = ServiceHealth.DOWN
                    
        except requests.exceptions.ConnectionError:
            status.available = False
            status.health = ServiceHealth.DOWN
            status.details['error'] = 'Ошибка подключения'
        except Exception as e:
            status.available = False
            status.health = ServiceHealth.DOWN
            status.details['error'] = str(e)
        
        status.last_update = datetime.now()
    
    def _check_camera_api(self):
        """Проверка статуса Camera API"""
        status = self.statuses['camera_api']
        url = self.config.get('CAMERA_API_URL')
        
        if not url:
            status.health = ServiceHealth.DOWN
            status.available = False
            status.details['error'] = 'URL не настроен'
            return
        
        status.location = ServiceLocation.LOCAL if self.hardware.is_local_url(url) else ServiceLocation.REMOTE
        status.url = url
        
        try:
            response = requests.get(f"{url}/health", timeout=2)
            
            if response.status_code == 200:
                status.available = True
                status.health = ServiceHealth.HEALTHY
                
                # Пробуем получить список комнат
                try:
                    rooms_response = requests.get(f"{url}/rooms", timeout=2)
                    if rooms_response.status_code == 200:
                        rooms = rooms_response.json()
                        if isinstance(rooms, list):
                            status.details['rooms'] = rooms
                        elif isinstance(rooms, dict) and 'rooms' in rooms:
                            status.details['rooms'] = rooms['rooms']
                        else:
                            status.details['rooms'] = []
                    else:
                        status.details['rooms'] = []
                except Exception as e:
                    logger.debug(f"Ошибка получения списка комнат: {e}")
                    status.details['rooms'] = []
            else:
                status.available = False
                status.health = ServiceHealth.DOWN
                
        except Exception as e:
            status.available = False
            status.health = ServiceHealth.DOWN
            status.details['error'] = str(e)
        
        status.last_update = datetime.now()
    
    def can_process_request(self, request_type, model_name=None):
        """
        Проверка возможности обработки запроса
        Возвращает: (можно_ли, причина, ожидаемое_время_сек)
        """
        
        if request_type == 'image':
            return self._can_process_automatic1111(model_name)
        elif request_type in ['text', 'reasoning', 'multimodal']:
            return self._can_process_ollama(request_type, model_name)
        elif request_type == 'camera':
            return "go", "ready", 0
        
        return "go", "ready", 0
    
    def _can_process_ollama(self, request_type, model_name=None):
        """Проверка возможности обработки запроса Ollama"""
        
        # Получаем статус Ollama
        status = self.statuses['ollama']
        
        if not status.available:
            return "wait", "Ollama недоступна", 5
        
        # Проверяем в зависимости от режима работы (полученного из API)
        if self.ollama_mode == ProcessingMode.GPU_ONLY:
            # Только GPU - нужна VRAM
            if status.memory['free_gb'] < 2:  # Минимум 2GB свободно
                return "wait", f"Мало VRAM: {status.memory['free_gb']:.1f}GB свободно", 10
            else:
                return "go", "ready", 0
        
        elif self.ollama_mode == ProcessingMode.CPU_ONLY:
            # Только CPU - нужна RAM
            if self.ram_available < 4:  # Минимум 4GB RAM
                return "wait", f"Мало RAM: {self.ram_available:.1f}GB свободно", 5
            else:
                return "go", "ready", 0
        
        # HYBRID или UNKNOWN - считаем, что готов
        return "go", "ready", 0
    
    def _can_process_automatic1111(self, model_name=None):
        """Проверка возможности обработки запроса Automatic1111"""
        
        if not model_name:
            model_name = self.config.get('AUTOMATIC1111_MODEL', '')
        
        model_vram = self.hardware.estimate_model_vram(model_name)
        status = self.statuses['automatic1111']
        
        if not status.available:
            return "wait", "Automatic1111 недоступен", 10
        
        # Проверяем режим работы (полученный из API)
        if self.automatic1111_mode == ProcessingMode.GPU_ONLY:
            if status.memory['free_gb'] < model_vram:
                return "wait", f"Нужно {model_vram}GB VRAM, свободно {status.memory['free_gb']:.1f}GB", 15
            else:
                return "go", "ready", 0
        
        elif self.automatic1111_mode == ProcessingMode.CPU_ONLY:
            # На CPU работает очень медленно, но возможно
            if self.ram_available < model_vram * 2:  # На CPU нужно больше RAM
                return "wait", f"Мало RAM для CPU режима", 10
            else:
                return "go", "ready (CPU mode)", 0
        
        # HYBRID или UNKNOWN - проверяем наличие информации о памяти
        if status.memory['free_gb'] > 0:
            if status.memory['free_gb'] < model_vram:
                return "wait", f"Нужно {model_vram}GB VRAM, свободно {status.memory['free_gb']:.1f}GB", 15
            else:
                return "go", "ready", 0
        
        return "go", "ready", 0
    
    def get_status(self, service_name=None):
        """Получение статуса сервиса(ов)"""
        if service_name:
            return self.statuses.get(service_name)
        
        # Обновляем, если кэш устарел
        if time.time() - self.last_check > self.cache_ttl:
            # Запускаем проверку в фоне, чтобы не блокировать
            threading.Thread(target=self.check_all_services, daemon=True).start()
        
        return self.statuses
    
    def get_system_info(self):
        """Получение информации о системе (только для сервера с веб-приложением)"""
        system_info = {
            'system': {
                'cpu_load': self.cpu_load,
                'ram_available_gb': round(self.ram_available, 1)
            }
        }
        
        # Добавляем информацию о GPU, ТОЛЬКО если nvidia-smi доступен на сервере с веб-приложением
        try:
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader'],
                capture_output=True, text=True, timeout=2
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                if lines and lines[0]:
                    gpu_info = {
                        'available': True,
                        'count': len(lines),
                        'type': 'nvidia',
                        'memory_mb': [],
                        'details': []
                    }
                    
                    for line in lines:
                        if line and ',' in line:
                            parts = line.split(',')
                            if len(parts) >= 2:
                                name = parts[0].strip()
                                mem_str = parts[1].strip()
                                mem_digits = re.sub(r'[^0-9]', '', mem_str)
                                if mem_digits:
                                    mem_mb = int(mem_digits)
                                    gpu_info['memory_mb'].append(mem_mb)
                                    gpu_info['details'].append({
                                        'name': name,
                                        'memory_mb': mem_mb
                                    })
                    
                    system_info['gpu'] = gpu_info
                else:
                    system_info['gpu'] = {'available': False}
            else:
                system_info['gpu'] = {'available': False}
        except:
            system_info['gpu'] = {'available': False}
        
        # Добавляем информацию о режимах работы сервисов (из их API)
        system_info['services'] = {
            'ollama': {
                'mode': self.ollama_mode.value if self.ollama_mode else 'unknown',
                'location': self.statuses['ollama'].location.value if self.statuses['ollama'] else 'unknown'
            },
            'automatic1111': {
                'mode': self.automatic1111_mode.value if self.automatic1111_mode else 'unknown',
                'location': self.statuses['automatic1111'].location.value if self.statuses['automatic1111'] else 'unknown'
            }
        }
        
        return system_info