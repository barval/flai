# tests/test_cam.py
"""Tests for camera module."""
import pytest
import json
import time
from unittest.mock import patch, MagicMock, call

from modules.cam import CamModule


class TestCamModule:
    """Test cases for CamModule class."""

    @pytest.fixture
    def mock_app(self):
        """Create mock Flask app."""
        app = MagicMock()
        app.config = {
            'CAMERA_API_URL': 'http://test-camera:5000',
            'CAMERA_API_TIMEOUT': 10,
            'CAMERA_CHECK_INTERVAL': 30,
            'CAMERA_MAX_INIT_RETRIES': 3,
            'CAMERA_INIT_RETRY_DELAY': 1,
        }
        return app

    @pytest.fixture
    def cam_module(self, mock_app):
        """Create CamModule with mock app."""
        with patch('modules.cam.CamModule.check_availability') as mock_check:
            mock_check.return_value = False
            module = CamModule(mock_app)
            module.available = False
            return module

    def test_initialization(self, mock_app):
        """Should initialize with app config."""
        with patch('modules.cam.CamModule.check_availability') as mock_check:
            mock_check.return_value = False
            module = CamModule(mock_app)
            assert module.camera_api_url == 'http://test-camera:5000'
            assert module.timeout == 10
            assert module.check_interval == 30

    def test_room_names_loaded(self, cam_module):
        """Should load room names."""
        rooms = cam_module.get_all_rooms()
        assert 'tam' in rooms
        assert 'pri' in rooms
        assert rooms['tam'] == 'тамбур'
        assert rooms['gos'] == 'гостиная'

    def test_room_codes_reverse(self, cam_module):
        """Should have reverse mapping."""
        assert cam_module.room_codes.get('тамбур') == 'tam'
        assert cam_module.room_codes.get('гостиная') == 'gos'

    def test_get_all_rooms_returns_copy(self, cam_module):
        """Should return a copy, not original."""
        rooms = cam_module.get_all_rooms()
        rooms['new'] = 'new room'
        assert 'new room' not in cam_module.get_all_rooms()


class TestCameraPermissions:
    """Test camera permission checking."""

    @pytest.fixture
    def cam_module(self):
        """Create module for testing."""
        with patch('modules.cam.CamModule.check_availability') as mock_check:
            mock_check.return_value = False
            app = MagicMock()
            app.config = {
                'CAMERA_API_URL': 'http://test:5000',
                'CAMERA_API_TIMEOUT': 10,
            }
            return CamModule(app)

    def test_check_permission_delegates(self, cam_module):
        """Should delegate to userdb."""
        with patch('modules.cam.check_camera_permission') as mock_perm:
            mock_perm.return_value = True
            result = cam_module.check_permission('testuser', 'tam')
            assert result is True
            mock_perm.assert_called_once_with('testuser', 'tam')

    def test_get_available_rooms_no_user(self, cam_module):
        """Should return all rooms if no user."""
        rooms = cam_module.get_available_rooms(None)
        assert 'tam' in rooms

    def test_get_available_rooms_with_user_permissions(self, cam_module):
        """Should filter by user permissions."""
        with patch('modules.cam.get_user_by_login') as mock_get_user:
            mock_user = {
                'login': 'testuser',
                'camera_permissions': json.dumps(['tam', 'pri'])
            }
            mock_get_user.return_value = mock_user

            rooms = cam_module.get_available_rooms('testuser')
            assert 'tam' in rooms
            assert 'pri' in rooms
            assert 'kab' not in rooms

    def test_get_available_rooms_with_null_permissions(self, cam_module):
        """Should return all rooms if permissions is None."""
        with patch('modules.cam.get_user_by_login') as mock_get_user:
            mock_user = {
                'login': 'testuser',
                'camera_permissions': None
            }
            mock_get_user.return_value = mock_user

            rooms = cam_module.get_available_rooms('testuser')
            assert 'tam' in rooms

    def test_get_available_rooms_invalid_json(self, cam_module):
        """Should return empty if permissions invalid JSON."""
        with patch('modules.cam.get_user_by_login') as mock_get_user:
            mock_user = {
                'login': 'testuser',
                'camera_permissions': 'invalid{json}'
            }
            mock_get_user.return_value = mock_user

            rooms = cam_module.get_available_rooms('testuser')
            assert len(rooms) == 0


class TestCheckAvailability:
    """Test availability checking."""

    @pytest.fixture
    def cam_module_no_url(self):
        """Create module without camera URL."""
        with patch('modules.cam.CamModule.check_availability') as mock_check:
            mock_check.return_value = False
            app = MagicMock()
            app.config = {}
            return CamModule(app)

    def test_check_availability_no_url(self, cam_module_no_url):
        """Should return False if no camera URL."""
        result = cam_module_no_url.check_availability()
        assert result is False

    def test_check_availability_returns_cached(self):
        """Should return cached result if within interval."""
        with patch('modules.cam.CamModule.check_availability') as mock_check:
            mock_check.return_value = False
            app = MagicMock()
            app.config = {'CAMERA_API_URL': 'http://test:5000'}
            module = CamModule(app)
            module.available = True
            module.last_check = time.time()

            result = module.check_availability(force=False)
            assert result is True


class TestRoomNames:
    """Test room name constants."""

    def test_all_room_codes_defined(self):
        """All room codes should be defined."""
        with patch('modules.cam.CamModule.check_availability') as mock_check:
            mock_check.return_value = False
            app = MagicMock()
            app.config = {
                'CAMERA_API_URL': 'http://test:5000',
            }
            module = CamModule(app)

            expected_codes = ['tam', 'pri', 'kor', 'spa', 'kab', 'det', 'gos', 'kuh', 'bal']
            for code in expected_codes:
                assert code in module.room_names

    def test_room_name_keys_defined(self):
        """Room name translation keys should be defined."""
        with patch('modules.cam.CamModule.check_availability') as mock_check:
            mock_check.return_value = False
            app = MagicMock()
            app.config = {
                'CAMERA_API_URL': 'http://test:5000',
            }
            module = CamModule(app)

            assert 'tam' in module.room_name_keys
            assert module.room_name_keys['tam'] == 'room_tambour'


class TestConfigParameters:
    """Test configuration parameters."""

    def test_timeout_from_config(self):
        """Should load timeout from config."""
        with patch('modules.cam.CamModule.check_availability') as mock_check:
            mock_check.return_value = False
            app = MagicMock()
            app.config = {
                'CAMERA_API_URL': 'http://test:5000',
                'CAMERA_API_TIMEOUT': 20,
            }
            module = CamModule(app)
            assert module.timeout == 20

    def test_check_interval_from_config(self):
        """Should load check interval from config."""
        with patch('modules.cam.CamModule.check_availability') as mock_check:
            mock_check.return_value = False
            app = MagicMock()
            app.config = {
                'CAMERA_API_URL': 'http://test:5000',
                'CAMERA_CHECK_INTERVAL': 60,
            }
            module = CamModule(app)
            assert module.check_interval == 60

    def test_default_values(self):
        """Should have correct defaults."""
        with patch('modules.cam.CamModule.check_availability') as mock_check:
            mock_check.return_value = False
            app = MagicMock()
            app.config = {
                'CAMERA_API_URL': 'http://test:5000',
            }
            module = CamModule(app)
            assert module.timeout == 15
            assert module.check_interval == 30
            assert module.max_init_retries == 5
            assert module.init_retry_delay == 2