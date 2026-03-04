# modules/cam.py
import logging
import requests
import base64
import time
import json
from datetime import datetime
from app.userdb import check_camera_permission  # импортируем функцию проверки

class CamModule:
    """Модуль для работы с системой видеонаблюдения"""
    
    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.camera_api_url = None
        self.available = False
        self.last_check = 0
        self.check_interval = 30
        self.timeout = 15
        
        self.room_names = {
            'tam': 'тамбур',
            'pri': 'прихожая',
            'kor': 'коридор',
            'spa': 'спальня',
            'kab': 'кабинет',
            'det': 'детская',
            'gos': 'гостиная',
            'kuh': 'кухня',
            'bal': 'балкон'
        }
        
        self.room_codes = {v: k for k, v in self.room_names.items()}
        
        if app:
            self.init_app(app)
    
    def init_app(self, app):
        self.camera_api_url = app.config.get('CAMERA_API_URL', 'http://host.docker.internal:5005')
        self.timeout = app.config.get('CAMERA_API_TIMEOUT', 15)
        self.check_interval = app.config.get('CAMERA_CHECK_INTERVAL', 30)
        self.check_availability()
        if self.available:
            self.logger.info(f"CamModule инициализирован и доступен (API: {self.camera_api_url}), таймаут: {self.timeout}с")
        else:
            self.logger.warning(f"CamModule инициализирован, но API камер недоступно ({self.camera_api_url})")
    
    def get_all_rooms(self):
        """Возвращает словарь {код: название} всех известных камер."""
        return self.room_names.copy()
    
    def check_permission(self, user_login, room_code):
        """Проверяет, имеет ли пользователь доступ к камере."""
        return check_camera_permission(user_login, room_code)
    
    def get_available_rooms(self, user_login):
        """Возвращает словарь доступных пользователю камер (код -> название)."""
        all_rooms = self.get_all_rooms()
        if user_login is None:
            return all_rooms
        # Получаем разрешённые коды
        from app.userdb import get_user_by_login
        user = get_user_by_login(user_login)
        if user and user['camera_permissions'] is not None:
            try:
                allowed_codes = json.loads(user['camera_permissions'])
                return {code: name for code, name in all_rooms.items() if code in allowed_codes}
            except:
                return {}
        return all_rooms  # если permissions == NULL, то доступны все
    
    def check_availability(self, force=False):
        current_time = time.time()
        if not force and (current_time - self.last_check) < self.check_interval:
            return self.available
        if not self.camera_api_url:
            self.available = False
            self.last_check = current_time
            return False
        
        health_endpoints = [
            f"{self.camera_api_url}/health",
            f"{self.camera_api_url}/api/health",
            f"{self.camera_api_url}/snapshot/health",
        ]
        
        for endpoint in health_endpoints:
            try:
                self.logger.debug(f"Проверка доступности API камер: {endpoint}")
                response = requests.get(endpoint, timeout=3)
                if response.status_code == 200:
                    try:
                        data = response.json()
                        if isinstance(data, dict) and data.get('status') == 'ok':
                            self.available = True
                            self.last_check = current_time
                            self.logger.info(f"API камер доступно (через {endpoint})")
                            return True
                    except:
                        self.available = True
                        self.last_check = current_time
                        self.logger.info(f"API камер доступно (через {endpoint})")
                        return True
            except requests.exceptions.ConnectionError:
                self.logger.debug(f"Ошибка подключения к {endpoint}")
                continue
            except requests.exceptions.Timeout:
                self.logger.debug(f"Таймаут при подключении к {endpoint}")
                continue
            except Exception as e:
                self.logger.debug(f"Ошибка при проверке {endpoint}: {str(e)}")
                continue
        
        try:
            response = requests.get(f"{self.camera_api_url}/rooms", timeout=3)
            if response.status_code == 200:
                self.available = True
                self.last_check = current_time
                self.logger.info(f"API камер доступно (через /rooms)")
                return True
        except:
            pass
        
        self.available = False
        self.last_check = current_time
        self.logger.warning(f"API камер недоступно по адресу {self.camera_api_url}")
        return False
    
    def get_status(self):
        self.check_availability()
        status = {
            'available': self.available,
            'url': self.camera_api_url,
            'last_check': datetime.fromtimestamp(self.last_check).isoformat() if self.last_check else None,
            'rooms': list(self.room_names.keys()),
            'room_names': self.room_names,
            'timeout': self.timeout,
            'check_interval': self.check_interval,
            'message': 'Доступно' if self.available else 'Недоступно'
        }
        if self.available:
            try:
                response = requests.get(f"{self.camera_api_url}/rooms", timeout=3)
                if response.status_code == 200:
                    api_rooms = response.json()
                    if isinstance(api_rooms, list):
                        status['available_rooms'] = api_rooms
            except:
                pass
        return status
    
    def get_room_name(self, room_code):
        return self.room_names.get(room_code, f"комната '{room_code}'")
    
    def get_room_code(self, room_name):
        room_name_lower = room_name.lower().strip()
        if room_name_lower in self.room_codes:
            return self.room_codes[room_name_lower]
        for name, code in self.room_codes.items():
            if name in room_name_lower or room_name_lower in name:
                return code
        return None
    
    def get_snapshot(self, user_login, room_code):
        """
        Получение снимка с камеры с проверкой прав доступа.
        """
        # Проверяем доступ
        if not self.check_permission(user_login, room_code):
            return {
                'success': False,
                'error': 'Доступ к данной камере запрещён',
                'status_code': 403
            }
        
        self.check_availability()
        if not self.available:
            return {
                'success': False,
                'error': "Сервис видеонаблюдения недоступен",
                'status_code': 503
            }
        
        if room_code not in self.room_names:
            code = self.get_room_code(room_code)
            if code:
                room_code = code
                self.logger.info(f"Преобразовано название '{room_code}' в код '{code}'")
            else:
                return {
                    'success': False,
                    'error': f"Неизвестная комната: {room_code}",
                    'status_code': 404,
                    'available_rooms': list(self.room_names.keys())
                }
        
        room_name = self.get_room_name(room_code)
        
        try:
            endpoints = [
                f"{self.camera_api_url}/snapshot/{room_code}",
                f"{self.camera_api_url}/api/snapshot/{room_code}",
                f"{self.camera_api_url}/camera/{room_code}",
            ]
            
            last_error = None
            for endpoint in endpoints:
                try:
                    self.logger.info(f"Запрос к камере: {endpoint}, таймаут: {self.timeout}с")
                    response = requests.get(
                        endpoint, 
                        timeout=self.timeout,
                        headers={'Accept': 'image/jpeg,image/png,*/*'}
                    )
                    
                    if response.status_code == 200:
                        content_type = response.headers.get('content-type', '')
                        
                        if 'image' in content_type:
                            image_data = base64.b64encode(response.content).decode('utf-8')
                            file_type = content_type
                        else:
                            try:
                                data = response.json()
                                if isinstance(data, dict):
                                    if 'image' in data:
                                        image_data = data['image']
                                    elif 'image_data' in data:
                                        image_data = data['image_data']
                                    elif 'base64' in data:
                                        image_data = data['base64']
                                    else:
                                        for key in ['data', 'snapshot', 'frame']:
                                            if key in data and isinstance(data[key], str):
                                                image_data = data[key]
                                                break
                                        else:
                                            continue
                                    file_type = data.get('content_type', data.get('mime_type', 'image/jpeg'))
                                else:
                                    continue
                            except:
                                continue
                        
                        file_size_bytes = int((len(image_data) * 3) / 4)
                        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                        filename = f'camera_{room_code}_{timestamp}.jpg'
                        
                        self.logger.info(f"Успешно получено изображение с камеры {room_code}")
                        
                        return {
                            'success': True,
                            'image_data': image_data,
                            'image_type': file_type,
                            'file_name': filename,
                            'file_size': file_size_bytes,
                            'room_code': room_code,
                            'room_name': room_name,
                            'timestamp': datetime.now().isoformat()
                        }
                        
                except requests.exceptions.ConnectionError:
                    last_error = "Ошибка подключения"
                    continue
                except requests.exceptions.Timeout:
                    last_error = f"Таймаут ожидания ({self.timeout}с)"
                    continue
                except Exception as e:
                    last_error = str(e)
                    continue
            
            error_msg = f"Не удалось получить изображение с камеры {room_name}"
            if last_error:
                error_msg += f": {last_error}"
            
            self.logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg,
                'status_code': 500
            }
                
        except Exception as e:
            self.logger.error(f"Ошибка при обращении к API камер: {str(e)}")
            return {
                'success': False,
                'error': f"Ошибка: {str(e)}",
                'status_code': 500
            }
    
    def get_available_rooms(self, user_login):
        """Возвращает список доступных комнат для пользователя."""
        self.check_availability()
        if not self.available:
            return {
                'success': False,
                'error': "Сервис видеонаблюдения недоступен",
                'rooms': list(self.room_names.keys()),
                'room_names': self.room_names
            }
        
        # Получаем список всех комнат (или только разрешённых)
        allowed_rooms = self.get_available_rooms(user_login)
        return {
            'success': True,
            'rooms': list(allowed_rooms.keys()),
            'room_names': allowed_rooms
        }

class CamAPI:
    """Класс для регистрации API эндпоинтов модуля камер"""
    
    @staticmethod
    def register_routes(app, cam_module):
        
        @app.route('/api/cam/status', methods=['GET'])
        def cam_status():
            if 'login' not in session:
                return jsonify({'error': 'Не авторизован'}), 401
            return jsonify(cam_module.get_status())
        
        @app.route('/api/cam/rooms', methods=['GET'])
        def cam_rooms():
            if 'login' not in session:
                return jsonify({'error': 'Не авторизован'}), 401
            user_login = session['login']
            return jsonify(cam_module.get_available_rooms(user_login))
        
        @app.route('/api/cam/snapshot/<room>', methods=['GET'])
        def cam_snapshot(room):
            if 'login' not in session:
                return jsonify({'error': 'Не авторизован'}), 401
            user_login = session['login']
            result = cam_module.get_snapshot(user_login, room)
            if result['success']:
                return jsonify({
                    'success': True,
                    'image_data': result['image_data'],
                    'image_type': result['image_type'],
                    'room_name': result['room_name'],
                    'timestamp': result.get('timestamp')
                })
            else:
                return jsonify(result), result.get('status_code', 500)
        
        @app.route('/api/cam/health', methods=['GET'])
        def cam_health():
            if 'login' not in session:
                return jsonify({'error': 'Не авторизован'}), 401
            cam_module.check_availability(force=True)
            return jsonify({
                'module': 'cam',
                'available': cam_module.available,
                'url': cam_module.camera_api_url,
                'timeout': cam_module.timeout,
                'check_interval': cam_module.check_interval,
                'timestamp': datetime.now().isoformat()
            })