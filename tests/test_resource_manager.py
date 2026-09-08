"""Tests for Resource Manager — GPU/CPU/RAM adaptive management."""

import threading
from unittest.mock import MagicMock, mock_open, patch

import pytest

from app.resource_manager import HardwareInfo, ResourceManager, get_resource_manager


class TestHardwareInfo:
    def test_defaults(self):
        hw = HardwareInfo()
        assert hw.total_vram_mb == 0
        assert hw.gpu_name == "unknown"
        assert hw.cuda_detected is False
        assert hw.cpu_count == 0


class TestDetectHardware:
    MEMINFO = "MemTotal:       16384000 kB\nMemFree:         4096000 kB\nMemAvailable:    8192000 kB\n"

    def _rm_teardown(self, rm):
        if hasattr(rm, "hardware"):
            rm.hardware = HardwareInfo()

    @patch("os.cpu_count", return_value=8)
    @patch("builtins.open", new_callable=mock_open, read_data=MEMINFO)
    @patch("subprocess.run")
    def test_detect_hardware_no_gpu(self, mock_run, mock_file, mock_cpu):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="nvidia-smi not found")
        rm = ResourceManager()
        hw = rm.detect_hardware()

        assert hw.cuda_detected is False
        assert hw.gpu_name == "unknown"
        assert hw.total_vram_mb == 0
        assert hw.total_ram_mb == 16000
        assert hw.available_ram_mb == 8000
        assert hw.cpu_count == 8

    @patch("os.cpu_count", return_value=16)
    @patch("builtins.open", new_callable=mock_open, read_data=MEMINFO)
    @patch("subprocess.run")
    def test_detect_hardware_with_gpu(self, mock_run, mock_file, mock_cpu):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="NVIDIA GeForce RTX 4090, 24564, 2048, 22516\n",
        )

        rm = ResourceManager()
        hw = rm.detect_hardware()

        assert hw.cuda_detected is True
        assert "RTX 4090" in hw.gpu_name
        assert hw.total_vram_mb == 24564
        assert hw.available_vram_mb == 22516
        assert hw.cpu_count == 16

    @patch("os.cpu_count", return_value=4)
    @patch("builtins.open", new_callable=mock_open, read_data=MEMINFO)
    @patch("subprocess.run")
    def test_detect_hardware_gpu_empty_output(self, mock_run, mock_file, mock_cpu):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        rm = ResourceManager()
        hw = rm.detect_hardware()
        assert hw.cuda_detected is False

    @patch("os.cpu_count", return_value=4)
    @patch("builtins.open", new_callable=mock_open, read_data=MEMINFO)
    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_detect_hardware_nvidia_smi_not_found(self, mock_run, mock_file, mock_cpu):
        rm = ResourceManager()
        hw = rm.detect_hardware()
        assert hw.cuda_detected is False
        assert hw.gpu_name == "unknown"


class TestComputeConfig:
    @patch("os.cpu_count", return_value=8)
    @patch(
        "builtins.open", new_callable=mock_open, read_data="MemTotal:       32768000 kB\nMemAvailable:   16384000 kB\n"
    )
    @patch("subprocess.run")
    def test_cpu_only_mode(self, mock_run, mock_file, mock_cpu):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="nvidia-smi not found")
        rm = ResourceManager()
        rm.detect_hardware()
        cfg = rm.compute_llamacpp_config("multimodal")

        assert cfg["n_gpu_layers"] == 0
        assert cfg["flash_attn"] is False
        assert cfg["warning"] is not None
        assert "CPU-only" in cfg["warning"]

    @patch("os.cpu_count", return_value=8)
    @patch(
        "builtins.open", new_callable=mock_open, read_data="MemTotal:       32768000 kB\nMemAvailable:   16384000 kB\n"
    )
    @patch("subprocess.run")
    def test_24gb_gpu(self, mock_run, mock_file, mock_cpu):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="NVIDIA GeForce RTX 4090, 24564, 2048, 22516\n",
        )
        rm = ResourceManager()
        rm.detect_hardware()
        cfg = rm.compute_llamacpp_config("multimodal")

        assert cfg["n_gpu_layers"] == -1
        assert cfg["flash_attn"] is True
        assert cfg["cache_capacity"] == 8192

    @patch("os.cpu_count", return_value=8)
    @patch(
        "builtins.open", new_callable=mock_open, read_data="MemTotal:       32768000 kB\nMemAvailable:   16384000 kB\n"
    )
    @patch("subprocess.run")
    def test_16gb_gpu(self, mock_run, mock_file, mock_cpu):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="NVIDIA GeForce RTX 4060 Ti, 16384, 2048, 14336\n",
        )
        rm = ResourceManager()
        rm.detect_hardware()
        cfg = rm.compute_llamacpp_config("multimodal")

        assert cfg["flash_attn"] is True
        assert cfg["offload_kqv"] is True or cfg["n_gpu_layers"] == -1

    @patch("os.cpu_count", return_value=8)
    @patch(
        "builtins.open", new_callable=mock_open, read_data="MemTotal:       32768000 kB\nMemAvailable:   16384000 kB\n"
    )
    @patch("subprocess.run")
    def test_8gb_gpu(self, mock_run, mock_file, mock_cpu):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="NVIDIA GeForce RTX 3050, 8192, 2048, 6144\n",
        )
        rm = ResourceManager()
        rm.detect_hardware()
        cfg = rm.compute_llamacpp_config("multimodal")

        assert cfg["flash_attn"] is True
        assert cfg["offload_kqv"] is True
        assert cfg["ctx_size"] == 4096
        assert cfg["cache_capacity"] == 1024
        assert "Limited VRAM" in (cfg["warning"] or "")

    @patch("os.cpu_count", return_value=8)
    @patch(
        "builtins.open", new_callable=mock_open, read_data="MemTotal:       32768000 kB\nMemAvailable:   16384000 kB\n"
    )
    @patch("subprocess.run")
    def test_less_than_8gb_gpu(self, mock_run, mock_file, mock_cpu):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="NVIDIA GeForce GTX 1050, 4096, 2048, 2048\n",
        )
        rm = ResourceManager()
        rm.detect_hardware()
        cfg = rm.compute_llamacpp_config("multimodal")

        assert cfg["n_gpu_layers"] == 0
        assert cfg["flash_attn"] is True
        assert "Very limited" in (cfg["warning"] or "")

    @patch("os.cpu_count", return_value=8)
    @patch(
        "builtins.open", new_callable=mock_open, read_data="MemTotal:       32768000 kB\nMemAvailable:   16384000 kB\n"
    )
    @patch("subprocess.run")
    def test_reasoning_model_large_vram(self, mock_run, mock_file, mock_cpu):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="NVIDIA GeForce RTX 4090, 24564, 2048, 22516\n",
        )
        rm = ResourceManager()
        rm.detect_hardware()
        cfg = rm.compute_llamacpp_config("reasoning")

        assert cfg["n_gpu_layers"] == -1
        assert cfg["flash_attn"] is True

    @patch("os.cpu_count", return_value=8)
    @patch(
        "builtins.open", new_callable=mock_open, read_data="MemTotal:       32768000 kB\nMemAvailable:   16384000 kB\n"
    )
    @patch("subprocess.run")
    def test_degradation_loop_no_nameerror(self, mock_run, mock_file, mock_cpu):
        """Regression: degradation loop must not raise NameError on ctx_size.

        Pre-fix: ctx_size was undefined in compute_llamacpp_config, causing
        the while loop on lines 319-337 to raise NameError. The exception was
        swallowed by get_vram_needed_mb's try/except, so the bug was silent.
        """
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="NVIDIA GeForce RTX 4060 Ti, 16384, 2048, 14336\n",
        )
        rm = ResourceManager()
        rm.detect_hardware()
        # Should not raise NameError on any model type
        for mt in ["multimodal", "reasoning", "embedding"]:
            cfg = rm.compute_llamacpp_config(mt)
            assert "n_gpu_layers" in cfg
            assert cfg["n_gpu_layers"] is not None
            assert cfg["ctx_size"] > 0

    @patch("os.cpu_count", return_value=8)
    @patch(
        "builtins.open", new_callable=mock_open, read_data="MemTotal:       32768000 kB\nMemAvailable:   16384000 kB\n"
    )
    @patch("subprocess.run")
    def test_ctx_size_uses_db_config(self, mock_run, mock_file, mock_cpu):
        """ctx_size in result should reflect DB context_length, not hardcoded 8192.

        Pre-fix: result always returned ctx_size=8192 regardless of model config.
        """
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="NVIDIA GeForce RTX 4090, 24564, 2048, 22516\n",
        )
        with patch("app.model_config.get_model_config") as mock_cfg:
            mock_cfg.return_value = {
                "model_name": "Qwen3-4B-Instruct-2507-MXFP4_MOE.gguf",
                "context_length": 16384,
            }
            rm = ResourceManager()
            rm.detect_hardware()
            cfg = rm.compute_llamacpp_config("multimodal")
            assert cfg["ctx_size"] == 16384

        with patch("app.model_config.get_model_config") as mock_cfg:
            mock_cfg.return_value = {
                "model_name": "Qwen3VL-8B-Instruct-Q4_K_M",
                "context_length": 8192,
            }
            rm = ResourceManager()
            rm.detect_hardware()
            cfg = rm.compute_llamacpp_config("multimodal")
            assert cfg["ctx_size"] == 8192

    @patch("os.cpu_count", return_value=8)
    @patch(
        "builtins.open", new_callable=mock_open, read_data="MemTotal:       32768000 kB\nMemAvailable:   16384000 kB\n"
    )
    @patch("subprocess.run")
    def test_8gb_override_keeps_ctx_4096(self, mock_run, mock_file, mock_cpu):
        """8GB tier must still override ctx_size to 4096 even when config has 16384."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="NVIDIA GeForce RTX 3050, 8192, 2048, 6144\n",
        )
        with patch("app.model_config.get_model_config") as mock_cfg:
            mock_cfg.return_value = {
                "model_name": "Qwen3-4B-Instruct-2507-MXFP4_MOE.gguf",
                "context_length": 16384,
            }
            rm = ResourceManager()
            rm.detect_hardware()
            cfg = rm.compute_llamacpp_config("multimodal")
            # 8GB tier caps ctx_size to 4096 regardless of DB config
            assert cfg["ctx_size"] == 4096


class TestEnsureVramForCpu:
    """ensure_vram_for must short-circuit on CPU platform (no GPU memory to wait for)."""

    def _rm_cpu(self):
        rm = ResourceManager()
        rm.hardware.platform = "cpu"
        rm.hardware.cuda_detected = False
        rm.hardware.available_vram_mb = 0
        return rm

    @patch("app.resource_manager.requests.get")
    @patch.object(ResourceManager, "unload_llamacpp_model")
    @patch.object(ResourceManager, "unload_video_pipeline")
    def test_cpu_platform_returns_true(self, mock_unload_video, mock_unload_llm, mock_get):
        rm = self._rm_cpu()
        assert rm.ensure_vram_for("multimodal") is True
        mock_get.assert_not_called()
        mock_unload_llm.assert_not_called()
        mock_unload_video.assert_not_called()

    @patch("app.resource_manager.requests.get")
    @patch.object(ResourceManager, "unload_llamacpp_model")
    @patch.object(ResourceManager, "unload_video_pipeline")
    def test_cpu_platform_skips_vram_poll(self, mock_unload_video, mock_unload_llm, mock_get):
        rm = self._rm_cpu()
        with patch.object(rm, "_poll_vram") as mock_poll:
            assert rm.ensure_vram_for("multimodal") is True
        mock_poll.assert_not_called()


class TestGpuGating:
    def test_initial_not_busy(self):
        rm = ResourceManager()
        assert rm._sd_busy is False

    def test_mark_sd_busy(self):
        rm = ResourceManager()
        rm.mark_sd_busy()
        assert rm._sd_busy is True
        assert rm._sd_busy_since > 0

    def test_mark_sd_idle(self):
        rm = ResourceManager()
        rm.mark_sd_busy()
        rm.mark_sd_idle()
        assert rm._sd_busy is False

    def test_thread_safety(self):
        rm = ResourceManager()

        def toggle():
            for _ in range(100):
                rm.mark_sd_busy()
                rm.mark_sd_idle()

        threads = [threading.Thread(target=toggle) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert rm._sd_busy is False


class TestMmprojAccounting:
    """mmproj (vision encoder) is a fixed VRAM cost independent of n_gpu_layers.

    llama-server loads the mmproj fully into VRAM (no partial offload is
    configured), so the multimodal module budget is reduced by the mmproj size
    and every VRAM formula must account for it.
    """

    _BIG_MODEL = {"Qwen3VL-13B-Instruct-Q8_0": {"file_size_mb": 12000, "block_count": 40}}
    _VL_MODEL = {"Qwen3VL-8B-Instruct-Q4_K_M": {"file_size_mb": 4795, "block_count": 36}}

    def _rm_teardown(self, rm):
        if hasattr(rm, "hardware"):
            rm.hardware = HardwareInfo()

    @patch("os.cpu_count", return_value=8)
    @patch(
        "builtins.open", new_callable=mock_open, read_data="MemTotal:       32768000 kB\nMemAvailable:   16384000 kB\n"
    )
    @patch("subprocess.run")
    @patch("app.utils.get_gguf_models_cached")
    @patch("app.model_config.get_model_config")
    @patch("app.utils.get_mmproj_size_mb")
    def test_mmproj_reduces_ngl_on_16gb(self, mock_mmproj, mock_cfg, mock_cache, mock_run, mock_file, mock_cpu):
        """On 16GB a big multimodal model gets fewer GPU layers when mmproj exists."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="NVIDIA GeForce RTX 4060 Ti, 16384, 2048, 14336\n",
        )
        mock_cache.return_value = self._BIG_MODEL
        mock_cfg.return_value = {
            "model_name": "Qwen3VL-13B-Instruct-Q8_0.gguf",
            "context_length": 16384,
        }
        rm = ResourceManager()
        rm.detect_hardware()

        mock_mmproj.return_value = 1105
        cfg_with_mmproj = rm.compute_llamacpp_config("multimodal")

        mock_mmproj.return_value = 0
        cfg_without_mmproj = rm.compute_llamacpp_config("multimodal")

        assert cfg_with_mmproj["n_gpu_layers"] < cfg_without_mmproj["n_gpu_layers"]
        self._rm_teardown(rm)

    @patch("os.cpu_count", return_value=8)
    @patch(
        "builtins.open", new_callable=mock_open, read_data="MemTotal:       32768000 kB\nMemAvailable:   16384000 kB\n"
    )
    @patch("subprocess.run")
    @patch("app.utils.get_gguf_models_cached")
    @patch("app.model_config.get_model_config")
    @patch("app.utils.get_mmproj_size_mb")
    def test_get_vram_needed_mb_includes_mmproj(self, mock_mmproj, mock_cfg, mock_cache, mock_run, mock_file, mock_cpu):
        """get_vram_needed_mb total grows by the mmproj size for multimodal."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="NVIDIA GeForce RTX 4090, 24564, 2048, 22516\n",
        )
        mock_cache.return_value = self._VL_MODEL
        mock_cfg.return_value = {
            "model_name": "Qwen3VL-8B-Instruct-Q4_K_M.gguf",
            "context_length": 16384,
        }
        rm = ResourceManager()
        rm.detect_hardware()

        mock_mmproj.return_value = 1105
        with_mmproj = rm.get_vram_needed_mb("multimodal", ctx_size=16384)

        mock_mmproj.return_value = 0
        without_mmproj = rm.get_vram_needed_mb("multimodal", ctx_size=16384)

        assert with_mmproj - without_mmproj == 1105
        self._rm_teardown(rm)


class TestUnloadModel:
    @patch("requests.post")
    @patch("app.resource_manager.os.getenv")
    def test_unload_llama_swap(self, mock_getenv, mock_post):
        mock_getenv.return_value = "llama-swap"
        mock_post.return_value = MagicMock(status_code=200)
        rm = ResourceManager()
        result = rm.unload_llamacpp_model()
        assert result is True

    @patch("requests.post")
    @patch("requests.get")
    @patch("app.resource_manager.os.getenv")
    def test_unload_direct_no_model_loaded(self, mock_getenv, mock_get, mock_post):
        mock_getenv.return_value = "llamacpp"
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {"data": []})
        rm = ResourceManager()
        result = rm.unload_llamacpp_model()
        assert result is True

    @patch("requests.post")
    @patch("requests.get")
    @patch("app.resource_manager.os.getenv")
    def test_unload_direct_with_model(self, mock_getenv, mock_get, mock_post):
        mock_getenv.return_value = "llamacpp"
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"data": [{"id": "test-model", "status": {"value": "loaded"}}]},
        )
        mock_post.return_value = MagicMock(status_code=200)
        rm = ResourceManager()
        result = rm.unload_llamacpp_model()
        assert result is True
        assert mock_post.called

    @patch("requests.post")
    @patch("requests.get")
    @patch("app.resource_manager.os.getenv")
    def test_unload_direct_failure(self, mock_getenv, mock_get, mock_post):
        mock_getenv.return_value = "llamacpp"
        mock_get.side_effect = Exception("Connection refused")
        rm = ResourceManager()
        result = rm.unload_llamacpp_model()
        assert result is False


class TestRefreshRuntime:
    @patch(
        "app.resource_manager.ResourceManager._detect_available_ram_mb",
        return_value=6000,
    )
    @patch(
        "app.resource_manager.ResourceManager._detect_total_ram_mb",
        return_value=16000,
    )
    @patch(
        "app.resource_manager.ResourceManager._detect_cpu_name",
        return_value="AMD Ryzen 5 7500F",
    )
    @patch("os.cpu_count", return_value=12)
    @patch("app.platform_detect.get_platform_info")
    def test_refresh_runtime_re_reads_resources(self, mock_gpi, mock_cpu, mock_cpu_name, mock_total, mock_avail):
        """refresh_runtime() must refresh RAM/CPU (and VRAM) on demand."""
        rm = ResourceManager()
        rm.hardware = HardwareInfo()
        rm.hardware.available_ram_mb = 8000  # stale startup value
        rm.hardware.total_ram_mb = 16000
        rm.hardware.cpu_count = 8
        rm.hardware.cpu_name = ""
        mock_gpi.return_value = MagicMock(total_vram_mb=16311, available_vram_mb=12000, used_vram_mb=4000)
        rm.hardware.platform = "nvidia"

        rm.refresh_runtime()

        assert rm.hardware.available_ram_mb == 6000
        assert rm.hardware.total_ram_mb == 16000
        assert rm.hardware.cpu_count == 12
        assert rm.hardware.cpu_name == "AMD Ryzen 5 7500F"
        assert rm.hardware.available_vram_mb == 12000


class TestGetStatus:
    @patch("os.cpu_count", return_value=8)
    @patch(
        "builtins.open", new_callable=mock_open, read_data="MemTotal:       16384000 kB\nMemAvailable:   8192000 kB\n"
    )
    @patch("subprocess.run")
    def test_get_status(self, mock_run, mock_file, mock_cpu):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="NVIDIA GeForce RTX 4090, 24564, 2048, 22516\n",
        )
        rm = ResourceManager()
        rm.detect_hardware()
        status = rm.get_status()

        assert status["gpu_name"] == "NVIDIA GeForce RTX 4090"
        assert status["cuda_detected"] is True
        assert status["total_vram_mb"] == 24564
        assert status["total_ram_mb"] == 16000
        assert status["cpu_count"] == 8
        assert status["sd_busy"] is False


class TestSingleton:
    def test_get_resource_manager_returns_same_instance(self):
        rm1 = get_resource_manager()
        rm2 = get_resource_manager()
        assert rm1 is rm2


class TestEstimateVideoVram:
    """Tests for dynamic Wan2.2 VRAM estimation."""

    @pytest.mark.unit
    def test_estimate_video_vram_needed_prefers_wan_measurement(self, monkeypatch):
        from app.resource_manager import ResourceManager

        rm = ResourceManager.__new__(ResourceManager)
        rm.hardware = MagicMock()
        rm.hardware.platform = "gpu"
        calls = {}

        class FakeDb:
            def __call__(self, module, model_name=None):
                calls["module"] = module
                if module == "ltx-video":
                    return {"measured_vram_mb": 3822}
                return None

        monkeypatch.setattr("app.resource_manager.get_vram_estimate", FakeDb())
        est = rm.estimate_video_vram_needed()
        assert calls["module"] == "ltx-video"
        assert est >= 7000  # Wan floor, NOT the 3822 LTX measurement

    def test_uses_wan_measured_when_available(self):
        """A stored Wan-named measurement takes precedence over the floor."""
        with patch("app.database.get_vram_estimate") as mock_get:
            mock_get.return_value = {
                "measured_vram_mb": 12500,
                "model_name": "wan2.2-ti2v-5b-turbo",
                "module": "ltx-video",
            }
            rm = ResourceManager()
            result = rm.estimate_video_vram_needed()
            assert result == 12500

    @pytest.mark.unit
    def test_wan_measured_below_floor_is_clamped_to_8000(self):
        """A near-zero Wan measurement (sd-cli exits before VRAM is measured)
        must not collapse the estimate below the 8000 MB Wan floor."""
        with patch("app.database.get_vram_estimate") as mock_get:
            mock_get.return_value = {
                "measured_vram_mb": 1000,
                "model_name": "wan2.2-ti2v-5b-turbo",
                "module": "ltx-video",
            }
            rm = ResourceManager()
            result = rm.estimate_video_vram_needed()
            assert result == 8000

    def test_ignores_ltx_measured_row(self):
        """A legacy LTX-named measurement must NOT be used (Wan floor applies)."""
        with patch("app.database.get_vram_estimate") as mock_get:
            mock_get.return_value = {
                "measured_vram_mb": 6917,
                "model_name": "ltxv-2b-0.9.8-distilled",
                "module": "ltx-video",
            }
            rm = ResourceManager()
            result = rm.estimate_video_vram_needed()
            assert result == 8000

    def test_ignores_measured_row_without_model_name(self):
        """A measurement without a model_name is not trusted as Wan."""
        with patch("app.database.get_vram_estimate") as mock_get:
            mock_get.return_value = {"measured_vram_mb": 6800, "module": "ltx-video"}
            rm = ResourceManager()
            result = rm.estimate_video_vram_needed()
            assert result == 8000

    def test_falls_back_to_wan_floor_when_no_measurement(self, tmp_path, monkeypatch):
        """Without a Wan measurement, return the 8000 MB Wan floor (env/files logic removed)."""
        monkeypatch.delenv("LTX_VIDEO_VRAM_MB", raising=False)
        with patch("app.database.get_vram_estimate", return_value=None):
            rm = ResourceManager()
            result = rm.estimate_video_vram_needed()
            assert result == 8000

    def test_wan_measured_takes_precedence_even_with_ltx_env(self, monkeypatch):
        """Legacy LTX_VIDEO_VRAM_MB env var is inactive; Wan measurement wins."""
        monkeypatch.setenv("LTX_VIDEO_VRAM_MB", "9100")
        with patch(
            "app.database.get_vram_estimate",
            return_value={"measured_vram_mb": 13000, "model_name": "wan-2.2-other", "module": "ltx-video"},
        ):
            rm = ResourceManager()
            result = rm.estimate_video_vram_needed()
            assert result == 13000

    def test_db_error_returns_floor(self):
        """A DB failure must not crash; fall back to the Wan floor."""
        with patch("app.database.get_vram_estimate", side_effect=Exception("DB down")):
            rm = ResourceManager()
            result = rm.estimate_video_vram_needed()
            assert result == 8000


class TestUnloadVideoPipeline:
    """Tests for verify-on-unload behavior."""

    def test_succeeds_when_vram_freed(self):
        """If VRAM grows by >=3GB after POST, return True."""
        rm = ResourceManager()
        rm.hardware.total_vram_mb = 16311
        rm.hardware.available_vram_mb = 10000  # before unload

        mock_post = MagicMock(return_value=MagicMock(status_code=200))
        # Simulate VRAM growing after unload — first poll frees to 15000MB
        poll_count = {"n": 0}

        def fake_poll():
            poll_count["n"] += 1
            if poll_count["n"] >= 2:
                rm.hardware.available_vram_mb = 15000  # +5000MB freed

        rm._poll_vram = MagicMock(side_effect=fake_poll)

        with (
            patch.dict("os.environ", {"LTX_VIDEO_WRAPPER_URL": "http://test:7872"}),
            patch("app.resource_manager.requests.post", new=mock_post),
        ):
            result = rm.unload_video_pipeline()
        assert result is True
        assert mock_post.called

    def test_retries_when_vram_doesnt_free(self):
        """If POST succeeds but VRAM doesn't free, retry up to 3 times."""
        rm = ResourceManager()
        rm.hardware.total_vram_mb = 16311
        rm.hardware.available_vram_mb = 10000

        mock_post = MagicMock(return_value=MagicMock(status_code=200))
        # VRAM never grows — simulate stuck pipeline
        rm._poll_vram = MagicMock()

        with (
            patch("time.sleep"),
            patch.dict("os.environ", {"LTX_VIDEO_WRAPPER_URL": "http://test:7872"}),
            patch("app.resource_manager.requests.post", new=mock_post),
        ):
            result = rm.unload_video_pipeline()
        # Should have tried 3 times
        assert mock_post.call_count == 3
        assert result is False

    def test_returns_false_on_http_error(self):
        """If all retries fail with HTTP error, return False."""
        rm = ResourceManager()
        rm.hardware.total_vram_mb = 16311
        rm.hardware.available_vram_mb = 14000

        mock_post = MagicMock(return_value=MagicMock(status_code=500))
        rm._poll_vram = MagicMock()

        with (
            patch("time.sleep"),
            patch.dict("os.environ", {"LTX_VIDEO_WRAPPER_URL": "http://test:7872"}),
            patch("app.resource_manager.requests.post", new=mock_post),
        ):
            result = rm.unload_video_pipeline()
        assert result is False
        assert mock_post.call_count == 3

    def test_returns_false_on_exception(self):
        """Network exception is treated as failure."""
        rm = ResourceManager()
        rm.hardware.total_vram_mb = 16311
        rm.hardware.available_vram_mb = 14000

        mock_post = MagicMock(side_effect=ConnectionError("boom"))
        rm._poll_vram = MagicMock()
        with (
            patch("time.sleep"),
            patch.dict("os.environ", {"LTX_VIDEO_WRAPPER_URL": "http://test:7872"}),
            patch.dict("sys.modules", {"requests": MagicMock(post=mock_post)}),
        ):
            result = rm.unload_video_pipeline()
        assert result is False


class TestMeasureVideoVramPeak:
    """Tests for video peak VRAM recording."""

    def test_records_peak_when_cuda_present(self):
        """Store measured_vram_mb = total - free in DB."""
        rm = ResourceManager()
        rm.hardware.total_vram_mb = 16311
        rm.hardware.available_vram_mb = 9000
        rm._poll_vram = MagicMock()

        with patch("app.database.upsert_vram_estimate") as mock_upsert:
            rm.measure_video_vram_peak("ltxv-2b-0.9.8-distilled")
            mock_upsert.assert_called_once()
            call_kwargs = mock_upsert.call_args
            assert call_kwargs.kwargs["module"] == "ltx-video"
            assert call_kwargs.kwargs["model_name"] == "ltxv-2b-0.9.8-distilled"
            assert call_kwargs.kwargs["measured_mb"] == 16311 - 9000

    def test_silent_on_exception(self):
        """DB error doesn't propagate."""
        rm = ResourceManager()
        rm.hardware.total_vram_mb = 16311
        rm.hardware.available_vram_mb = 9000
        rm._poll_vram = MagicMock()

        with patch("app.database.upsert_vram_estimate", side_effect=Exception("DB down")):
            # Should not raise
            rm.measure_video_vram_peak("test")
