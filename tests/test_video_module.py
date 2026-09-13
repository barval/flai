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
                    "metadata": {"num_frames": 120},
                },
            )
            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.cuda_detected = True
            mock_rm_instance.hardware.available_vram_mb = 12000
            mock_rm_instance.estimate_video_vram_needed.return_value = 8500
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            result = module.generate_video(
                {"prompt": "test video prompt", "width": 1216, "height": 704, "num_frames": 120}
            )

            assert result["success"] is True
            assert result["video_data"] == mock_b64
            assert result["file_type"] == "video/mp4"

    def test_generate_video_cpu_platform_skips_vram_check(self, mock_app):
        """CPU platform must skip the VRAM gate and proceed straight to ltx-wrapper.

        Regression: on CPU builds available_vram_mb is always 0, so the
        llama-swap wait-loop used to reject video generation with
        "requires GPU" even though ltx-wrapper runs fine on device=cpu.
        """
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
                    "generation_time": 120.0,
                    "seed": 12345,
                    "metadata": {},
                },
            )
            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.platform = "cpu"
            mock_rm_instance.hardware.cuda_detected = False
            mock_rm_instance.hardware.available_vram_mb = 0
            mock_rm_instance.hardware.total_vram_mb = 0
            mock_rm_instance.estimate_video_vram_needed.return_value = 3867
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            result = module.generate_video({"prompt": "test video prompt"})

            assert result["success"] is True
            assert result["video_data"] == mock_b64
            # CPU path must not poll VRAM / query llama-swap running state.
            mock_rm_instance._poll_vram.assert_not_called()

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

    def test_generate_video_gunicorn_error_localized(self, mock_app):
        """Raw gunicorn 'Internal Server Error' must not reach the user.

        Regression: gunicorn replies with an English HTML body when the
        ltx-wrapper worker crashes; it used to be interpolated verbatim
        into the localized 'Video generation failed: {error}' template.
        """
        from modules.video import VideoModule

        with (
            patch("modules.video.requests.get") as mock_get,
            patch("modules.video.requests.post") as mock_post,
            patch("app.resource_manager.get_resource_manager") as mock_rm,
        ):
            mock_get.return_value = MagicMock(status_code=200)

            def bad_json():
                raise ValueError("Not JSON — gunicorn HTML error page")

            response_500 = MagicMock(
                status_code=500,
                text=("<html><title>Internal Server Error</title><h1><p>Internal Server Error</p></h1>"),
            )
            response_500.json = bad_json
            mock_post.return_value = response_500
            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.cuda_detected = True
            mock_rm_instance.hardware.available_vram_mb = 12000
            mock_rm_instance.estimate_video_vram_needed.return_value = 8500
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            result = module.generate_video({"prompt": "test"}, lang="ru")

            assert result["success"] is False
            assert "Internal Server Error" not in result["error"]

    def test_generate_video_timeout(self, mock_app):
        from modules.video import VideoModule

        with (
            patch("modules.video.requests.get") as mock_get,
            patch("modules.video.requests.post") as mock_post,
            patch("app.resource_manager.get_resource_manager") as mock_rm,
        ):
            # /running returns empty so 15s wait exits immediately
            running_resp = MagicMock(status_code=200)
            running_resp.json.return_value = {"running": []}
            mock_get.return_value = running_resp
            import requests

            mock_post.side_effect = requests.exceptions.Timeout("Connection timed out")
            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.cuda_detected = True
            mock_rm_instance.hardware.available_vram_mb = 12000
            mock_rm_instance.hardware.total_vram_mb = 16311
            mock_rm_instance.estimate_video_vram_needed.return_value = 3057
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
class TestVideoModuleLowVram:
    """Test that low VRAM causes immediate error without POST to ltx-wrapper.

    Regression: previously the code logged "forcing CPU" and continued,
    causing 92-second waste + OOM in ltx-wrapper. Now it must return
    error within milliseconds when available_vram_mb < threshold.
    """

    @pytest.fixture
    def mock_app(self):
        app = MagicMock()
        app.config = {
            "LTX_VIDEO_WRAPPER_URL": "http://test-ltxvideo:7872",
            "LTX_VIDEO_MODEL": "ltxv-2b-0.9.8-distilled",
            "LTX_VIDEO_TIMEOUT": 600,
            "LLAMA_SWAP_URL": "http://test-llamaswap:8080",
            "LLAMACPP_URL": "http://test-llamacpp:8033",
        }
        app.logger = MagicMock()
        return app

    def test_low_vram_returns_error_no_post(self, mock_app, monkeypatch):
        import requests

        from modules.video import VideoModule

        # Track all POSTs to ltx-wrapper
        post_calls = {"count": 0}

        def fake_post(*args, **kwargs):
            post_calls["count"] += 1
            raise AssertionError("Should NOT POST to ltx-wrapper when VRAM is insufficient")

        # /running returns empty (no LLM models loaded) — so 15s wait exits OK
        get_resp = MagicMock(status_code=200)
        get_resp.json.return_value = {"running": []}

        monkeypatch.setattr(requests, "post", fake_post)
        monkeypatch.setattr(requests, "get", lambda *a, **kw: get_resp)

        with patch("app.resource_manager.get_resource_manager") as mock_rm:
            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.cuda_detected = True
            mock_rm_instance.hardware.available_vram_mb = 1000  # 1 GB free
            mock_rm_instance.hardware.total_vram_mb = 16311
            mock_rm_instance.estimate_video_vram_needed.return_value = 3057
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            result = module.generate_video({"prompt": "test", "width": 768, "height": 512, "num_frames": 120})

        assert result["success"] is False
        assert "VRAM" in result["error"]
        assert "1000" in result["error"] or "below" in result["error"].lower()
        assert post_calls["count"] == 0

    def test_healthy_vram_proceeds_normally(self, mock_app, monkeypatch):
        import base64

        import requests

        from modules.video import VideoModule

        # VRAM is sufficient — generation should proceed
        get_resp = MagicMock(status_code=200)
        get_resp.json.return_value = {"running": []}

        mock_b64 = base64.b64encode(b"fake mp4").decode("utf-8")
        post_resp = MagicMock(
            status_code=200,
            json=lambda: {
                "success": True,
                "video_data": mock_b64,
                "file_name": "ok.mp4",
                "file_size": 100,
                "file_type": "video/mp4",
                "generation_time": 5.0,
                "seed": 1,
                "metadata": {},
            },
        )

        monkeypatch.setattr(requests, "get", lambda *a, **kw: get_resp)
        monkeypatch.setattr(requests, "post", lambda *a, **kw: post_resp)

        with patch("app.resource_manager.get_resource_manager") as mock_rm:
            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.cuda_detected = True
            mock_rm_instance.hardware.available_vram_mb = 12000  # 12 GB free
            mock_rm_instance.hardware.total_vram_mb = 16311
            mock_rm_instance.estimate_video_vram_needed.return_value = 3057
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            result = module.generate_video({"prompt": "test", "width": 512, "height": 512, "num_frames": 120})

        assert result["success"] is True


@pytest.mark.unit
class TestVideoModuleMemoryPlanning:
    """CPU video generation: fall back to lower resolution when RAM is tight."""

    @pytest.fixture
    def cpu_rm(self):
        from types import SimpleNamespace

        rm = MagicMock()
        rm.hardware = SimpleNamespace(platform="cpu", cuda_detected=False)
        return rm

    def test_plan_large_ram_no_degradation(self, cpu_rm):
        from modules.video import VideoModule

        cpu_rm._detect_available_ram_mb.return_value = 56000
        with patch("app.resource_manager.get_resource_manager", return_value=cpu_rm):
            module = VideoModule()
            prompt_data = {"width": 768, "height": 512, "num_frames": 240}
            override, notice, error = module.plan_cpu_generation(prompt_data, lang="ru")

        assert override == {}
        assert notice is None
        assert error is None

    def test_plan_container_cap_limits_budget(self, cpu_rm):
        from modules.video import VideoModule

        # Host has plenty of RAM, but the ltxvideo container cap (32 GB) is the
        # binding constraint: 768×512×240 (~53 GB peak) must degrade.
        cpu_rm._detect_available_ram_mb.return_value = 56000
        with (
            patch("app.resource_manager.get_resource_manager", return_value=cpu_rm),
            patch.dict("os.environ", {"LTX_VIDEO_RAM_LIMIT_MB": "32768"}),
        ):
            module = VideoModule()
            prompt_data = {"width": 768, "height": 512, "num_frames": 240}
            override, notice, error = module.plan_cpu_generation(prompt_data, lang="ru")

        assert error is None
        assert notice is not None
        assert "384×256×120" in notice
        assert override == VideoModule.CPU_FALLBACK_PARAMS[0]

    def test_plan_medium_ram_falls_back(self, cpu_rm):
        from modules.video import VideoModule

        cpu_rm._detect_available_ram_mb.return_value = 30000
        with patch("app.resource_manager.get_resource_manager", return_value=cpu_rm):
            module = VideoModule()
            prompt_data = {"width": 768, "height": 512, "num_frames": 240}
            override, notice, error = module.plan_cpu_generation(prompt_data, lang="ru")

        assert error is None
        assert notice is not None
        assert "lower resolution" in notice
        assert "384×256×120" in notice
        assert override == VideoModule.CPU_FALLBACK_PARAMS[0]

    def test_plan_second_fallback_lower_frames(self, cpu_rm):
        from modules.video import VideoModule

        cpu_rm._detect_available_ram_mb.return_value = 20000
        with patch("app.resource_manager.get_resource_manager", return_value=cpu_rm):
            module = VideoModule()
            prompt_data = {"width": 768, "height": 512, "num_frames": 240}
            override, notice, error = module.plan_cpu_generation(prompt_data, lang="ru")

        assert error is None
        assert notice is not None
        assert "256×192×57" in notice
        assert override == VideoModule.CPU_FALLBACK_PARAMS[1]

    def test_plan_low_ram_impossible(self, cpu_rm):
        from modules.video import VideoModule

        cpu_rm._detect_available_ram_mb.return_value = 19000
        with patch("app.resource_manager.get_resource_manager", return_value=cpu_rm):
            module = VideoModule()
            prompt_data = {"width": 768, "height": 512, "num_frames": 240}
            override, notice, error = module.plan_cpu_generation(prompt_data, lang="ru")

        assert override is None
        assert notice is None
        assert error is not None
        assert "not possible" in error

    def test_plan_non_cpu_platform_skipped(self, cpu_rm):
        from modules.video import VideoModule

        cpu_rm.hardware.platform = "nvidia"
        cpu_rm.hardware.cuda_detected = True
        cpu_rm._detect_available_ram_mb.return_value = 20000
        with patch("app.resource_manager.get_resource_manager", return_value=cpu_rm):
            module = VideoModule()
            prompt_data = {"width": 768, "height": 512, "num_frames": 240}
            override, notice, error = module.plan_cpu_generation(prompt_data, lang="ru")

        assert override == {}
        assert notice is None
        assert error is None


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
