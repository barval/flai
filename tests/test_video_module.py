# tests/test_video_module.py
"""Tests for VideoModule (LTX-Video)."""

import base64
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.unit
class TestVideoModuleInit:
    """Test VideoModule initialization."""

    @pytest.fixture
    def mock_app(self):
        app = MagicMock()
        app.config = {
            "LTX_VIDEO_WRAPPER_URL": "http://test-ltxvideo:7872",
            "LTX_VIDEO_MODEL": "ltxv-2b-0.9.8-distilled",
            "LTX_VIDEO_TIMEOUT": 600,
            "LLAMACPP_URL": "http://test-llamacpp:8033",
            "SERVICE_RETRY_ATTEMPTS": 1,
            "SERVICE_RETRY_DELAY": 0,
        }
        app.logger = MagicMock()
        return app

    def test_init_with_available_api(self, mock_app):
        from modules.video import VideoModule

        with patch("modules.video.requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            module = VideoModule(mock_app)
            assert module.available is True

    def test_init_with_unavailable_api(self, mock_app):
        from modules.video import VideoModule

        with patch("modules.video.requests.get") as mock_get:
            mock_get.side_effect = Exception("Connection error")
            module = VideoModule(mock_app)
            assert module.available is False

    def test_init_missing_url(self):
        from modules.video import VideoModule

        app = MagicMock()
        app.config = {
            "LTX_VIDEO_WRAPPER_URL": None,
            "LTX_VIDEO_MODEL": "ltxv-2b-0.9.8-distilled",
            "LTX_VIDEO_TIMEOUT": 600,
            "LLAMACPP_URL": "http://test-llamacpp:8033",
            "SERVICE_RETRY_ATTEMPTS": 1,
            "SERVICE_RETRY_DELAY": 0,
        }
        app.logger = MagicMock()
        module = VideoModule(app)
        assert module.available is False


@pytest.mark.unit
class TestVideoModuleGenerate:
    """Test video generation via ltx-wrapper."""

    @pytest.fixture
    def mock_app(self):
        app = MagicMock()
        app.config = {
            "LTX_VIDEO_WRAPPER_URL": "http://test-ltxvideo:7872",
            "LTX_VIDEO_MODEL": "ltxv-2b-0.9.8-distilled",
            "LTX_VIDEO_TIMEOUT": 600,
            "LLAMACPP_URL": "http://test-llamacpp:8033",
        }
        app.logger = MagicMock()
        return app

    def test_generate_video_success(self, mock_app):
        from modules.video import VideoModule

        mock_b64 = base64.b64encode(b"fake mp4 data").decode("utf-8")

        with (
            patch("modules.video.requests.get") as mock_get,
            patch("modules.video.requests.post") as mock_post,
            patch("app.resource_manager.get_resource_manager") as mock_rm,
        ):
            mock_get.return_value = MagicMock(status_code=200)
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {
                    "success": True,
                    "video_data": mock_b64,
                    "file_name": "test_video.mp4",
                    "file_size": 1024,
                    "file_type": "video/mp4",
                    "generation_time": 30.0,
                    "seed": 12345,
                    "metadata": {"num_frames": 121},
                },
            )
            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.cuda_detected = True
            mock_rm_instance.hardware.available_vram_mb = 12000
            mock_rm_instance.estimate_video_vram_needed.return_value = 8500
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            result = module.generate_video(
                {"prompt": "test video prompt", "width": 1216, "height": 704, "num_frames": 121}
            )

            assert result["success"] is True
            assert result["video_data"] == mock_b64
            assert result["file_type"] == "video/mp4"

    def test_generate_video_unavailable(self, mock_app):
        from modules.video import VideoModule

        with patch("modules.video.requests.get") as mock_get:
            mock_get.side_effect = Exception("Connection error")
            module = VideoModule(mock_app)
            result = module.generate_video({"prompt": "test"})
            assert result["success"] is False
            assert "error" in result

    def test_generate_video_api_error(self, mock_app):
        from modules.video import VideoModule

        with (
            patch("modules.video.requests.get") as mock_get,
            patch("modules.video.requests.post") as mock_post,
            patch("app.resource_manager.get_resource_manager") as mock_rm,
        ):
            mock_get.return_value = MagicMock(status_code=200)
            mock_post.return_value = MagicMock(status_code=500, text="Internal error")
            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.cuda_detected = False
            mock_rm_instance.estimate_video_vram_needed.return_value = 8500
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            result = module.generate_video({"prompt": "test"})
            assert result["success"] is False
            assert "error" in result

    def test_generate_video_timeout(self, mock_app):
        from modules.video import VideoModule

        with (
            patch("modules.video.requests.get") as mock_get,
            patch("modules.video.requests.post") as mock_post,
            patch("app.resource_manager.get_resource_manager") as mock_rm,
        ):
            mock_get.return_value = MagicMock(status_code=200)
            import requests

            mock_post.side_effect = requests.exceptions.Timeout("Connection timed out")
            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.cuda_detected = False
            mock_rm_instance.hardware.available_vram_mb = 12000
            mock_rm_instance.estimate_video_vram_needed.return_value = 8500
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            result = module.generate_video({"prompt": "test"})
            assert result["success"] is False
            assert "timeout" in result["error"].lower()

    def test_generate_video_with_image(self, mock_app):
        from modules.video import VideoModule

        mock_b64 = base64.b64encode(b"fake mp4 data").decode("utf-8")
        image_b64 = base64.b64encode(b"fake image data").decode("utf-8")

        with (
            patch("modules.video.requests.get") as mock_get,
            patch("modules.video.requests.post") as mock_post,
            patch("app.resource_manager.get_resource_manager") as mock_rm,
        ):
            mock_get.return_value = MagicMock(status_code=200)
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {
                    "success": True,
                    "video_data": mock_b64,
                    "file_name": "test_video.mp4",
                    "file_size": 1024,
                    "file_type": "video/mp4",
                    "generation_time": 30.0,
                    "seed": 12345,
                },
            )
            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.cuda_detected = True
            mock_rm_instance.hardware.available_vram_mb = 12000
            mock_rm_instance.estimate_video_vram_needed.return_value = 8500
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            result = module.generate_video({"prompt": "animate this image"}, image_data=image_b64)

            assert result["success"] is True
            assert result["video_data"] == mock_b64


@pytest.mark.unit
class TestVideoModuleResourceManager:
    """Test VideoModule VRAM management."""

    @pytest.fixture
    def mock_app(self):
        app = MagicMock()
        app.config = {
            "LTX_VIDEO_WRAPPER_URL": "http://test-ltxvideo:7872",
            "LTX_VIDEO_MODEL": "ltxv-2b-0.9.8-distilled",
            "LTX_VIDEO_TIMEOUT": 600,
            "LLAMACPP_URL": "http://test-llamacpp:8033",
        }
        app.logger = MagicMock()
        return app

    def test_vram_unload_called(self, mock_app):
        from modules.video import VideoModule

        mock_b64 = base64.b64encode(b"fake mp4 data").decode("utf-8")

        with (
            patch("modules.video.requests.get") as mock_get,
            patch("modules.video.requests.post") as mock_post,
            patch("app.resource_manager.get_resource_manager") as mock_rm,
        ):
            mock_get.return_value = MagicMock(status_code=200)
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {
                    "success": True,
                    "video_data": mock_b64,
                    "file_name": "test.mp4",
                    "file_size": 1024,
                    "file_type": "video/mp4",
                    "generation_time": 10.0,
                },
            )

            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.cuda_detected = True
            mock_rm_instance.hardware.available_vram_mb = 12000
            mock_rm_instance.estimate_video_vram_needed.return_value = 8500
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            module.generate_video({"prompt": "test"})

            mock_rm_instance.unload_llamacpp_model.assert_called()
            mock_rm_instance.mark_video_busy.assert_called_once()
            mock_rm_instance.mark_video_idle.assert_called_once()

    def test_vram_not_unloaded_when_disabled(self, mock_app):
        from modules.video import VideoModule

        mock_app.config["LLAMACPP_URL"] = None

        with (
            patch("modules.video.requests.get") as mock_get,
            patch("modules.video.requests.post") as mock_post,
            patch("app.resource_manager.get_resource_manager") as mock_rm,
        ):
            mock_get.return_value = MagicMock(status_code=200)
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {
                    "success": True,
                    "video_data": base64.b64encode(b"data").decode("utf-8"),
                    "file_name": "test.mp4",
                    "file_size": 100,
                    "file_type": "video/mp4",
                    "generation_time": 5.0,
                },
            )

            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.cuda_detected = True
            mock_rm_instance.hardware.available_vram_mb = 12000
            mock_rm_instance.estimate_video_vram_needed.return_value = 8500
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            module.generate_video({"prompt": "test"})

            mock_rm_instance.mark_video_busy.assert_called_once()
            mock_rm_instance.mark_video_idle.assert_called_once()


@pytest.mark.unit
class TestVideoModuleHealth:
    """Test health endpoint integration."""

    def test_health_check_in_init(self):
        from modules.video import VideoModule

        app = MagicMock()
        app.config = {
            "LTX_VIDEO_WRAPPER_URL": "http://test-ltxvideo:7872",
            "LTX_VIDEO_MODEL": "ltxv-2b-0.9.8-distilled",
            "LTX_VIDEO_TIMEOUT": 600,
            "LLAMACPP_URL": "http://test-llamacpp:8033",
            "SERVICE_RETRY_ATTEMPTS": 1,
            "SERVICE_RETRY_DELAY": 0,
        }
        app.logger = MagicMock()

        with patch("modules.video.requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            module = VideoModule(app)
            assert module.check_availability() is True

        with patch("modules.video.requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=503)
            module = VideoModule(app)
            assert module.check_availability() is False
