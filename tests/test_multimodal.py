# tests/test_multimodal.py
"""Tests for multimodal module."""
import pytest
import json
import base64
from unittest.mock import patch, MagicMock, PropertyMock

from modules.multimodal import MultimodalModule


class TestMultimodalModule:
    """Test cases for MultimodalModule class."""

    @pytest.fixture
    def mock_app(self):
        """Create mock Flask app."""
        app = MagicMock()
        app.config = {
            'MAX_IMAGE_WIDTH': 3840,
            'MAX_IMAGE_HEIGHT': 2160,
            'MAX_IMAGE_SIZE_MB': 5,
            'TOKEN_CHARS': 3,
            'CONTEXT_HISTORY_PERCENT': 75,
            'SD_MODEL_TYPE': 'z_image_turbo',
        }
        return app

    @pytest.fixture
    def multimodal_module(self, mock_app):
        """Create MultimodalModule with mock app."""
        with patch('modules.multimodal.LlamaCppClient') as MockClient:
            mock_client = MagicMock()
            mock_client.available = True
            MockClient.return_value = mock_client

            module = MultimodalModule(mock_app)
            return module

    def test_initialization(self, mock_app):
        """Should initialize with app."""
        with patch('modules.multimodal.LlamaCppClient') as MockClient:
            mock_client = MagicMock()
            mock_client.available = True
            MockClient.return_value = mock_client

            module = MultimodalModule(mock_app)
            assert module.available is True

    def test_image_settings_loaded(self, multimodal_module, mock_app):
        """Should load image settings from app config."""
        assert multimodal_module.image_settings['max_width'] == 3840
        assert multimodal_module.image_settings['max_height'] == 2160
        assert multimodal_module.image_settings['max_size_mb'] == 5

    def test_validate_image_valid(self, multimodal_module):
        """Should validate valid image."""
        valid_jpg = base64.b64encode(b'fake image data').decode()
        is_valid, error = multimodal_module.validate_image(
            valid_jpg, 'image/jpeg', 'test.jpg', 1024
        )
        assert is_valid is True
        assert error is None

    def test_validate_image_too_large(self, multimodal_module):
        """Should reject too large image."""
        large_image = b'x' * (6 * 1024 * 1024)
        encoded = base64.b64encode(large_image).decode()
        is_valid, error = multimodal_module.validate_image(
            encoded, 'image/jpeg', 'test.jpg', 6 * 1024 * 1024
        )
        assert is_valid is False
        assert '5 MB' in error

    def test_validate_image_unsupported_type(self, multimodal_module):
        """Should reject unsupported image type."""
        valid_jpg = base64.b64encode(b'fake').decode()
        is_valid, error = multimodal_module.validate_image(
            valid_jpg, 'image/gif', 'test.gif', 1024
        )
        assert is_valid is False


class TestGenerateImageParams:
    """Test image parameter generation."""

    @pytest.fixture
    def module_with_mock_llamacpp(self):
        """Create module with mocked llama.cpp client."""
        with patch('modules.multimodal.LlamaCppClient') as MockClient:
            mock_client = MagicMock()
            mock_client.available = True
            mock_client.chat.return_value = 'test response'
            MockClient.return_value = mock_client

            app = MagicMock()
            app.config = {
                'MAX_IMAGE_WIDTH': 3840,
                'MAX_IMAGE_HEIGHT': 2160,
                'MAX_IMAGE_SIZE_MB': 5,
                'TOKEN_CHARS': 3,
                'CONTEXT_HISTORY_PERCENT': 75,
                'SD_MODEL_TYPE': 'z_image_turbo',
            }

            module = MultimodalModule(app)
            return module

    def test_generate_image_params_unavailable(self):
        """Should return error when model unavailable."""
        with patch('modules.multimodal.LlamaCppClient') as MockClient:
            mock_client = MagicMock()
            mock_client.available = False
            MockClient.return_value = mock_client

            app = MagicMock()
            app.config = {'MAX_IMAGE_SIZE_MB': 5}

            module = MultimodalModule(app)
            result, error = module.generate_image_params('draw a cat')

            assert result is None
            assert 'unavailable' in error.lower()

    def test_generate_image_params_parses_json(self, module_with_mock_llamacpp):
        """Should parse JSON response from model."""
        json_response = json.dumps({
            'prompt': 'a cat',
            'steps': 10,
            'width': 1024,
            'height': 1024,
            'cfg_scale': 1.0,
        })
        module_with_mock_llamacpp.llamacpp.chat.return_value = json_response

        result, error = module_with_mock_llamacpp.generate_image_params('draw a cat')

        assert result is not None
        assert 'prompt' in result
        assert result['prompt'] == 'a cat'

    def test_generate_image_params_with_fallback_prompt(self, module_with_mock_llamacpp):
        """Should use original query if prompt missing in response."""
        json_response = json.dumps({
            'steps': 10,
            'width': 1024,
            'height': 1024,
        })
        module_with_mock_llamacpp.llamacpp.chat.return_value = json_response

        result, error = module_with_mock_llamacpp.generate_image_params('draw a cat')

        assert result is not None
        assert 'prompt' in result

    def test_generate_image_params_adds_negative_prompt(self, module_with_mock_llamacpp):
        """Should add empty negative_prompt if missing."""
        json_response = json.dumps({
            'prompt': 'a cat',
            'steps': 10,
        })
        module_with_mock_llamacpp.llamacpp.chat.return_value = json_response

        result, error = module_with_mock_llamacpp.generate_image_params('draw a cat')

        assert result['negative_prompt'] == ''


class TestCheckAvailability:
    """Test availability check."""

    def test_check_availability_delegates(self):
        """Should delegate to llama.cpp client."""
        with patch('modules.multimodal.LlamaCppClient') as MockClient:
            mock_client = MagicMock()
            mock_client.check_availability.return_value = True
            MockClient.return_value = mock_client

            app = MagicMock()
            app.config = {'MAX_IMAGE_SIZE_MB': 5}

            module = MultimodalModule(app)
            assert module.check_availability() is True


class TestValidateImageEdgeCases:
    """Test edge cases for image validation."""

    @pytest.fixture
    def module(self):
        """Create module for testing."""
        with patch('modules.multimodal.LlamaCppClient') as MockClient:
            mock_client = MagicMock()
            mock_client.available = True
            MockClient.return_value = mock_client

            app = MagicMock()
            app.config = {
                'MAX_IMAGE_WIDTH': 3840,
                'MAX_IMAGE_HEIGHT': 2160,
                'MAX_IMAGE_SIZE_MB': 5,
                'TOKEN_CHARS': 3,
                'CONTEXT_HISTORY_PERCENT': 75,
            }

            return MultimodalModule(app)

    def test_validate_corrupt_image(self, module):
        """Should handle corrupt image data."""
        is_valid, error = module.validate_image(
            'not-valid-base64!!!',
            'image/jpeg',
            'test.jpg',
            1024
        )
        assert is_valid is False

    def test_validate_exactly_max_size(self, module):
        """Should accept image at exactly max size."""
        max_size = 5 * 1024 * 1024
        data = b'x' * max_size
        encoded = base64.b64encode(data).decode()

        is_valid, error = module.validate_image(
            encoded, 'image/jpeg', 'test.jpg', max_size
        )
        assert is_valid is True

    def test_validate_just_over_max_size(self, module):
        """Should reject image just over max size."""
        max_size = 5 * 1024 * 1024 + 1
        data = b'x' * max_size
        encoded = base64.b64encode(data).decode()

        is_valid, error = module.validate_image(
            encoded, 'image/jpeg', 'test.jpg', max_size
        )
        assert is_valid is False