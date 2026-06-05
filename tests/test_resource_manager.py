"""Tests for Resource Manager — GPU/CPU/RAM adaptive management."""

import threading
from unittest.mock import MagicMock, mock_open, patch

from app.resource_manager import HardwareInfo, ResourceManager, get_resource_manager


class _FakeStat:
    """os.stat_result-like with overridden st_size for file-size mocks."""

    def __init__(self, real_result, fake_size):
        self._real = real_result
        self.st_size = fake_size

    def __getattr__(self, name):
        return getattr(self._real, name)


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
        cfg = rm.compute_llamacpp_config("chat")

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
        cfg = rm.compute_llamacpp_config("chat")

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
        cfg = rm.compute_llamacpp_config("chat")

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
        cfg = rm.compute_llamacpp_config("chat")

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
        cfg = rm.compute_llamacpp_config("chat")

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
        for mt in ["chat", "multimodal", "reasoning", "embedding"]:
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
            cfg = rm.compute_llamacpp_config("chat")
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
            cfg = rm.compute_llamacpp_config("chat")
            # 8GB tier caps ctx_size to 4096 regardless of DB config
            assert cfg["ctx_size"] == 4096


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
    """Tests for dynamic LTX-Video VRAM estimation."""

    def test_uses_measured_when_available(self):
        """If a measurement exists in DB, it takes precedence over file size."""
        with patch("app.database.get_vram_estimate") as mock_get:
            mock_get.return_value = {"measured_vram_mb": 6917, "module": "ltx-video"}
            rm = ResourceManager()
            result = rm.estimate_video_vram_needed()
            assert result == 6917

    def test_falls_back_to_env_when_no_files(self, tmp_path, monkeypatch):
        """If no model files exist, fall back to LTX_VIDEO_VRAM_MB env var."""
        monkeypatch.setenv("LTX_MODELS_DIR", str(tmp_path))
        monkeypatch.setenv("LTX_VIDEO_VRAM_MB", "9100")
        with patch("app.database.get_vram_estimate", return_value=None):
            rm = ResourceManager()
            result = rm.estimate_video_vram_needed()
            assert result == 9100

    def test_falls_back_to_default_when_no_files_no_env(self, tmp_path, monkeypatch):
        """Without env var and no files, return default 8500."""
        monkeypatch.setenv("LTX_MODELS_DIR", str(tmp_path))
        monkeypatch.delenv("LTX_VIDEO_VRAM_MB", raising=False)
        with patch("app.database.get_vram_estimate", return_value=None):
            rm = ResourceManager()
            result = rm.estimate_video_vram_needed()
            assert result == 8500

    def test_computes_from_files_t5_on_cpu(self, tmp_path, monkeypatch):
        """File-based estimation: transformer + upscaler + 15% overhead (T5 stays on CPU)."""
        monkeypatch.setenv("LTX_MODELS_DIR", str(tmp_path))
        # 6.04 GB transformer, 482 MB upscaler
        files_sizes = {
            tmp_path / "ltxv-2b-0.9.8-distilled.safetensors": 6_300_000_000,
            tmp_path / "ltxv-spatial-upscaler-0.9.8.safetensors": 500_000_000,
        }
        t5_dir = tmp_path / "t5_encoder" / "text_encoder"
        t5_dir.mkdir(parents=True)
        # T5 on CPU — should NOT be counted
        files_sizes[t5_dir / "model-00001-of-00002.safetensors"] = 9_990_000_000
        files_sizes[t5_dir / "model-00002-of-00002.safetensors"] = 9_060_000_000
        for f in files_sizes:
            f.touch()

        real_stat = type(tmp_path).stat

        def fake_stat(self, *args, **kwargs):
            res = real_stat(self, *args, **kwargs)
            if self in files_sizes:
                return _FakeStat(res, files_sizes[self])
            return res

        with patch("app.database.get_vram_estimate", return_value=None), \
             patch.object(type(tmp_path), "stat", fake_stat):
            rm = ResourceManager()
            result = rm.estimate_video_vram_needed()
            # transformer (6007 MB) + upscaler (476 MB) = 6483 MB
            # + 15% overhead = 7456 MB (T5 on CPU is NOT counted)
            assert 7000 < result < 8000

    def test_no_t5_uses_transformer_only(self, tmp_path, monkeypatch):
        """When T5 directory is missing, count only transformer + upscaler."""
        monkeypatch.setenv("LTX_MODELS_DIR", str(tmp_path))
        # 6 GB transformer
        (tmp_path / "ltxv-2b-0.9.8-distilled.safetensors").touch()
        real_stat = type(tmp_path).stat

        def fake_stat(self, *args, **kwargs):
            res = real_stat(self, *args, **kwargs)
            if self.name == "ltxv-2b-0.9.8-distilled.safetensors":
                return _FakeStat(res, 6_000_000_000)
            return res

        with patch("app.database.get_vram_estimate", return_value=None), \
             patch.object(type(tmp_path), "stat", fake_stat):
            rm = ResourceManager()
            result = rm.estimate_video_vram_needed()
            # 5715 MB (6GB) + 15% overhead = ~6573 MB
            assert 6000 < result < 7500

    def test_measured_takes_precedence_over_files(self, tmp_path, monkeypatch):
        """Even if files exist, measured value wins."""
        monkeypatch.setenv("LTX_MODELS_DIR", str(tmp_path))
        (tmp_path / "ltxv-2b-0.9.8-distilled.safetensors").touch()

        with patch("app.database.get_vram_estimate", return_value={"measured_vram_mb": 6800}):
            rm = ResourceManager()
            result = rm.estimate_video_vram_needed()
            assert result == 6800

    def test_queries_ltxvideo_vram_info(self, monkeypatch):
        """When no measured value, query ltxvideo for component sizes."""
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "component_sizes_mb": {
                "transformer": 6040,
                "upscaler": 482,
                "t5_encoder_dir": 19050,
            }
        }
        with patch("app.database.get_vram_estimate", return_value=None), \
             patch.dict("os.environ", {"LTX_VIDEO_WRAPPER_URL": "http://test:7872"}), \
             patch.dict("sys.modules", {"requests": MagicMock(get=MagicMock(return_value=mock_resp))}):
            rm = ResourceManager()
            result = rm.estimate_video_vram_needed()
            # transformer (6040) + upscaler (482) = 6522 MB
            # + 15% overhead = 7500 MB. T5 NOT counted (on CPU).
            assert 7000 < result < 8000

    def test_falls_through_when_ltxvideo_unreachable(self, tmp_path, monkeypatch):
        """If ltxvideo HTTP fails and no files, use env fallback."""
        monkeypatch.setenv("LTX_MODELS_DIR", str(tmp_path))
        monkeypatch.setenv("LTX_VIDEO_VRAM_MB", "9300")

        with patch("app.database.get_vram_estimate", return_value=None), \
             patch.dict("os.environ", {"LTX_VIDEO_WRAPPER_URL": "http://test:7872"}), \
             patch.dict("sys.modules", {"requests": MagicMock(get=MagicMock(side_effect=ConnectionError("boom")))}):
            rm = ResourceManager()
            result = rm.estimate_video_vram_needed()
            assert result == 9300


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

        with patch.dict("os.environ", {"LTX_VIDEO_WRAPPER_URL": "http://test:7872"}), \
             patch.dict("sys.modules", {"requests": MagicMock(post=mock_post)}):
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

        with patch("time.sleep"), patch.dict("os.environ", {"LTX_VIDEO_WRAPPER_URL": "http://test:7872"}), \
             patch.dict("sys.modules", {"requests": MagicMock(post=mock_post)}):
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

        with patch("time.sleep"), patch.dict("os.environ", {"LTX_VIDEO_WRAPPER_URL": "http://test:7872"}), \
             patch.dict("sys.modules", {"requests": MagicMock(post=mock_post)}):
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
        with patch("time.sleep"), patch.dict("os.environ", {"LTX_VIDEO_WRAPPER_URL": "http://test:7872"}), \
             patch.dict("sys.modules", {"requests": MagicMock(post=mock_post)}):
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
