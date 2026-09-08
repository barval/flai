# tests/test_video_module.py
"""Tests for VideoModule (Wan2.2 via sd-wrapper)."""

import base64
from unittest.mock import MagicMock, patch

import pytest


def _video_config(**overrides):
    """Base app.config for VideoModule tests (Wan sd-wrapper keys)."""
    config = {
        "VIDEO_WRAPPER_URL": "http://test-sd:7861",
        "VIDEO_MODEL": "wan2.2-ti2v-5b-turbo",
        "VIDEO_TIMEOUT": 600,
        "LLAMA_SWAP_URL": "http://test-llamaswap:8080",
    }
    config.update(overrides)
    return config


@pytest.mark.unit
class TestVideoModuleWanPresets:
    """Wan2.2 video presets, degradation chain, payload, RAM planning."""

    @pytest.fixture
    def mock_app(self):
        app = MagicMock()
        app.config = _video_config()
        app.logger = MagicMock()
        return app

    def test_default_params_are_wan(self):
        from modules.video import DEFAULT_VIDEO_PARAMS

        assert DEFAULT_VIDEO_PARAMS["width"] == 768
        assert DEFAULT_VIDEO_PARAMS["height"] == 448
        assert DEFAULT_VIDEO_PARAMS["num_frames"] == 121
        assert DEFAULT_VIDEO_PARAMS["frame_rate"] == 24

    def test_sanitize_video_params_enforces_wan_constraints(self):
        from modules.video import sanitize_video_params

        out = sanitize_video_params({"width": 1000, "height": 500, "num_frames": 300})
        assert out["num_frames"] == 121
        assert out["width"] % 32 == 0
        assert out["height"] % 32 == 0

    def test_init_uses_video_wrapper_url(self, mock_app):
        from modules.video import VideoModule

        with patch("modules.video.requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            module = VideoModule(mock_app)
            assert module.wrapper_url == "http://test-sd:7861"
            assert module.model_type == "wan2.2-ti2v-5b-turbo"

    def test_cpu_degradation_chain(self, mock_app):
        from modules.video import VideoModule

        frames = [fb["num_frames"] for fb in VideoModule.CPU_FALLBACK_PARAMS]
        assert frames == [81, 57, 49]
        for fb in VideoModule.CPU_FALLBACK_PARAMS:
            assert fb["width"] % 32 == 0 and fb["height"] % 32 == 0
            assert (fb["num_frames"] - 1) % 4 == 0

    def test_generate_video_success_webm(self, mock_app):
        from modules.video import VideoModule

        mock_b64 = base64.b64encode(b"fake webm data").decode("utf-8")
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
                    "file_name": "wan_video.webm",
                    "file_size": 1024,
                    "file_type": "video/webm",
                    "generation_time": 30.0,
                    "seed": 12345,
                    "metadata": {"num_frames": 81},
                },
            )
            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.cuda_detected = True
            mock_rm_instance.hardware.available_vram_mb = 14000
            mock_rm_instance.hardware.total_vram_mb = 16384
            mock_rm_instance.estimate_video_vram_needed.return_value = 8000
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            result = module.generate_video({"prompt": "test video", "width": 640, "height": 384, "num_frames": 81})
            assert result["success"] is True
            assert result["file_type"] == "video/webm"

    def test_8gb_tier_caps_to_first_degradation_preset(self, mock_app):
        """8/10 GB GPUs (total VRAM < 10000) must cap to 640×384@121."""
        from modules.video import VideoModule

        mock_b64 = base64.b64encode(b"fake webm data").decode("utf-8")
        with (
            patch("modules.video.requests.get") as mock_get,
            patch("modules.video.requests.post") as mock_post,
            patch("app.resource_manager.get_resource_manager") as mock_rm,
        ):
            mock_get.return_value = MagicMock(status_code=200)
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {
                    "video_data": mock_b64,
                    "file_name": "wan_video.webm",
                    "file_type": "video/webm",
                    "generation_time": 30.0,
                },
            )
            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.cuda_detected = True
            mock_rm_instance.hardware.available_vram_mb = 12000
            mock_rm_instance.hardware.total_vram_mb = 8192
            mock_rm_instance.estimate_video_vram_needed.return_value = 8000
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            result = module.generate_video({"prompt": "test video", "width": 832, "height": 480, "num_frames": 121})
            assert result["success"] is True
            sent_payload = mock_post.call_args.kwargs["json"]
            assert sent_payload["width"] == 640
            assert sent_payload["height"] == 384
            assert sent_payload["num_frames"] == 121

    def test_dimensions_snapped_to_multiple_of_32(self, mock_app):
        """Non-multiple-of-32 dims must be snapped down before the payload."""
        from modules.video import VideoModule

        mock_b64 = base64.b64encode(b"fake webm data").decode("utf-8")
        with (
            patch("modules.video.requests.get") as mock_get,
            patch("modules.video.requests.post") as mock_post,
            patch("app.resource_manager.get_resource_manager") as mock_rm,
        ):
            mock_get.return_value = MagicMock(status_code=200)
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {
                    "video_data": mock_b64,
                    "file_name": "wan_video.webm",
                    "file_type": "video/webm",
                },
            )
            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.cuda_detected = True
            mock_rm_instance.hardware.available_vram_mb = 14000
            mock_rm_instance.hardware.total_vram_mb = 16384
            mock_rm_instance.estimate_video_vram_needed.return_value = 8000
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            result = module.generate_video({"prompt": "test video", "width": 770, "height": 450, "num_frames": 200})
            assert result["success"] is True
            sent_payload = mock_post.call_args.kwargs["json"]
            assert sent_payload["width"] == 768
            assert sent_payload["height"] == 448
            assert sent_payload["num_frames"] == 121


@pytest.mark.unit
class TestVideoModuleInit:
    """Test VideoModule initialization."""

    @pytest.fixture
    def mock_app(self):
        app = MagicMock()
        app.config = _video_config()
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
        app.config = _video_config(VIDEO_WRAPPER_URL=None)
        app.logger = MagicMock()
        module = VideoModule(app)
        assert module.available is False


@pytest.mark.unit
class TestVideoModuleGenerate:
    """Test video generation via the Wan sd-wrapper."""

    @pytest.fixture
    def mock_app(self):
        app = MagicMock()
        app.config = _video_config()
        app.logger = MagicMock()
        return app

    def test_generate_video_success(self, mock_app):
        from modules.video import VideoModule

        mock_b64 = base64.b64encode(b"fake webm data").decode("utf-8")

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
                    "file_name": "wan_video.webm",
                    "file_size": 1024,
                    "file_type": "video/webm",
                    "generation_time": 30.0,
                    "seed": 12345,
                    "metadata": {"num_frames": 121},
                },
            )
            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.cuda_detected = True
            mock_rm_instance.hardware.available_vram_mb = 12000
            mock_rm_instance.hardware.total_vram_mb = 16384
            mock_rm_instance.estimate_video_vram_needed.return_value = 8500
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            result = module.generate_video(
                {"prompt": "test video prompt", "width": 768, "height": 448, "num_frames": 121}
            )

            assert result["success"] is True
            assert result["video_data"] == mock_b64
            assert result["file_type"] == "video/webm"

    def test_generate_video_cpu_platform_skips_vram_check(self, mock_app):
        """CPU platform must skip the VRAM gate and proceed straight to the wrapper.

        Regression: on CPU builds available_vram_mb is always 0, so the
        llama-swap wait-loop used to reject video generation with
        "requires GPU" even though the wrapper runs fine (te=cpu).
        """
        from modules.video import VideoModule

        mock_b64 = base64.b64encode(b"fake webm data").decode("utf-8")

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
                    "file_name": "wan_video.webm",
                    "file_size": 1024,
                    "file_type": "video/webm",
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
            mock_rm_instance.estimate_video_vram_needed.return_value = 8000
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            result = module.generate_video({"prompt": "test video prompt"})

            assert result["success"] is True
            assert result["video_data"] == mock_b64
            # CPU path must not poll VRAM / query llama-swap running state.
            mock_rm_instance._poll_vram.assert_not_called()
            # CPU platform must advertise use_gpu=False so the wrapper runs full CPU offload.
            sent_payload = mock_post.call_args.kwargs["json"]
            assert sent_payload["use_gpu"] is False

    def test_payload_sends_use_gpu_true_on_gpu(self, mock_app):
        """GPU platform with enough free VRAM must advertise use_gpu=True to the wrapper."""
        from modules.video import VideoModule

        mock_b64 = base64.b64encode(b"fake webm data").decode("utf-8")
        with (
            patch("modules.video.requests.get") as mock_get,
            patch("modules.video.requests.post") as mock_post,
            patch("app.resource_manager.get_resource_manager") as mock_rm,
        ):
            running_resp = MagicMock(status_code=200)
            running_resp.json.return_value = {"running": []}
            mock_get.return_value = running_resp
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {
                    "video_data": mock_b64,
                    "file_name": "wan_video.webm",
                    "file_type": "video/webm",
                },
            )
            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.cuda_detected = True
            mock_rm_instance.hardware.available_vram_mb = 14000
            mock_rm_instance.hardware.total_vram_mb = 16384
            mock_rm_instance.estimate_video_vram_needed.return_value = 8000
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            result = module.generate_video({"prompt": "test video", "width": 768, "height": 448, "num_frames": 81})

            assert result["success"] is True
            sent_payload = mock_post.call_args.kwargs["json"]
            assert sent_payload["use_gpu"] is True

    def test_generate_video_unavailable(self, mock_app):
        import requests

        from modules.video import VideoModule

        with (
            patch("modules.video.requests.get") as mock_get,
            patch("app.resource_manager.get_resource_manager") as mock_rm,
            patch("modules.video.requests.post") as mock_post,
        ):
            mock_get.side_effect = Exception("Connection error")
            mock_post.side_effect = requests.exceptions.ConnectionError("could not connect")
            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.platform = "cpu"
            mock_rm_instance.hardware.cuda_detected = False
            mock_rm_instance.estimate_video_vram_needed.return_value = 8000
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            assert module.available is False
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
            mock_post.return_value = MagicMock(
                status_code=500, text="Internal error", json=lambda: {"error": "Internal error"}
            )
            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.cuda_detected = True
            mock_rm_instance.hardware.available_vram_mb = 12000
            mock_rm_instance.hardware.total_vram_mb = 16384
            mock_rm_instance.estimate_video_vram_needed.return_value = 8500
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            result = module.generate_video({"prompt": "test"})
            assert result["success"] is False
            assert "error" in result

    def test_generate_video_html_error_localized(self, mock_app):
        """Raw HTML 'Internal Server Error' must not reach the user.

        Regression: a crashed worker replies with an English HTML body; it
        used to be interpolated verbatim into the localized
        'Video generation failed: {error}' template.
        """
        from modules.video import VideoModule

        with (
            patch("modules.video.requests.get") as mock_get,
            patch("modules.video.requests.post") as mock_post,
            patch("app.resource_manager.get_resource_manager") as mock_rm,
        ):
            mock_get.return_value = MagicMock(status_code=200)

            def bad_json():
                raise ValueError("Not JSON — HTML error page")

            response_500 = MagicMock(
                status_code=500,
                text=("<html><title>Internal Server Error</title><h1><p>Internal Server Error</p></h1>"),
            )
            response_500.json = bad_json
            mock_post.return_value = response_500
            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.cuda_detected = True
            mock_rm_instance.hardware.available_vram_mb = 12000
            mock_rm_instance.hardware.total_vram_mb = 16384
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
            mock_rm_instance.estimate_video_vram_needed.return_value = 8000
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            result = module.generate_video({"prompt": "test"})
            assert result["success"] is False
            assert "timeout" in result["error"].lower()

    def test_generate_video_with_image(self, mock_app):
        from modules.video import VideoModule

        mock_b64 = base64.b64encode(b"fake webm data").decode("utf-8")
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
                    "file_name": "wan_video.webm",
                    "file_size": 1024,
                    "file_type": "video/webm",
                    "generation_time": 30.0,
                    "seed": 12345,
                },
            )
            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.cuda_detected = True
            mock_rm_instance.hardware.available_vram_mb = 12000
            mock_rm_instance.hardware.total_vram_mb = 16384
            mock_rm_instance.estimate_video_vram_needed.return_value = 8500
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            result = module.generate_video({"prompt": "animate this image"}, image_data=image_b64)

            assert result["success"] is True
            assert result["video_data"] == mock_b64


@pytest.mark.unit
class TestVideoModuleLowVram:
    """Test that low VRAM causes immediate error without POST to the wrapper.

    Regression: previously the code logged "forcing CPU" and continued,
    causing long waste + OOM in the video pipeline. Now it must time out the
    VRAM gate without ever POSTing.
    """

    @pytest.fixture
    def mock_app(self):
        app = MagicMock()
        app.config = _video_config()
        app.logger = MagicMock()
        return app

    def test_low_vram_returns_error_no_post(self, mock_app, monkeypatch):
        import requests

        from modules.video import VideoModule

        # Track all POSTs to the wrapper
        post_calls = {"count": 0}

        def fake_post(*args, **kwargs):
            post_calls["count"] += 1
            raise AssertionError("Should NOT POST to the wrapper when VRAM is insufficient")

        # /running returns empty (no LLM models loaded) — so the gate spins until timeout
        get_resp = MagicMock(status_code=200)
        get_resp.json.return_value = {"running": []}

        monkeypatch.setattr(requests, "post", fake_post)
        monkeypatch.setattr(requests, "get", lambda *a, **kw: get_resp)

        with patch("app.resource_manager.get_resource_manager") as mock_rm:
            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.cuda_detected = True
            mock_rm_instance.hardware.available_vram_mb = 1000  # 1 GB free
            mock_rm_instance.hardware.total_vram_mb = 16311
            mock_rm_instance.estimate_video_vram_needed.return_value = 8000
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            result = module.generate_video({"prompt": "test", "width": 768, "height": 448, "num_frames": 121})

        assert result["success"] is False
        assert "VRAM" in result["error"]
        assert "1000" in result["error"] or "below" in result["error"].lower()
        assert post_calls["count"] == 0

    def test_healthy_vram_proceeds_normally(self, mock_app, monkeypatch):
        import requests

        from modules.video import VideoModule

        # VRAM is sufficient — generation should proceed
        get_resp = MagicMock(status_code=200)
        get_resp.json.return_value = {"running": []}

        mock_b64 = base64.b64encode(b"fake webm").decode("utf-8")
        post_resp = MagicMock(
            status_code=200,
            json=lambda: {
                "success": True,
                "video_data": mock_b64,
                "file_name": "ok.webm",
                "file_size": 100,
                "file_type": "video/webm",
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
            mock_rm_instance.estimate_video_vram_needed.return_value = 8000
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            result = module.generate_video({"prompt": "test", "width": 640, "height": 384, "num_frames": 81})

        assert result["success"] is True


@pytest.mark.unit
class TestVideoModuleMemoryPlanning:
    """CPU video generation: fall back to lower resolution when RAM is tight.

    Wan formula: peak_mb ≈ 11000 + (w//32)·(h//32)·((frames-1)//4+1)·2.0, +1024 safety.
    Requested 768×448×121 ≈ 32856 MB; fallbacks need ≈16056 / 13824 / 13064 MB.
    """

    @pytest.fixture
    def cpu_rm(self):
        from types import SimpleNamespace

        rm = MagicMock()
        rm.hardware = SimpleNamespace(platform="cpu", cuda_detected=False)
        return rm

    def test_plan_large_ram_no_degradation(self, cpu_rm):
        from modules.video import VideoModule

        cpu_rm._detect_available_ram_mb.return_value = 50000
        with patch("app.resource_manager.get_resource_manager", return_value=cpu_rm):
            module = VideoModule()
            prompt_data = {"width": 768, "height": 448, "num_frames": 121}
            override, notice, error = module.plan_cpu_generation(prompt_data, lang="ru")

        assert override == {}
        assert notice is None
        assert error is None

    def test_plan_medium_ram_falls_back(self, cpu_rm):
        from modules.video import VideoModule

        cpu_rm._detect_available_ram_mb.return_value = 24000
        with patch("app.resource_manager.get_resource_manager", return_value=cpu_rm):
            module = VideoModule()
            prompt_data = {"width": 768, "height": 448, "num_frames": 121}
            override, notice, error = module.plan_cpu_generation(prompt_data, lang="ru")

        assert error is None
        assert notice is not None
        assert "lower resolution" in notice
        assert "384×256×81" in notice
        assert override == VideoModule.CPU_FALLBACK_PARAMS[0]

    def test_plan_second_fallback_lower_frames(self, cpu_rm):
        from modules.video import VideoModule

        cpu_rm._detect_available_ram_mb.return_value = 15000
        with patch("app.resource_manager.get_resource_manager", return_value=cpu_rm):
            module = VideoModule()
            prompt_data = {"width": 768, "height": 448, "num_frames": 121}
            override, notice, error = module.plan_cpu_generation(prompt_data, lang="ru")

        assert error is None
        assert notice is not None
        assert "320×192×57" in notice
        assert override == VideoModule.CPU_FALLBACK_PARAMS[1]

    def test_plan_third_fallback_smallest(self, cpu_rm):
        from modules.video import VideoModule

        cpu_rm._detect_available_ram_mb.return_value = 13200
        with patch("app.resource_manager.get_resource_manager", return_value=cpu_rm):
            module = VideoModule()
            prompt_data = {"width": 768, "height": 448, "num_frames": 121}
            override, notice, error = module.plan_cpu_generation(prompt_data, lang="ru")

        assert error is None
        assert notice is not None
        assert "256×160×49" in notice
        assert override == VideoModule.CPU_FALLBACK_PARAMS[2]

    def test_plan_low_ram_impossible(self, cpu_rm):
        from modules.video import VideoModule

        cpu_rm._detect_available_ram_mb.return_value = 12500
        with patch("app.resource_manager.get_resource_manager", return_value=cpu_rm):
            module = VideoModule()
            prompt_data = {"width": 768, "height": 448, "num_frames": 121}
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
            prompt_data = {"width": 768, "height": 448, "num_frames": 121}
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
        app.config = _video_config()
        app.logger = MagicMock()
        return app

    def test_vram_unload_called(self, mock_app):
        from modules.video import VideoModule

        mock_b64 = base64.b64encode(b"fake webm data").decode("utf-8")

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
                    "file_name": "wan_video.webm",
                    "file_size": 1024,
                    "file_type": "video/webm",
                    "generation_time": 10.0,
                },
            )

            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.cuda_detected = True
            mock_rm_instance.hardware.available_vram_mb = 12000
            mock_rm_instance.hardware.total_vram_mb = 16384
            mock_rm_instance.estimate_video_vram_needed.return_value = 8500
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            module.generate_video({"prompt": "test"})

            mock_rm_instance.unload_llamacpp_model.assert_called()
            mock_rm_instance.mark_video_busy.assert_called_once()
            mock_rm_instance.mark_video_idle.assert_called_once()

    def test_vram_busy_idle_bookkeeping_on_error(self, mock_app):
        """mark_video_busy/mark_video_idle must bracket failures too (finally block)."""
        import requests

        from modules.video import VideoModule

        with (
            patch("modules.video.requests.get") as mock_get,
            patch("modules.video.requests.post") as mock_post,
            patch("app.resource_manager.get_resource_manager") as mock_rm,
        ):
            mock_get.return_value = MagicMock(status_code=200)
            mock_post.side_effect = requests.exceptions.ConnectionError("could not connect")

            mock_rm_instance = MagicMock()
            mock_rm_instance.hardware.cuda_detected = True
            mock_rm_instance.hardware.available_vram_mb = 12000
            mock_rm_instance.hardware.total_vram_mb = 16384
            mock_rm_instance.estimate_video_vram_needed.return_value = 8500
            mock_rm.return_value = mock_rm_instance

            module = VideoModule(mock_app)
            result = module.generate_video({"prompt": "test"})

            assert result["success"] is False
            mock_rm_instance.mark_video_busy.assert_called_once()
            mock_rm_instance.mark_video_idle.assert_called_once()


@pytest.mark.unit
class TestVideoModuleHealth:
    """Test health endpoint integration."""

    def test_health_check_in_init(self):
        from modules.video import VideoModule

        app = MagicMock()
        app.config = _video_config()
        app.logger = MagicMock()

        with patch("modules.video.requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            module = VideoModule(app)
            assert module.check_availability() is True

        with patch("modules.video.requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=503)
            module = VideoModule(app)
            assert module.check_availability() is False


@pytest.mark.unit
class TestI2VPresets:
    """I2V: aspect-class detection, preset picking, center-crop helper."""

    def test_detect_aspect_class(self):
        from modules.video import VideoModule

        assert VideoModule._detect_aspect_class(768, 448) == "landscape"
        assert VideoModule._detect_aspect_class(448, 768) == "portrait"
        assert VideoModule._detect_aspect_class(512, 512) == "square"
        assert VideoModule._detect_aspect_class(1300, 1000) == "landscape"
        assert VideoModule._detect_aspect_class(1000, 1300) == "portrait"

    def test_gpu_presets_are_fixed_by_aspect(self):
        from modules.video import VideoModule

        module = VideoModule.__new__(VideoModule)
        p, notice, error = module.pick_i2v_preset(1600, 900, platform="gpu")
        assert (p["width"], p["height"], p["num_frames"]) == (768, 448, 121)
        assert notice is None and error is None
        p, _, _ = module.pick_i2v_preset(900, 1600, platform="gpu")
        assert (p["width"], p["height"]) == (448, 768)
        p, _, _ = module.pick_i2v_preset(1000, 1000, platform="gpu")
        assert (p["width"], p["height"]) == (512, 512)

    def test_cpu_cascade_degrades_by_ram(self):
        from modules.video import VideoModule

        module = VideoModule.__new__(VideoModule)

        # Enough RAM for the best landscape step (384x256x81 ≈ 15 GB) -> no notice.
        with patch("app.resource_manager.get_resource_manager") as mock_rm:
            rm = MagicMock()
            rm._detect_available_ram_mb.return_value = 40000
            mock_rm.return_value = rm
            p, notice, error = module.pick_i2v_preset(1600, 900, platform="cpu")
            assert (p["width"], p["height"]) == (384, 256)
            assert notice is None and error is None

        # Only enough for the smallest portrait step (160x256x49) -> notice.
        with patch("app.resource_manager.get_resource_manager") as mock_rm:
            rm = MagicMock()
            rm._detect_available_ram_mb.return_value = 13500
            mock_rm.return_value = rm
            p, notice, error = module.pick_i2v_preset(900, 1600, platform="cpu")
            assert (p["width"], p["height"]) == (160, 256)
            assert notice is not None and error is None

        # Portrait best (256x384x81) fits 24 GB but not 16 GB.
        with patch("app.resource_manager.get_resource_manager") as mock_rm:
            rm = MagicMock()
            rm._detect_available_ram_mb.return_value = 24000
            mock_rm.return_value = rm
            p, notice, error = module.pick_i2v_preset(900, 1600, platform="cpu")
            assert (p["width"], p["height"]) == (256, 384)
            assert notice is None and error is None

        # Not even the smallest step fits -> error.
        with patch("app.resource_manager.get_resource_manager") as mock_rm:
            rm = MagicMock()
            rm._detect_available_ram_mb.return_value = 11000
            mock_rm.return_value = rm
            p, notice, error = module.pick_i2v_preset(1600, 900, platform="cpu")
            assert error is not None

    def test_center_crop_to_preset(self):
        from io import BytesIO

        from PIL import Image

        from modules.video import center_crop_image_to_preset

        img = Image.new("RGB", (1600, 900), (120, 40, 200))
        buf = BytesIO()
        img.save(buf, format="JPEG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        cropped, orig, new = center_crop_image_to_preset(b64, 768, 448)
        assert orig == (1600, 900)
        assert new == (768, 448)
        out = Image.open(BytesIO(base64.b64decode(cropped)))
        assert out.size == (768, 448)

    def test_center_crop_portrait_small_image(self):
        from io import BytesIO

        from PIL import Image

        from modules.video import center_crop_image_to_preset

        img = Image.new("RGB", (400, 700), (200, 40, 120))
        buf = BytesIO()
        img.save(buf, format="JPEG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        cropped, orig, new = center_crop_image_to_preset(b64, 448, 768)
        assert new == (448, 768)
        out = Image.open(BytesIO(base64.b64decode(cropped)))
        assert out.size == (448, 768)
