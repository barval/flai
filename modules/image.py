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
        
        # Настройки очистки памяти
        self.cleanup_delay = 2  # секунд после генерации
        
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
                self.consecutive_errors = 0
                return True
        except Exception as e:
            self.logger.error(f"Ошибка подключения к Automatic1111: {str(e)}")
        
        self.available = False
        return False
    
    def get_memory_info(self):
        """Получение подробной информации о памяти"""
        try:
            response = requests.get(f"{self.automatic1111_url}/sdapi/v1/memory", timeout=3)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            self.logger.debug(f"Не удалось получить информацию о памяти: {e}")
        return None
    
    def log_memory_status(self, stage=""):
        """Детальное логирование состояния памяти"""
        memory_info = self.get_memory_info()
        if not memory_info:
            self.logger.info(f"{stage} - Информация о памяти недоступна")
            return
        
        if 'cuda' in memory_info:
            cuda = memory_info['cuda']
            free_mb = cuda['system']['free'] / (1024 * 1024)
            used_mb = cuda['system']['used'] / (1024 * 1024)
            total_mb = cuda['system']['total'] / (1024 * 1024)
            
            self.logger.info(f"{stage} - CUDA VRAM: Свободно={free_mb:.0f}MB, Использовано={used_mb:.0f}MB, Всего={total_mb:.0f}MB")
            self.logger.info(f"{stage} - PyTorch: Текущий аллок={cuda['allocated']['current'] / (1024*1024):.0f}MB, Пик={cuda['allocated']['peak'] / (1024*1024):.0f}MB")
            
            if cuda['events']['oom'] > 0:
                self.logger.warning(f"{stage} - Зафиксировано OOM ошибок: {cuda['events']['oom']}")
        
        if 'ram' in memory_info:
            ram = memory_info['ram']
            free_gb = ram['free'] / (1024**3)
            self.logger.info(f"{stage} - Системная RAM: свободно {free_gb:.1f}GB")
    
    def has_enough_memory(self, required_mb=4096):  # 4GB по умолчанию для SDXL
        """Проверка, достаточно ли свободной памяти"""
        memory_info = self.get_memory_info()
        if not memory_info:
            self.logger.warning("Не удалось проверить память, предполагаем что достаточно")
            return True
        
        if 'cuda' in memory_info:
            free_mb = memory_info['cuda']['system']['free'] / (1024 * 1024)
            self.logger.info(f"Проверка памяти: свободно {free_mb:.0f}MB, требуется {required_mb}MB")
            return free_mb > required_mb
        elif 'ram' in memory_info:
            free_mb = memory_info['ram']['free'] / (1024 * 1024)
            self.logger.info(f"Проверка RAM: свободно {free_mb:.0f}MB, требуется {required_mb * 2}MB")
            return free_mb > required_mb * 2  # На CPU нужно больше памяти
        
        return True
    
    def free_memory_aggressive(self):
        """
        Агрессивная очистка памяти Automatic1111
        Возвращает True если очистка прошла успешно
        """
        try:
            self.logger.info("=" * 50)
            self.logger.info("АГРЕССИВНАЯ ОЧИСТКА ПАМЯТИ AUTOMATIC1111")
            
            # Логируем состояние до очистки
            self.log_memory_status("ДО ОЧИСТКИ")
            
            # 1. Выгружаем checkpoint
            self.logger.info("Шаг 1/4: Выгрузка checkpoint...")
            try:
                response = requests.post(
                    f"{self.automatic1111_url}/sdapi/v1/unload-checkpoint",
                    timeout=10
                )
                if response.status_code == 200:
                    self.logger.info("✓ Checkpoint успешно выгружен")
                else:
                    self.logger.warning(f"✗ Ошибка при выгрузке checkpoint: {response.status_code}")
                time.sleep(2)  # Пауза 2 секунды
            except Exception as e:
                self.logger.warning(f"✗ Исключение при выгрузке checkpoint: {e}")
            
            # 2. Очищаем кэш (если доступно)
            self.logger.info("Шаг 2/4: Очистка кэша...")
            try:
                response = requests.post(
                    f"{self.automatic1111_url}/sdapi/v1/empty-cache",
                    timeout=5
                )
                if response.status_code == 200:
                    self.logger.info("✓ Кэш успешно очищен")
                else:
                    self.logger.debug("Эндпоинт empty-cache не поддерживается")
            except Exception as e:
                self.logger.debug(f"Очистка кэша не поддерживается: {e}")
            
            # 3. Перезагружаем VAE для освобождения памяти
            self.logger.info("Шаг 3/4: Перезагрузка VAE...")
            try:
                # Получаем текущие опции
                options_response = requests.get(
                    f"{self.automatic1111_url}/sdapi/v1/options",
                    timeout=3
                )
                if options_response.status_code == 200:
                    options = options_response.json()
                    if 'sd_vae' in options:
                        current_vae = options['sd_vae']
                        self.logger.info(f"Текущий VAE: {current_vae}")
                        
                        # Устанавливаем None
                        requests.post(
                            f"{self.automatic1111_url}/sdapi/v1/options",
                            json={"sd_vae": "None"},
                            timeout=5
                        )
                        self.logger.info("VAE отключен")
                        time.sleep(1)
                        
                        # Возвращаем обратно
                        requests.post(
                            f"{self.automatic1111_url}/sdapi/v1/options",
                            json={"sd_vae": current_vae},
                            timeout=5
                        )
                        self.logger.info(f"VAE перезагружен: {current_vae}")
            except Exception as e:
                self.logger.debug(f"Ошибка при перезагрузке VAE: {e}")
            
            # 4. Финальная пауза для освобождения памяти
            self.logger.info("Шаг 4/4: Ожидание 5 секунд для полного освобождения памяти...")
            time.sleep(5)
            
            # Логируем состояние после очистки
            self.log_memory_status("ПОСЛЕ ОЧИСТКИ")
            
            # Проверяем результат
            if self.has_enough_memory():
                self.logger.info("✓ Агрессивная очистка памяти завершена успешно")
            else:
                self.logger.warning("⚠ После очистки всё ещё недостаточно памяти")
            
            self.logger.info("=" * 50)
            return True
                
        except Exception as e:
            self.logger.error(f"Ошибка при агрессивной очистке памяти: {e}")
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
        
        # Отправляем запрос в Automatic1111 (с повтором при ошибке)
        return self._call_automatic1111_with_retry(prompt_data)
    
    def _call_automatic1111_with_retry(self, prompt_data):
        """
        Вызов Automatic1111 с повтором при ошибке
        Делаем агрессивную очистку памяти и повтор
        """
        # Логируем начало генерации
        self.logger.info(f"Начало генерации изображения")
        self.log_memory_status("ПЕРЕД ГЕНЕРАЦИЕЙ")
        
        # Первая попытка
        result = self._call_automatic1111(prompt_data)
        
        # Если успех - возвращаем
        if result['success']:
            self.consecutive_errors = 0
            self.logger.info("Генерация успешна")
            self.log_memory_status("ПОСЛЕ УСПЕШНОЙ ГЕНЕРАЦИИ")
            return result
        
        # Если ошибка 500 - пробуем агрессивно очистить память и повторить
        if result.get('status_code') == 500:
            self.consecutive_errors += 1
            self.logger.warning(f"ОШИБКА 500 ПРИ ГЕНЕРАЦИИ (попытка {self.consecutive_errors})")
            self.log_memory_status("В МОМЕНТ ОШИБКИ")
            
            # Если слишком много ошибок подряд - не пытаемся
            if self.consecutive_errors > self.max_consecutive_errors:
                self.logger.error(f"СЛИШКОМ МНОГО ПОСЛЕДОВАТЕЛЬНЫХ ОШИБОК ({self.consecutive_errors})")
                return result
            
            # Агрессивно очищаем память
            self.logger.info("ЗАПУСК АГРЕССИВНОЙ ОЧИСТКИ ПАМЯТИ")
            if self.free_memory_aggressive():
                self.logger.info("Агрессивная очистка завершена, проверяем достаточно ли памяти")
                
                # Проверяем, достаточно ли памяти
                if not self.has_enough_memory():
                    self.logger.error("НЕДОСТАТОЧНО ПАМЯТИ ДАЖЕ ПОСЛЕ ОЧИСТКИ")
                    return {
                        'success': False,
                        'error': "Недостаточно памяти для генерации изображения после очистки",
                        'status_code': 507
                    }
                
                # Повторяем запрос
                self.logger.info("ПОВТОРНЫЙ ЗАПРОС ПОСЛЕ ОЧИСТКИ ПАМЯТИ")
                second_result = self._call_automatic1111(prompt_data)
                
                if second_result['success']:
                    self.logger.info("✓ ПОВТОРНЫЙ ЗАПРОС УСПЕШЕН ПОСЛЕ ОЧИСТКИ ПАМЯТИ")
                    self.consecutive_errors = 0
                    self.log_memory_status("ПОСЛЕ УСПЕШНОГО ПОВТОРА")
                    return second_result
                else:
                    self.logger.error(f"✗ ПОВТОРНЫЙ ЗАПРОС СНОВА ОШИБКА: {second_result.get('error')}")
                    self.log_memory_status("ПОСЛЕ НЕУДАЧНОГО ПОВТОРА")
                    return second_result
            else:
                self.logger.error("НЕ УДАЛОСЬ АГРЕССИВНО ОЧИСТИТЬ ПАМЯТЬ")
                return result
        
        # Для других ошибок (не 500) - просто возвращаем результат
        self.logger.warning(f"Ошибка не 500 (код {result.get('status_code')}), повтор не требуется")
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
            # Используем стандартные параметры (без уменьшения)
            steps = int(prompt_data.get("steps", 30))
            width = int(prompt_data.get("width", 512))
            height = int(prompt_data.get("height", 512))
            
            payload = {
                "prompt": prompt_data.get("prompt", ""),
                "negative_prompt": prompt_data.get("negative_prompt", ""),
                "steps": steps,
                "width": width,
                "height": height,
                "cfg_scale": float(prompt_data.get("cfg_scale", 7)),
                "sampler_name": prompt_data.get("sampler_name", "DPM++ 2M Karras"),
                "batch_size": 1,
                "enable_hr": False,  # Hi-Res отключен для стабильности
            }
            
            # Добавляем модель, если указана
            if self.model_name:
                payload["override_settings"] = {
                    "sd_model_checkpoint": self.model_name
                }
            
            self.logger.info(f"Отправка запроса в Automatic1111: steps={steps}, size={width}x{height}")
            
            response = requests.post(
                f"{self.automatic1111_url}/sdapi/v1/txt2img",
                json=payload,
                timeout=120
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('images') and len(result['images']) > 0:
                    image_data = result['images'][0]
                    
                    file_size_bytes = int((len(image_data) * 3) / 4)
                    filename = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.jpg"
                    
                    self.logger.info(f"Изображение получено, размер: {file_size_bytes/1024:.0f}KB")
                    
                    # Запускаем очистку памяти в фоне
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
                    self.logger.error("Automatic1111 не вернул изображение")
                    return {
                        'success': False,
                        'error': "Automatic1111 не вернул изображение",
                        'status_code': 500
                    }
            else:
                error_text = f"Ошибка Automatic1111: {response.status_code}"
                try:
                    error_data = response.json()
                    if 'error' in error_data:
                        error_text += f" - {error_data['error']}"
                        # Проверяем на OutOfMemory
                        if 'out of memory' in error_data.get('error', '').lower():
                            error_text = "Ошибка Automatic1111: 500 - OutOfMemoryError"
                except:
                    pass
                
                self.logger.error(error_text)
                return {
                    'success': False,
                    'error': error_text,
                    'status_code': response.status_code
                }
                
        except requests.exceptions.ConnectionError:
            self.available = False
            self.logger.error("Не удалось подключиться к Automatic1111")
            return {
                'success': False,
                'error': "Не удалось подключиться к Automatic1111",
                'status_code': 503
            }
        except Exception as e:
            self.logger.error(f"Ошибка вызова Automatic1111: {str(e)}")
            return {
                'success': False,
                'error': f"Ошибка: {str(e)}",
                'status_code': 500
            }
    
    def _delayed_cleanup(self):
        """Очистка памяти с задержкой после успешной генерации"""
        self.logger.info("Запуск отложенной очистки памяти после генерации")
        time.sleep(self.cleanup_delay)
        self.free_memory_aggressive()