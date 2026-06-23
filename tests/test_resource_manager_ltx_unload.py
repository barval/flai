"""Tests for ResourceManager.unload_video_pipeline — LTX-Video unload optimizations.

Covers the v8.8 fixes:
  - Pre-flight GET /v1/vram_info: skip HTTP when pipeline not loaded
  - Reachable success condition (clamped to total - 1GB)
  - 30s result cache (eliminates double-call overhead)
  - 3 consecutive HTTP timeouts trigger docker restart
  - Docker socket unavailable: fail safely without raising
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from app.resource_manager import ResourceManager


@pytest.fixture
def rm():
    """ResourceManager with mocked VRAM detection so __init__ doesn't touch nvidia-smi."""
    with patch.object(ResourceManager, "detect_hardware"):
        manager = ResourceManager()
        manager.hardware.total_vram_mb = 16311
        manager.hardware.available_vram_mb = 15229
        manager.hardware.cuda_detected = True
        return manager


def _mock_response(status_code: int, json_data: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


def _patch_sleep():
    """Patch time.sleep globally so tests don't actually wait."""
    return patch("time.sleep", lambda *_a, **_kw: None)


@pytest.mark.unit
class TestUnloadVideoPipelinePreflight:
    """A1: GET /v1/vram_info pre-flight check."""

    def test_preflight_skips_unload_when_pipeline_not_loaded(self, rm):
        with (
            _patch_sleep(),
            patch("app.resource_manager.requests.get") as mock_get,
            patch("app.resource_manager.requests.post") as mock_post,
        ):
            mock_get.return_value = _mock_response(200, {"pipeline_loaded": False})

            result = rm.unload_video_pipeline()

            assert result is True
            # Only the pre-flight GET was made — no POST /v1/unload
            assert mock_get.call_count == 1
            assert mock_post.call_count == 0
            # Cache should be primed
            assert rm._last_ltx_unload_at > 0
            # Hang counter should be reset
            assert rm._ltx_unload_consecutive_timeouts == 0

    def test_unload_proceeds_when_pipeline_loaded(self, rm):
        with (
            _patch_sleep(),
            patch("app.resource_manager.requests.get") as mock_get,
            patch("app.resource_manager.requests.post") as mock_post,
        ):
            # First call: pre-flight (loaded), second: poll, third: nvidia-smi
            mock_get.side_effect = [
                _mock_response(200, {"pipeline_loaded": True}),  # pre-flight
                _mock_response(200, {"pipeline_loaded": True}),  # polling iteration
            ]
            mock_post.return_value = _mock_response(200)

            # Simulate VRAM growing to target during polling
            poll_count = [0]

            def fake_poll():
                poll_count[0] += 1
                # After 1 poll iteration, free VRAM reaches target
                rm.hardware.available_vram_mb = 16311 - 500  # close to total

            with patch.object(rm, "_poll_vram", side_effect=fake_poll):
                result = rm.unload_video_pipeline()

            assert result is True
            # Pre-flight + at least one poll iteration
            assert mock_get.call_count >= 1
            # POST /v1/unload was made because pipeline was loaded
            assert mock_post.call_count == 1

    def test_preflight_continues_on_error(self, rm):
        """If GET /v1/vram_info fails (timeout/connection error), fall through to regular flow."""
        with (
            _patch_sleep(),
            patch("app.resource_manager.requests.get") as mock_get,
            patch("app.resource_manager.requests.post") as mock_post,
        ):
            mock_get.side_effect = ConnectionError("refused")
            mock_post.return_value = _mock_response(200)

            with patch.object(rm, "_poll_vram"):
                result = rm.unload_video_pipeline()

            # Continued to regular unload flow (3 attempts)
            assert mock_post.call_count == 3
            # Returned False because polling loop never saw target met
            # (since fake_poll doesn't bump free VRAM)
            assert result is False


@pytest.mark.unit
class TestUnloadVideoPipelineCache:
    """A3: 30s result cache eliminates double-call overhead."""

    def test_cache_returns_true_within_30s(self, rm):
        with (
            _patch_sleep(),
            patch("app.resource_manager.requests.get") as mock_get,
        ):
            mock_get.return_value = _mock_response(200, {"pipeline_loaded": False})

            # Prime cache
            rm.unload_video_pipeline()
            assert mock_get.call_count == 1

            # Second call within 30s — should hit cache, not HTTP
            result = rm.unload_video_pipeline()
            assert result is True
            assert mock_get.call_count == 1  # no new HTTP call

    def test_cache_expires_after_30s(self, rm):
        with (
            _patch_sleep(),
            patch("app.resource_manager.requests.get") as mock_get,
        ):
            mock_get.return_value = _mock_response(200, {"pipeline_loaded": False})

            # Prime cache
            rm.unload_video_pipeline()
            first_call_count = mock_get.call_count

            # Simulate time passing beyond 30s
            rm._last_ltx_unload_at = time.time() - 31

            # New call should re-issue the pre-flight
            rm.unload_video_pipeline()
            assert mock_get.call_count > first_call_count


@pytest.mark.unit
class TestUnloadVideoPipelineSuccessCondition:
    """A2: Reachable success condition (clamped to total - 1GB)."""

    def test_condition_with_free_before_close_to_total(self, rm):
        """When free_before is near total, the condition must still be reachable."""
        # Setup: free_before = 15229, total = 16311 — Bug #1 scenario
        rm.hardware.available_vram_mb = 15229
        rm.hardware.total_vram_mb = 16311

        with (
            _patch_sleep(),
            patch("app.resource_manager.requests.get") as mock_get,
            patch("app.resource_manager.requests.post") as mock_post,
        ):
            # Pre-flight says pipeline loaded (otherwise we'd skip the loop)
            mock_get.return_value = _mock_response(200, {"pipeline_loaded": True})
            mock_post.return_value = _mock_response(200)

            # After unload, VRAM should grow toward total-1GB = 15311
            poll_count = [0]

            def fake_poll():
                poll_count[0] += 1
                # Simulate empty_cache settling: free grows past target (15311)
                if poll_count[0] >= 2:
                    rm.hardware.available_vram_mb = 15400

            with patch.object(rm, "_poll_vram", side_effect=fake_poll):
                start = time.monotonic()
                result = rm.unload_video_pipeline()
                elapsed = time.monotonic() - start

            # Should return True within a couple of poll iterations, not 30s
            assert result is True
            # 8 sleeps × 1s = 8s max; in practice ~2s with our fake_poll
            assert elapsed < 5.0


@pytest.mark.unit
class TestUnloadVideoPipelineDockerRestart:
    """B: docker restart on 3 consecutive HTTP timeouts."""

    def test_three_consecutive_timeouts_trigger_restart(self, rm):
        with (
            _patch_sleep(),
            patch("app.resource_manager.requests.get") as mock_get,
            patch("app.resource_manager.requests.post") as mock_post,
        ):
            # Pre-flight: pipeline loaded
            mock_get.return_value = _mock_response(200, {"pipeline_loaded": True})

            import requests as req

            mock_post.side_effect = req.exceptions.ReadTimeout("Read timed out. (read timeout=30)")

            with (
                patch.object(rm, "_poll_vram"),
                patch.object(rm, "_maybe_restart_ltx_video") as mock_restart,
            ):
                rm.unload_video_pipeline()

            # 3 attempts × 1 timeout = 3 consecutive timeouts
            assert mock_post.call_count == 3
            assert rm._ltx_unload_consecutive_timeouts == 3
            mock_restart.assert_called_once()

    def test_successful_unload_resets_timeout_counter(self, rm):
        with (
            _patch_sleep(),
            patch("app.resource_manager.requests.get") as mock_get,
            patch("app.resource_manager.requests.post") as mock_post,
        ):
            # Pre-flight: loaded
            mock_get.return_value = _mock_response(200, {"pipeline_loaded": True})

            import requests as req

            # First call: timeout. Second call: success.
            mock_post.side_effect = [
                req.exceptions.ReadTimeout("Read timed out"),
                _mock_response(200),
            ]

            poll_count = [0]

            def fake_poll():
                poll_count[0] += 1
                if poll_count[0] >= 1:
                    rm.hardware.available_vram_mb = 16311 - 500

            with (
                patch.object(rm, "_poll_vram", side_effect=fake_poll),
                patch.object(rm, "_maybe_restart_ltx_video") as mock_restart,
            ):
                rm.unload_video_pipeline()

            # Counter was incremented to 1, then reset on success
            assert rm._ltx_unload_consecutive_timeouts == 0
            mock_restart.assert_not_called()

    def test_restart_protection_within_5_min(self, rm):
        """Rate-limit: if restart was triggered < 5 min ago, don't trigger again."""
        with patch("requests.post") as mock_docker_post:
            mock_docker_post.return_value = _mock_response(204)

            # Pre-set the last restart time to 1 min ago
            rm._ltx_restart_initiated_at = time.time() - 60

            rm._maybe_restart_ltx_video()

            # Docker restart was NOT called because of rate limit
            mock_docker_post.assert_not_called()
            # last-init time NOT updated (early return)
            assert abs(rm._ltx_restart_initiated_at - (time.time() - 60)) < 1.0

    def test_docker_socket_unavailable_fails_safely(self, rm):
        """Docker CLI not available → log error, don't raise."""
        with patch("subprocess.run", side_effect=FileNotFoundError("docker not found")):
            # Should not raise
            rm._maybe_restart_ltx_video()

        # Restart was attempted but failed — last-init time still updated
        assert rm._ltx_restart_initiated_at > 0
        # Counter NOT reset (we couldn't restart)
        assert rm._ltx_unload_consecutive_timeouts == 0

    def test_docker_socket_success_resets_state(self, rm):
        """Successful docker restart resets counter and cache."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            rm._ltx_unload_consecutive_timeouts = 3
            rm._last_ltx_unload_at = time.time()

            rm._maybe_restart_ltx_video()

            assert rm._ltx_unload_consecutive_timeouts == 0
            assert rm._last_ltx_unload_at == 0.0
            assert rm._ltx_restart_initiated_at > 0
