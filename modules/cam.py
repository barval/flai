# modules/cam.py
# modules/cam.py
import logging
import requests
import base64
import time
import json
from datetime import datetime
from flask import current_app
from flask_babel import gettext as _
from flask_babel import force_locale
from app.userdb import check_camera_permission

class CamModule:
    """Module for interacting with CCTV camera system"""
    
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

        # Translation keys for room names
        self.room_name_keys = {
            'tam': 'room_tambour',
            'pri': 'room_hallway',
            'kor': 'room_corridor',
            'spa': 'room_bedroom',
            'kab': 'room_office',
            'det': 'room_children',
            'gos': 'room_living',
            'kuh': 'room_kitchen',
            'bal': 'room_balcony'
        }
        
        self.room_codes = {v: k for k, v in self.room_names.items()}
        
        if app:
            self.init_app(app)

    def _(self, key, lang='ru', **kwargs):
        with self.app.app_context():
            with force_locale(lang):
                return _(key, **kwargs)
    
    def init_app(self, app):
        self.app = app
        self.camera_api_url = app.config.get('CAMERA_API_URL', 'http://flai-room-snapshot-api:5000')
        self.timeout = app.config.get('CAMERA_API_TIMEOUT', 15)
        self.check_interval = app.config.get('CAMERA_CHECK_INTERVAL', 30)
        self.max_init_retries = app.config.get('CAMERA_MAX_INIT_RETRIES', 5)
        self.init_retry_delay = app.config.get('CAMERA_INIT_RETRY_DELAY', 2)

        self.logger.info(f"Initializing CamModule with Camera API URL: {self.camera_api_url}")

        # Initial availability check with retries (camera service may start slower than web app)
        for attempt in range(1, self.max_init_retries + 1):
            if self.check_availability(force=True):
                break
            if attempt < self.max_init_retries:
                self.logger.warning(f"Camera API not ready (attempt {attempt}/{self.max_init_retries}), retrying in {self.init_retry_delay}s...")
                import time
                time.sleep(self.init_retry_delay)
            else:
                self.logger.warning(f"Camera API not available after {self.max_init_retries} attempts")
        
        if self.available:
            self.logger.info(f"CamModule initialized and available (API: {self.camera_api_url}), timeout: {self.timeout}s")
        else:
            self.logger.warning(f"CamModule initialized, but camera API unavailable ({self.camera_api_url}). Will retry on each request.")
    
    def get_all_rooms(self):
        """Return dictionary {code: name} of all known cameras."""
        return self.room_names.copy()
    
    def check_permission(self, user_login, room_code):
        """Check if user has permission to access the camera."""
        return check_camera_permission(user_login, room_code)
    
    def get_available_rooms(self, user_login):
        """Return dictionary of cameras accessible to user (code -> name)."""
        all_rooms = self.get_all_rooms()
        if user_login is None:
            return all_rooms
        from app.userdb import get_user_by_login
        user = get_user_by_login(user_login)
        if user and user['camera_permissions'] is not None:
            try:
                allowed_codes = json.loads(user['camera_permissions'])
                return {code: name for code, name in all_rooms.items() if code in allowed_codes}
            except Exception:
                return {}
        return all_rooms
    
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
                self.logger.debug(f"Checking camera API availability: {endpoint}")
                response = requests.get(endpoint, timeout=3)
                if response.status_code == 200:
                    try:
                        data = response.json()
                        if isinstance(data, dict) and data.get('status') == 'ok':
                            self.available = True
                            self.last_check = current_time
                            self.logger.info(f"Camera API available (via {endpoint})")
                            return True
                    except Exception:
                        self.available = True
                        self.last_check = current_time
                        self.logger.info(f"Camera API available (via {endpoint})")
                        return True
            except requests.exceptions.ConnectionError:
                self.logger.debug(f"Connection error to {endpoint}")
                continue
            except requests.exceptions.Timeout:
                self.logger.debug(f"Timeout connecting to {endpoint}")
                continue
            except Exception as e:
                self.logger.debug(f"Error checking {endpoint}: {str(e)}")
                continue
        
        try:
            response = requests.get(f"{self.camera_api_url}/rooms", timeout=3)
            if response.status_code == 200:
                self.available = True
                self.last_check = current_time
                self.logger.info(f"Camera API available (via /rooms)")
                return True
        except Exception:
            pass
        
        self.available = False
        self.last_check = current_time
        self.logger.warning(f"Camera API unavailable at {self.camera_api_url}")
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
            'message': 'Available' if self.available else 'Unavailable'
        }
        if self.available:
            try:
                response = requests.get(f"{self.camera_api_url}/rooms", timeout=3)
                if response.status_code == 200:
                    api_rooms = response.json()
                    if isinstance(api_rooms, list):
                        status['available_rooms'] = api_rooms
            except Exception:
                pass
        return status
    
    def get_room_name(self, room_code, lang='ru'):
        """Get translated room name for the given code."""
        key = self.room_name_keys.get(room_code)
        if key:
            return self._(key, lang)
        return f"room '{room_code}'"
    
    def get_room_code(self, room_name):
        room_name_lower = room_name.lower().strip()
        if room_name_lower in self.room_codes:
            return self.room_codes[room_name_lower]
        for name, code in self.room_codes.items():
            if name in room_name_lower or room_name_lower in name:
                return code
        return None
    
    def get_snapshot(self, user_login, room_code, lang='ru'):
        if not self.check_permission(user_login, room_code):
            return {
                'success': False,
                'error': self._('Access to this camera is denied', lang),
                'status_code': 403
            }
        
        self.check_availability()
        if not self.available:
            return {
                'success': False,
                'error': self._('CCTV service unavailable', lang),
                'status_code': 503
            }
        
        if room_code not in self.room_names:
            code = self.get_room_code(room_code)
            if code:
                room_code = code
                self.logger.info(f"Converted room name '{room_code}' to code '{code}'")
            else:
                template = self._('Unknown room: {room}', lang)
                return {
                    'success': False,
                    'error': template.format(room=room_code),
                    'status_code': 404,
                    'available_rooms': list(self.room_names.keys())
                }
        
        room_name = self.get_room_name(room_code, lang)
        
        try:
            endpoints = [
                f"{self.camera_api_url}/snapshot/{room_code}",
                f"{self.camera_api_url}/api/snapshot/{room_code}",
                f"{self.camera_api_url}/camera/{room_code}",
            ]
            
            last_error = None
            for endpoint in endpoints:
                try:
                    self.logger.info(f"Request to camera: {endpoint}, timeout: {self.timeout}s")
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
                            except Exception:
                                continue
                        
                        file_size_bytes = int((len(image_data) * 3) / 4)
                        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                        # Changed filename format: timestamp_roomcode.jpg
                        filename = f'{timestamp}_{room_code}.jpg'
                        
                        self.logger.info(f"Successfully got snapshot from camera {room_code}")
                        
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
                    last_error = self._('Connection error', lang)
                    continue
                except requests.exceptions.Timeout:
                    template = self._('Timeout ({timeout}s)', lang)
                    last_error = template.format(timeout=self.timeout)
                    continue
                except Exception as e:
                    last_error = str(e)
                    continue
            
            template = self._('Failed to get snapshot from camera {room_name}', lang)
            error_msg = template.format(room_name=room_name)
            if last_error:
                error_msg += f": {last_error}"
            
            self.logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg,
                'status_code': 500
            }
                
        except Exception as e:
            self.logger.error(f"Error calling camera API: {str(e)}")
            return {
                'success': False,
                'error': f"{self._('Error', lang)}: {str(e)}",
                'status_code': 500
            }
    
    def get_available_rooms(self, user_login):
        self.check_availability()
        if not self.available:
            return {
                'success': False,
                'error': self._('CCTV service unavailable', 'ru'),  # Use 'ru' as default for internal
                'rooms': list(self.room_names.keys()),
                'room_names': self.room_names
            }
        
        allowed_rooms = self.get_available_rooms(user_login)
        return {
            'success': True,
            'rooms': list(allowed_rooms.keys()),
            'room_names': allowed_rooms
        }

class CamAPI:
    @staticmethod
    def register_routes(app, cam_module):
        from flask import session, jsonify
        from flask_babel import gettext as _

        @app.route('/api/cam/status', methods=['GET'])
        def cam_status():
            if 'login' not in session:
                return jsonify({'error': _('Not authorized')}), 401
            return jsonify(cam_module.get_status())

        @app.route('/api/cam/rooms', methods=['GET'])
        def cam_rooms():
            if 'login' not in session:
                return jsonify({'error': _('Not authorized')}), 401
            user_login = session['login']
            return jsonify(cam_module.get_available_rooms(user_login))

        @app.route('/api/cam/snapshot/<room>', methods=['GET'])
        def cam_snapshot(room):
            if 'login' not in session:
                return jsonify({'error': _('Not authorized')}), 401
            user_login = session['login']
            lang = session.get('language', 'ru')
            result = cam_module.get_snapshot(user_login, room, lang=lang)
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
                return jsonify({'error': _('Not authorized')}), 401
            cam_module.check_availability(force=True)
            return jsonify({
                'module': 'cam',
                'available': cam_module.available,
                'url': cam_module.camera_api_url,
                'timeout': cam_module.timeout,
                'check_interval': cam_module.check_interval,
                'timestamp': datetime.now().isoformat()
            })