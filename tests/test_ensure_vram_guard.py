# tests/test_ensure_vram_guard.py
"""Tests for the VRAM-wait guard (v11.5 watchdog/queue race fix).

Covers:
  - ResourceManager.is_gpu_busy() includes the new _vram_wait_busy flag
  - ensure_vram_for() raises the flag for the whole unload+wait section
  - the wait loop re-unloads any model that appears in /running instead of
    sleeping until timeout (a model present at that point was respawned by
    an external actor — the watchdog — and VRAM would never free up)
"""

import logging
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from app.resource_manager import ResourceManager


@pytest.fixture
def rm():
    """ResourceManager with mocked VRAM detection so __init__ doesn't touch nvidia-smi."""
    with patch.object(ResourceManager, "detect_hardware"):
        manager = ResourceManager()
        manager.hardware.platform = "cuda"
        manager.hardware.total_vram_mb = 16311
        manager.hardware.available_vram_mb = 15229
        manager.hardware.cuda_detected = True
        return manager


def _resp(running):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"running": running}
    return resp


def _model(name):
    return {"name": name, "cmd": f"llama-server --model {name}.gguf"}


@pytest.mark.unit
class TestVramWaitFlag:
    """is_gpu_busy() composition + flag lifecycle inside ensure_vram_for."""

    def test_is_gpu_busy_false_by_default(self, rm):
        assert rm.is_gpu_busy() is False

    def test_sd_and_video_flags_compose(self, rm):
        rm.mark_sd_busy()
        assert rm.is_gpu_busy() is True
        rm.mark_sd_idle()
        assert rm.is_gpu_busy() is False
        rm.mark_video_busy()
        assert rm.is_gpu_busy() is True
        rm.mark_video_idle()
        assert rm.is_gpu_busy() is False

    def test_flag_up_during_wait_and_down_after(self, rm):
        seen = []

        def record_flag(llamacpp_url=None):
            seen.append(rm.is_gpu_busy())
            return True

        with (
            patch("time.sleep"),
            patch("app.resource_manager.requests.get", side_effect=[_resp([]), _resp([])]),
            patch.object(rm, "unload_llamacpp_model", side_effect=record_flag),
        ):
            rm.unload_video_pipeline = MagicMock()
            rm._poll_vram = MagicMock()
            result = rm.ensure_vram_for("reasoning", needed_mb=13736, timeout=5)

        assert result is True
        assert seen == [True]
        assert rm.is_gpu_busy() is False


@pytest.mark.unit
class TestVramWaitSelfHealing:
    """The wait loop re-unloads models instead of sleeping until timeout."""

    def test_reissues_unload_while_models_active(self, rm):
        # Poll 1 sees the model still shutting down, poll 2 is clean → OK.
        with (
            patch("time.sleep"),
            patch(
                "app.resource_manager.requests.get",
                side_effect=[_resp([]), _resp([_model("multimodal")]), _resp([])],
            ),
            patch.object(rm, "unload_llamacpp_model", return_value=True) as unload,
        ):
            rm.unload_video_pipeline = MagicMock()
            rm._poll_vram = MagicMock()
            result = rm.ensure_vram_for("reasoning", needed_mb=13736, timeout=10)

        assert result is True
        assert unload.call_count == 2

    def test_reunload_after_clean_poll_logs_warning(self, rm, caplog):
        # Poll 1 clean, the model respawns on poll 2 (external actor), poll 3 clean.
        with (
            patch("time.sleep"),
            patch(
                "app.resource_manager.requests.get",
                side_effect=[_resp([]), _resp([]), _resp([_model("multimodal")]), _resp([])],
            ),
            patch.object(rm, "unload_llamacpp_model", return_value=True) as unload,
        ):
            rm.unload_video_pipeline = MagicMock()
            rm._poll_vram = MagicMock()
            hw = MagicMock()
            type(hw).available_vram_mb = PropertyMock(side_effect=[7000, 14000])
            hw.total_vram_mb = 16311
            hw.cuda_detected = True
            rm.hardware = hw
            with caplog.at_level(logging.WARNING, logger="app.resource_manager"):
                result = rm.ensure_vram_for("reasoning", needed_mb=13736, timeout=10)

        assert result is True
        assert unload.call_count == 2
        assert any("reappeared" in r.message for r in caplog.records)

    def test_persistent_respawner_times_out_without_hang(self, rm):
        calls = {"n": 0}

        def fake_get(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return _resp([])  # step-1 check: nothing loaded
            return _resp([_model("multimodal")])  # external actor keeps respawning

        with (
            patch("time.sleep"),
            patch("app.resource_manager.requests.get", side_effect=fake_get),
            patch.object(rm, "unload_llamacpp_model", return_value=True) as unload,
        ):
            rm.unload_video_pipeline = MagicMock()
            rm._poll_vram = MagicMock()
            result = rm.ensure_vram_for("reasoning", needed_mb=13736, timeout=1)

        assert result is False
        assert unload.call_count >= 2

    def test_clean_shutdown_single_unload(self, rm):
        with (
            patch("time.sleep"),
            patch("app.resource_manager.requests.get", side_effect=[_resp([]), _resp([])]),
            patch.object(rm, "unload_llamacpp_model", return_value=True) as unload,
        ):
            rm.unload_video_pipeline = MagicMock()
            rm._poll_vram = MagicMock()
            result = rm.ensure_vram_for("reasoning", needed_mb=13736, timeout=5)

        assert result is True
        assert unload.call_count == 1
