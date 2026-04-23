# tests/test_sd_cpp_module.py
"""Tests for SdCppModule (stable-diffusion.cpp)."""
import pytest
from unittest.mock import patch, MagicMock
import base64


@pytest.mark.unit
class TestSdCppModuleInit:
    """Test SdCppModule initialization."""

    @pytest.fixture
    def mock_app(self):
        app = MagicMock()
        app.config = {
            'SD_CPP_URL': 'http://test-sd:7860',
            'SD_CPP_MODEL': 'test-model.gguf',
            'SD_CPP_TIMEOUT': 180,
            'SERVICE_RETRY_ATTEMPTS': 1,
            'SERVICE_RETRY_DELAY': 0
        }
        app.logger = MagicMock()
        return app

    def test_init_with_available_api(self, mock_app):
        from modules.sd_cpp import SdCppModule

        with patch('modules.sd_cpp.requests.get') as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            module = SdCppModule(mock_app)
            assert module.available is True

    def test_init_with_unavailable_api(self, mock_app):
        from modules.sd_cpp import SdCppModule

        with patch('modules.sd_cpp.requests.get') as mock_get:
            mock_get.side_effect = Exception("Connection error")
            module = SdCppModule(mock_app)
            assert module.available is False

    def test_init_missing_url(self):
        from modules.sd_cpp import SdCppModule

        app = MagicMock()
        app.config = {
            'SD_CPP_URL': None,
            'SD_CPP_TIMEOUT': 180,
            'SERVICE_RETRY_ATTEMPTS': 1,
            'SERVICE_RETRY_DELAY': 0
        }
        app.logger = MagicMock()
        module = SdCppModule(app)
        assert module.available is False


@pytest.mark.unit
class TestSdCppModuleGenerate:
    """Test image generation via sd.cpp."""

    @pytest.fixture
    def mock_app(self):
        app = MagicMock()
        app.config = {
            'SD_CPP_URL': 'http://test-sd:7860',
            'SD_CPP_MODEL': 'test-model.gguf',
            'SD_CPP_TIMEOUT': 180,
            'SERVICE_RETRY_ATTEMPTS': 1,
            'SERVICE_RETRY_DELAY': 0
        }
        app.logger = MagicMock()
        return app

    @pytest.fixture
    def mock_multimodal(self):
        multimodal = MagicMock()
        multimodal.available = True
        multimodal.generate_image_params.return_value = (
            {
                'prompt': 'test prompt',
                'negative_prompt': '',
                'steps': 30,
                'width': 512,
                'height': 512,
                'cfg_scale': 7.0
            },
            None
        )
        return multimodal

    def test_generate_image_success(self, mock_app, mock_multimodal):
        from modules.sd_cpp import SdCppModule

        with patch('modules.sd_cpp.requests.get') as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            module = SdCppModule(mock_app)
            module.set_multimodal_module(mock_multimodal)

            mock_b64 = base64.b64encode(b'test image data').decode('utf-8')
            with patch('modules.sd_cpp.requests.post') as mock_post:
                mock_post.return_value = MagicMock(
                    status_code=200,
                    json=lambda: {'data': [{'b64_json': mock_b64}]}
                )

                result = module._call_wrapper({
                    'prompt': 'test prompt', 'negative_prompt': '',
                    'steps': 30, 'width': 512, 'height': 512, 'cfg_scale': 7.0
                })

                assert result['success'] is True
                assert 'image_data' in result
                assert 'file_name' in result

    def test_generate_image_unavailable(self, mock_app, mock_multimodal):
        from modules.sd_cpp import SdCppModule

        with patch('modules.sd_cpp.requests.get') as mock_get:
            mock_get.side_effect = Exception("Connection error")
            module = SdCppModule(mock_app)
            module.set_multimodal_module(mock_multimodal)

            result = module.generate_image('test prompt')
            assert result['success'] is False
            assert 'error' in result

    def test_generate_image_api_error(self, mock_app, mock_multimodal):
        from modules.sd_cpp import SdCppModule

        with patch('modules.sd_cpp.requests.get') as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            module = SdCppModule(mock_app)
            module.set_multimodal_module(mock_multimodal)

            with patch('modules.sd_cpp.requests.post') as mock_post:
                mock_post.return_value = MagicMock(status_code=500)
                result = module._call_wrapper({
                    'prompt': 'test', 'negative_prompt': '',
                    'steps': 30, 'width': 512, 'height': 512
                })
                assert result['success'] is False
                assert 'error' in result

    def test_generate_image_empty_response(self, mock_app, mock_multimodal):
        from modules.sd_cpp import SdCppModule

        with patch('modules.sd_cpp.requests.get') as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            module = SdCppModule(mock_app)
            module.set_multimodal_module(mock_multimodal)

            with patch('modules.sd_cpp.requests.post') as mock_post:
                mock_post.return_value = MagicMock(
                    status_code=200,
                    json=lambda: {'data': []}
                )
                result = module._call_wrapper({
                    'prompt': 'test', 'negative_prompt': '',
                    'steps': 30, 'width': 512, 'height': 512
                })
                assert result['success'] is False
                assert 'error' in result
