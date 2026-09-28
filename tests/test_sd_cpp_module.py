# tests/test_sd_cpp_module.py
"""Tests for SdCppModule (stable-diffusion.cpp)."""

import base64
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.unit
class TestSdCppModuleInit:
    """Test SdCppModule initialization."""

    @pytest.fixture
    def mock_app(self):
        app = MagicMock()
        app.config = {
            "SD_CPP_URL": "http://test-sd:7860",
            "SD_CPP_MODEL": "test-model.gguf",
            "SD_CPP_TIMEOUT": 180,
            "SERVICE_RETRY_ATTEMPTS": 1,
            "SERVICE_RETRY_DELAY": 0,
        }
        app.logger = MagicMock()
        return app

    def test_init_with_available_api(self, mock_app):
        from modules.sd_cpp import SdCppModule

        with patch("modules.sd_cpp.requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            module = SdCppModule(mock_app)
            assert module.available is True

    def test_init_with_unavailable_api(self, mock_app):
        from modules.sd_cpp import SdCppModule

        with patch("modules.sd_cpp.requests.get") as mock_get:
            mock_get.side_effect = Exception("Connection error")
            module = SdCppModule(mock_app)
            assert module.available is False

    def test_init_missing_url(self):
        from modules.sd_cpp import SdCppModule

        app = MagicMock()
        app.config = {"SD_CPP_URL": None, "SD_CPP_TIMEOUT": 180, "SERVICE_RETRY_ATTEMPTS": 1, "SERVICE_RETRY_DELAY": 0}
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
            "SD_CPP_URL": "http://test-sd:7860",
            "SD_CPP_MODEL": "test-model.gguf",
            "SD_CPP_TIMEOUT": 180,
            "SERVICE_RETRY_ATTEMPTS": 1,
            "SERVICE_RETRY_DELAY": 0,
        }
        app.logger = MagicMock()
        return app

    @pytest.fixture
    def mock_multimodal(self):
        multimodal = MagicMock()
        multimodal.available = True
        multimodal.generate_image_params.return_value = (
            {
                "prompt": "test prompt",
                "negative_prompt": "",
                "steps": 30,
                "width": 512,
                "height": 512,
                "cfg_scale": 7.0,
            },
            None,
        )
        return multimodal

    def test_generate_image_success(self, mock_app, mock_multimodal):
        from modules.sd_cpp import SdCppModule

        with patch("modules.sd_cpp.requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            module = SdCppModule(mock_app)
            module.set_multimodal_module(mock_multimodal)

            mock_b64 = base64.b64encode(b"test image data").decode("utf-8")
            with patch("modules.sd_cpp.requests.post") as mock_post:
                mock_post.return_value = MagicMock(status_code=200, json=lambda: {"data": [{"b64_json": mock_b64}]})

                result = module._call_wrapper(
                    {
                        "prompt": "test prompt",
                        "negative_prompt": "",
                        "steps": 30,
                        "width": 512,
                        "height": 512,
                        "cfg_scale": 7.0,
                    }
                )

                assert result["success"] is True
                assert "image_data" in result
                assert "file_name" in result

    def test_generate_image_api_error(self, mock_app, mock_multimodal):
        from modules.sd_cpp import SdCppModule

        with patch("modules.sd_cpp.requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            module = SdCppModule(mock_app)
            module.set_multimodal_module(mock_multimodal)

            with patch("modules.sd_cpp.requests.post") as mock_post:
                mock_post.return_value = MagicMock(status_code=500)
                result = module._call_wrapper(
                    {"prompt": "test", "negative_prompt": "", "steps": 30, "width": 512, "height": 512}
                )
                assert result["success"] is False
                assert "error" in result

    def test_generate_image_empty_response(self, mock_app, mock_multimodal):
        from modules.sd_cpp import SdCppModule

        with patch("modules.sd_cpp.requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            module = SdCppModule(mock_app)
            module.set_multimodal_module(mock_multimodal)

            with patch("modules.sd_cpp.requests.post") as mock_post:
                mock_post.return_value = MagicMock(status_code=200, json=lambda: {"data": []})
                result = module._call_wrapper(
                    {"prompt": "test", "negative_prompt": "", "steps": 30, "width": 512, "height": 512}
                )
                assert result["success"] is False
                assert "error" in result

    @staticmethod
    def _cpu_resource_manager():
        fake_rm = MagicMock()
        fake_rm.hardware.cuda_detected = False
        fake_rm.hardware.total_vram_mb = 0
        fake_rm.hardware.available_vram_mb = 10**9
        fake_rm.unload_llamacpp_model = MagicMock()
        fake_rm.mark_sd_busy = MagicMock()
        fake_rm.mark_sd_idle = MagicMock()
        return fake_rm

    def test_generate_image_cpu_halves_resolution(self, mock_app):
        """On CPU the requested 1024×1024 must be halved to 512×512 before the
        payload is sent, so the run fits in the request timeout."""
        from modules.sd_cpp import SdCppModule

        with patch("modules.sd_cpp.requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            module = SdCppModule(mock_app)
            mock_b64 = base64.b64encode(b"img").decode("utf-8")
            fake_rm = self._cpu_resource_manager()
            with (
                patch("app.resource_manager.get_resource_manager", return_value=fake_rm),
                patch("modules.sd_cpp.requests.post") as mock_post,
            ):
                mock_post.return_value = MagicMock(status_code=200, json=lambda: {"data": [{"b64_json": mock_b64}]})
                result = module._call_wrapper({"prompt": "test", "steps": 10, "width": 1024, "height": 1024})
                payload = mock_post.call_args.kwargs["json"]
                assert payload["width"] == 512
                assert payload["height"] == 512
                assert result["cpu_degrade"]["degraded"] is True
                assert result["cpu_degrade"]["original_size"] == (1024, 1024)
                assert result["cpu_degrade"]["new_size"] == (512, 512)

    def test_edit_image_cpu_halves_resolution(self, mock_app):
        """Editing on CPU must halve both the source image and the target size
        (64×64 → 32×32)."""
        from io import BytesIO

        from PIL import Image

        from modules.sd_cpp import SdCppModule

        with patch("modules.sd_cpp.requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            module = SdCppModule(mock_app)
            buf = BytesIO()
            Image.new("RGB", (64, 64), (10, 20, 30)).save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            fake_rm = self._cpu_resource_manager()
            with (
                patch("app.resource_manager.get_resource_manager", return_value=fake_rm),
                patch("modules.sd_cpp.requests.post") as mock_post,
            ):
                mock_post.return_value = MagicMock(status_code=200, json=lambda: {"data": [{"b64_json": b64}]})
                result = module.edit_image({"edit_prompt": "make it red"}, b64)
                payload = mock_post.call_args.kwargs["json"]
                assert payload["width"] == 32
                assert payload["height"] == 32
                assert result["cpu_degrade"]["degraded"] is True
                assert result["cpu_degrade"]["original_size"] == (64, 64)
                assert result["cpu_degrade"]["new_size"] == (32, 32)
