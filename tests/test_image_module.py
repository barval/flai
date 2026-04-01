# tests/test_image_module.py
"""Tests for Image module (Automatic1111)."""
import pytest
from unittest.mock import patch, MagicMock
import base64


class TestImageModuleInit:
    """Test ImageModule initialization."""

    @pytest.fixture
    def mock_app(self):
        """Create mock Flask app."""
        app = MagicMock()
        app.config = {
            'AUTOMATIC1111_URL': 'http://test-sd:7860',
            'AUTOMATIC1111_MODEL': 'test-model.safetensors',
            'AUTOMATIC1111_TIMEOUT': 180
        }
        app.logger = MagicMock()
        return app

    def test_init_with_available_api(self, mock_app):
        """Test module initialization when API is available."""
        from modules.image import ImageModule
        
        with patch('modules.image.requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response
            
            module = ImageModule(mock_app)
            
            assert module.available is True
            assert module.automatic1111_url == 'http://test-sd:7860'
            assert module.timeout == 180

    def test_init_with_unavailable_api(self, mock_app):
        """Test module initialization when API is unavailable."""
        from modules.image import ImageModule
        
        with patch('modules.image.requests.get') as mock_get:
            mock_get.side_effect = Exception("Connection error")
            
            module = ImageModule(mock_app)
            
            assert module.available is False

    def test_init_missing_url(self):
        """Test module initialization without URL."""
        from modules.image import ImageModule
        
        app = MagicMock()
        app.config = {
            'AUTOMATIC1111_URL': None,
            'AUTOMATIC1111_TIMEOUT': 180
        }
        app.logger = MagicMock()
        
        module = ImageModule(app)
        
        assert module.available is False


class TestImageModuleAvailability:
    """Test ImageModule availability checks."""

    @pytest.fixture
    def mock_app(self):
        """Create mock Flask app."""
        app = MagicMock()
        app.config = {
            'AUTOMATIC1111_URL': 'http://test-sd:7860',
            'AUTOMATIC1111_TIMEOUT': 180
        }
        app.logger = MagicMock()
        return app

    def test_check_availability_success(self, mock_app):
        """Test successful availability check."""
        from modules.image import ImageModule
        
        with patch('modules.image.requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response
            
            module = ImageModule(mock_app)
            result = module.check_availability()
            
            assert result is True
            assert module.available is True

    def test_check_availability_failure(self, mock_app):
        """Test failed availability check."""
        from modules.image import ImageModule
        
        with patch('modules.image.requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 503
            mock_get.return_value = mock_response
            
            module = ImageModule(mock_app)
            result = module.check_availability()
            
            assert result is False
            assert module.available is False

    def test_check_availability_timeout(self, mock_app):
        """Test availability check timeout."""
        from modules.image import ImageModule
        
        with patch('modules.image.requests.get') as mock_get:
            mock_get.side_effect = Exception("Timeout")
            
            module = ImageModule(mock_app)
            result = module.check_availability()
            
            assert result is False

    def test_check_availability_no_url(self):
        """Test availability check without URL."""
        from modules.image import ImageModule
        
        app = MagicMock()
        app.config = {'AUTOMATIC1111_URL': None}
        app.logger = MagicMock()
        
        module = ImageModule(app)
        result = module.check_availability()
        
        assert result is False


class TestImageModuleGenerate:
    """Test image generation."""

    @pytest.fixture
    def mock_app(self):
        """Create mock Flask app."""
        app = MagicMock()
        app.config = {
            'AUTOMATIC1111_URL': 'http://test-sd:7860',
            'AUTOMATIC1111_MODEL': 'test-model.safetensors',
            'AUTOMATIC1111_TIMEOUT': 180
        }
        app.logger = MagicMock()
        return app

    @pytest.fixture
    def mock_multimodal(self):
        """Create mock multimodal module."""
        multimodal = MagicMock()
        multimodal.available = True
        multimodal.generate_image_params.return_value = (
            {'prompt': 'test prompt', 'negative_prompt': '', 'steps': 20, 'width': 512, 'height': 512},
            None
        )
        return multimodal

    def test_generate_image_success(self, mock_app, mock_multimodal):
        """Test successful image generation."""
        from modules.image import ImageModule
        
        with patch('modules.image.requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response
            
            module = ImageModule(mock_app)
            module.set_multimodal_module(mock_multimodal)
            
            with patch('modules.image.requests.post') as mock_post:
                mock_post_response = MagicMock()
                mock_post_response.status_code = 200
                mock_post_response.json.return_value = {
                    'images': [base64.b64encode(b'test image data').decode('utf-8')],
                    'parameters': {}
                }
                mock_post.return_value = mock_post_response
                
                result = module.generate_image('test prompt')
                
                assert result['success'] is True
                assert 'image_data' in result
                assert 'file_name' in result

    def test_generate_image_unavailable(self, mock_app, mock_multimodal):
        """Test image generation when service unavailable."""
        from modules.image import ImageModule
        
        with patch('modules.image.requests.get') as mock_get:
            mock_get.side_effect = Exception("Connection error")
            
            module = ImageModule(mock_app)
            module.set_multimodal_module(mock_multimodal)
            
            result = module.generate_image('test prompt')
            
            assert result['success'] is False
            assert 'error' in result

    def test_generate_image_no_multimodal(self, mock_app):
        """Test image generation without multimodal module."""
        from modules.image import ImageModule
        
        with patch('modules.image.requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response
            
            module = ImageModule(mock_app)
            # Don't set multimodal module
            
            result = module.generate_image('test prompt')
            
            assert result['success'] is False
            assert 'error' in result

    def test_generate_image_multimodal_error(self, mock_app):
        """Test image generation with multimodal error."""
        from modules.image import ImageModule
        
        with patch('modules.image.requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response
            
            module = ImageModule(mock_app)
            
            mock_multimodal = MagicMock()
            mock_multimodal.available = True
            mock_multimodal.generate_image_params.return_value = (None, 'Multimodal error')
            module.set_multimodal_module(mock_multimodal)
            
            result = module.generate_image('test prompt')
            
            assert result['success'] is False
            assert result['error'] == 'Multimodal error'

    def test_generate_image_api_error(self, mock_app, mock_multimodal):
        """Test image generation with API error."""
        from modules.image import ImageModule
        
        with patch('modules.image.requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response
            
            module = ImageModule(mock_app)
            module.set_multimodal_module(mock_multimodal)
            
            with patch('modules.image.requests.post') as mock_post:
                mock_post_response = MagicMock()
                mock_post_response.status_code = 500
                mock_post.return_value = mock_post_response
                
                result = module.generate_image('test prompt')
                
                assert result['success'] is False
                assert 'error' in result

    def test_generate_image_timeout(self, mock_app, mock_multimodal):
        """Test image generation timeout."""
        from modules.image import ImageModule
        
        with patch('modules.image.requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response
            
            module = ImageModule(mock_app)
            module.set_multimodal_module(mock_multimodal)
            
            with patch('modules.image.requests.post') as mock_post:
                mock_post.side_effect = Exception("Timeout")
                
                result = module.generate_image('test prompt')
                
                assert result['success'] is False
                assert 'error' in result

    def test_generate_image_empty_response(self, mock_app, mock_multimodal):
        """Test image generation with empty response."""
        from modules.image import ImageModule
        
        with patch('modules.image.requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response
            
            module = ImageModule(mock_app)
            module.set_multimodal_module(mock_multimodal)
            
            with patch('modules.image.requests.post') as mock_post:
                mock_post_response = MagicMock()
                mock_post_response.status_code = 200
                mock_post_response.json.return_value = {'images': []}
                mock_post.return_value = mock_post_response
                
                result = module.generate_image('test prompt')
                
                assert result['success'] is False
                assert 'error' in result
