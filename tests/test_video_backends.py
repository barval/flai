# tests/test_video_backends.py
"""Tests for the two video backends: LTX-Video (fast) and Wan 2.2 5B (quality)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from modules.video import (
    BACKEND_LTX,
    BACKEND_WAN,
    WAN_720P_PARAMS,
    WAN_DEFAULT_PARAMS,
    WAN_FALLBACK_PARAMS,
    WAN_MAX_FRAMES,
    VideoModule,
)

ROOT = Path(__file__).resolve().parents[1]


def _make_app(**overrides):
    config = {
        "VIDEO_BACKEND": "wan",
        "WAN_VIDEO_URL": "http://test-sd:7861",
        "WAN_VIDEO_TIMEOUT": 1800,
        "WAN_VIDEO_MODEL": "wan2.2-ti2v-5b-turbo-Q4_K_M.gguf",
        "WAN_VIDEO_STEPS": 4,
        "WAN_VIDEO_CFG_SCALE": 1.0,
        "WAN_VIDEO_FLOW_SHIFT": 3.0,
        "WAN_VIDEO_ALLOW_CPU": False,
        "LTX_VIDEO_WRAPPER_URL": "http://test-ltx:7872",
        "LTX_VIDEO_MODEL": "ltxv-2b-0.9.8-distilled",
        "LTX_VIDEO_TIMEOUT": 600,
        "SERVICE_RETRY_ATTEMPTS": 1,
        "SERVICE_RETRY_DELAY": 0,
    }
    config.update(overrides)
    app = MagicMock()
    app.config = config
    return app


def _module(**overrides):
    with patch("modules.video.requests.get", return_value=MagicMock(status_code=200)):
        return VideoModule(_make_app(**overrides))


@pytest.mark.unit
class TestWanVramEstimate:
    def test_default_preset_matches_the_measurement(self):
        """832×480×121 peaked at 15 340 MB card-wide (~15.0 GB) on the live card."""
        from app.vram_estimate import wan_video_vram_mb

        estimate = wan_video_vram_mb(832, 480, 121)
        assert 14_500 <= estimate <= 15_400

    def test_encoder_offload_saves_six_gigabytes(self):
        from app.vram_estimate import WAN_VIDEO_TEXT_ENCODER_MB, wan_video_vram_mb

        full = wan_video_vram_mb(832, 480, 121)
        offloaded = wan_video_vram_mb(832, 480, 121, encoder_out_of_vram=True)
        assert full - offloaded == WAN_VIDEO_TEXT_ENCODER_MB

    def test_720p_does_not_fit_16gb(self):
        from app.vram_estimate import wan_video_vram_mb

        assert wan_video_vram_mb(1280, 704, 121) > 16_311

    def test_smallest_preset_fits_8gb_with_offload(self):
        from app.vram_estimate import wan_video_vram_mb

        assert wan_video_vram_mb(512, 288, 49, encoder_out_of_vram=True) < 8_000

    def test_estimate_grows_with_resolution_and_frames(self):
        from app.vram_estimate import wan_video_vram_mb

        small = wan_video_vram_mb(512, 288, 49)
        taller = wan_video_vram_mb(512, 288, 121)
        wider = wan_video_vram_mb(832, 480, 121)
        assert small < taller < wider

    def test_module_estimate_uses_offload_below_12gb(self):
        module = _module()
        rm = MagicMock()
        rm.hardware.total_vram_mb = 8_000
        with patch("app.resource_manager.get_resource_manager", return_value=rm):
            on_8gb = module.estimate_vram_mb(dict(WAN_DEFAULT_PARAMS))
        rm.hardware.total_vram_mb = 16_311
        with patch("app.resource_manager.get_resource_manager", return_value=rm):
            on_16gb = module.estimate_vram_mb(dict(WAN_DEFAULT_PARAMS))
        assert on_16gb - on_8gb == 6_000


@pytest.mark.unit
class TestBackendLimits:
    def test_wan_caps_ten_seconds_to_five(self):
        module = _module()
        params, notice = module.apply_backend_limits(
            {"width": 832, "height": 480, "num_frames": 240, "frame_rate": 24}
        )
        assert params["num_frames"] == WAN_MAX_FRAMES
        assert notice is not None
        assert "5" in notice

    def test_wan_leaves_a_five_second_request_alone(self):
        module = _module()
        params, notice = module.apply_backend_limits(
            {"width": 832, "height": 480, "num_frames": 121, "frame_rate": 24}
        )
        assert params["num_frames"] == 121
        assert notice is None

    def test_ltx_keeps_ten_seconds(self):
        module = _module(VIDEO_BACKEND="ltx")
        params, notice = module.apply_backend_limits(
            {"width": 768, "height": 512, "num_frames": 240, "frame_rate": 24}
        )
        assert params["num_frames"] == 240
        assert notice is None


@pytest.mark.unit
class TestPayload:
    def test_wan_payload_carries_sampling(self):
        module = _module()
        payload = module._build_payload(
            {"prompt": "a fox", "width": 832, "height": 480, "num_frames": 121, "frame_rate": 24},
            None,
            "user",
            "session",
            "task",
        )
        assert payload["steps"] == 4
        assert payload["cfg_scale"] == 1.0
        assert payload["flow_shift"] == 3.0
        assert payload["width"] == 832

    def test_ltx_payload_has_no_sampling(self):
        module = _module(VIDEO_BACKEND="ltx")
        payload = module._build_payload({"prompt": "a fox"}, None, None, None, None)
        assert "steps" not in payload
        assert payload["width"] == 768
        assert payload["num_frames"] == 240

    def test_defaults_follow_the_backend(self):
        assert _module().default_params["num_frames"] == 121
        assert _module(VIDEO_BACKEND="ltx").default_params["num_frames"] == 240


@pytest.mark.unit
class TestCpuPlanning:
    def _rm(self, platform="cpu", ram_mb=32_000):
        rm = MagicMock()
        rm.hardware.platform = platform
        rm._detect_available_ram_mb.return_value = ram_mb
        return rm

    def test_wan_is_refused_on_cpu_by_default(self):
        module = _module(WAN_VIDEO_ALLOW_CPU=False)
        with patch("app.resource_manager.get_resource_manager", return_value=self._rm()):
            override, notice, error = module.plan_cpu_generation(dict(WAN_DEFAULT_PARAMS))
        assert override is None and notice is None
        assert error is not None
        assert "GPU" in error

    def test_wan_attempts_when_explicitly_allowed(self):
        module = _module(WAN_VIDEO_ALLOW_CPU=True)
        with patch("app.resource_manager.get_resource_manager", return_value=self._rm(ram_mb=48_000)):
            override, notice, error = module.plan_cpu_generation(dict(WAN_DEFAULT_PARAMS))
        # Opt-in only: the planner degrades the clip, never refuses it for the
        # backend. 480p at 5 s cannot fit 48 GB of RAM, so it steps down.
        assert error is None
        assert override is not None
        assert override["num_frames"] <= WAN_MAX_FRAMES
        assert notice is not None

    def test_ltx_on_cpu_is_planned_not_refused(self):
        module = _module(VIDEO_BACKEND="ltx", LTX_VIDEO_TIMEOUT=10_800)
        with patch("app.resource_manager.get_resource_manager", return_value=self._rm(ram_mb=64_000)):
            override, notice, error = module.plan_cpu_generation(
                {"width": 768, "height": 512, "num_frames": 240, "frame_rate": 24}
            )
        assert error is None
        assert override is not None or notice is not None

    def test_wan_cpu_estimate_is_heavier_than_ltx_for_the_same_clip(self):
        """Wan's 16-pixel VAE stride costs more RAM per clip than LTX's 32."""
        wan = VideoModule.estimate_peak_ram_mb(640, 384, 121, backend=BACKEND_WAN)
        ltx = VideoModule.estimate_peak_ram_mb(640, 384, 121, backend=BACKEND_LTX)
        assert wan > ltx


@pytest.mark.unit
class TestUnload:
    def test_wan_skips_the_ltx_unload(self, monkeypatch):
        from app.resource_manager import ResourceManager

        monkeypatch.setenv("VIDEO_BACKEND", "wan")
        with patch("app.resource_manager.requests.post") as post:
            result = ResourceManager.unload_video_pipeline(MagicMock())
        assert result is True
        post.assert_not_called()


@pytest.mark.unit
class TestFallbackChain:
    def test_chain_keeps_the_five_second_length_first(self):
        lengths = [preset["num_frames"] for preset in WAN_FALLBACK_PARAMS]
        assert lengths[0] == 121
        assert lengths.count(121) >= 2
        assert lengths == sorted(lengths, reverse=True)

    def test_720p_steps_down_on_a_16gb_card(self):
        """The clamp happens before the VRAM gate, so the gate never waits for 21.5 GB."""
        module = _module()
        rm = MagicMock()
        rm.hardware.total_vram_mb = 16_311
        rm.hardware.available_vram_mb = 337  # LLM still resident
        params = module._fit_wan_to_vram(rm, dict(WAN_720P_PARAMS))
        assert params["width"] <= 832
        assert params["num_frames"] == 121

    def test_default_preset_fits_a_16gb_card(self):
        """Measured: 15 003 MB of pipeline against 15 511 MB free, so it must stay 480p."""
        module = _module()
        rm = MagicMock()
        rm.hardware.total_vram_mb = 16_311
        rm.hardware.available_vram_mb = 337
        params = module._fit_wan_to_vram(rm, dict(WAN_DEFAULT_PARAMS))
        assert params["width"] == 832
        assert params["num_frames"] == 121

    def test_8gb_card_steps_down_to_360p(self):
        module = _module()
        rm = MagicMock()
        rm.hardware.total_vram_mb = 8_000
        rm.hardware.available_vram_mb = 500
        params = module._fit_wan_to_vram(rm, dict(WAN_DEFAULT_PARAMS))
        assert params["width"] == 640
        assert params["num_frames"] == 121

    def test_chain_sizes_stay_valid_for_wan(self):
        for preset in WAN_FALLBACK_PARAMS:
            assert preset["width"] % 32 == 0
            assert preset["height"] % 32 == 0
            assert (preset["num_frames"] - 1) % 4 == 0


@pytest.mark.unit
class TestDeployWiring:
    def test_gpu_compose_serves_wan_from_the_sd_container(self):
        compose = (ROOT / "docker-compose.gpu.yml").read_text(encoding="utf-8")
        assert "with-video-wan" in compose
        assert "sd_cpp:" in compose

    def test_deploy_scripts_offer_both_backends(self):
        for name in ("deploy.sh", "deploy-ru.sh"):
            script = (ROOT / name).read_text(encoding="utf-8")
            assert "--with-video-wan" in script
            assert "VIDEO_BACKEND" in script
            assert "download_wan_video_models" in script

    def test_env_example_documents_the_backend(self):
        env = (ROOT / ".env.example").read_text(encoding="utf-8")
        assert "VIDEO_BACKEND=ltx" in env
        assert "WAN_VIDEO_URL" in env

    def test_prompt_templates_are_backend_aware(self):
        for lang in ("ru", "en"):
            for name in ("create_video.template", "create_video_from_image.template"):
                text = (ROOT / "prompts" / lang / name).read_text(encoding="utf-8")
                assert "{video_model}" in text
                assert "{video_frames_rule}" in text


@pytest.mark.unit
class TestTranslations:
    def test_new_notices_exist_in_both_catalogs(self):
        keys = [
            "Wan generates clips up to {max_seconds} seconds",
            "Wan video generation requires a GPU",
            "Video service returned no video data",
        ]
        for lang in ("ru", "en"):
            catalog = (ROOT / "translations" / lang / "LC_MESSAGES" / "messages.po").read_text(encoding="utf-8")
            for key in keys:
                assert key in catalog, f"{lang}: missing {key}"


@pytest.mark.unit
class TestVideoSourceFields:
    def test_compose_passes_the_planner_ram_cap(self):
        gpu = (ROOT / "docker-compose.gpu.yml").read_text(encoding="utf-8")
        assert "WAN_VIDEO_RAM_LIMIT_MB" in gpu

    def test_wrapper_builds_the_diffusion_model_command(self):
        wrapper = (ROOT / "services" / "sd_cpp" / "sd_wrapper.py").read_text(encoding="utf-8")
        assert '"--diffusion-model"' in wrapper
        assert '"/v1/video/generations"' in wrapper
        assert "ffmpeg" in wrapper


@pytest.mark.unit
class TestWanWrapper:
    """Pure helpers of the sd.cpp video endpoint (no GPU, no subprocess)."""

    @staticmethod
    def _load():
        import importlib.util
        import os
        import tempfile

        os.environ.setdefault("SD_LOG_DIR", tempfile.mkdtemp(prefix="sd-test-logs-"))
        spec = importlib.util.spec_from_file_location(
            "sd_wrapper_video_test", ROOT / "services" / "sd_cpp" / "sd_wrapper.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_frames_snap_to_4n_plus_1(self):
        wrapper = self._load()
        assert wrapper._snap_frames(240) == 121
        assert wrapper._snap_frames(122) == 121
        assert wrapper._snap_frames(49) == 49
        assert wrapper._snap_frames(1) == 5

    def test_dimensions_snap_to_multiples_of_32(self):
        wrapper = self._load()
        assert wrapper._snap_dim(830) == 800
        assert wrapper._snap_dim(832) == 832
        assert wrapper._snap_dim(100) == 256

    def test_command_uses_diffusion_model_not_m(self):
        wrapper = self._load()
        cmd = wrapper._build_video_cmd(
            "a fox", "blurry", 832, 480, 121, 24, 4, 1.0, 3.0, 42, use_gpu=False
        )
        assert "-M" in cmd and cmd[cmd.index("-M") + 1] == "vid_gen"
        assert "--diffusion-model" in cmd
        assert "-m" not in cmd
        assert cmd[cmd.index("--video-frames") + 1] == "121"
        assert cmd[cmd.index("--steps") + 1] == "4"
        assert "--diffusion-fa" not in cmd  # CPU build

    def test_command_passes_the_init_image(self):
        wrapper = self._load()
        cmd = wrapper._build_video_cmd(
            "a fox", "blurry", 512, 288, 49, 24, 4, 1.0, 3.0, 42, init_image="/tmp/x.jpg", use_gpu=False
        )
        assert "-i" in cmd
        assert cmd[cmd.index("-i") + 1] == "/tmp/x.jpg"

    def test_wrapper_fallbacks_are_valid_wan_shapes(self):
        wrapper = self._load()
        for width, height, frames in wrapper.WAN_VIDEO_FALLBACKS:
            assert width % 32 == 0 and height % 32 == 0
            assert (frames - 1) % 4 == 0
            assert frames <= wrapper.WAN_MAX_FRAMES


@pytest.mark.unit
class TestRuntimeBackendSwitch:
    """VIDEO_BACKEND is a saved admin setting: switching it must not need a restart."""

    def test_config_change_moves_requests_to_the_other_wrapper(self):
        app = _make_app()
        with patch("modules.video.requests.get", return_value=MagicMock(status_code=200)):
            module = VideoModule(app)
        assert module.backend == "wan"
        assert module.wrapper_url == "http://test-sd:7861"

        app.config["VIDEO_BACKEND"] = "ltx"
        module._refresh_backend()
        assert module.backend == "ltx"
        assert module.wrapper_url == "http://test-ltx:7872"
        assert module.steps == module.CPU_STEPS_DEFAULT

    def test_current_backend_reads_the_live_config(self):
        """The VRAM gate runs before generate_video: a stale attribute asked 15 GB for a 3 GB clip."""
        app = _make_app()
        with patch("modules.video.requests.get", return_value=MagicMock(status_code=200)):
            module = VideoModule(app)
        app.config["VIDEO_BACKEND"] = "ltx"
        assert module.current_backend() == "ltx"
        app.config["VIDEO_BACKEND"] = "wan"
        assert module.current_backend() == "wan"

    def test_margin_follows_the_backend(self):
        assert _module().vram_margin_mb == 300
        assert _module(VIDEO_BACKEND="ltx").vram_margin_mb == 1000
