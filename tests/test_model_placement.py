"""A model that llama.cpp left in RAM is detected and reloaded.

Measured on the live instance: after the config was regenerated, llama-swap
preloaded the multimodal model while the reasoning model still held 12.9 GB, so
llama.cpp kept the weights in RAM and only the vision encoder and the KV cache
reached the card. Generation fell to 11 tok/s (prefill 49) against 75 (prefill
933) after a reload, and `ttl: 0` kept that placement for eight hours with
nothing in the log.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.resource_manager import PLACEMENT_FLOOR, ResourceManager, placement_is_degraded

pytestmark = pytest.mark.unit


class TestPlacementPredicate:
    def test_a_model_with_its_weights_in_ram_is_degraded(self):
        # 2.4 GB on the card for a 5 GB estimate — the live failure.
        assert placement_is_degraded(5058, 2414) is True

    def test_a_fully_placed_model_is_not_degraded(self):
        assert placement_is_degraded(5058, 8660) is False

    def test_the_floor_is_the_boundary(self):
        expected = 5000
        assert placement_is_degraded(expected, int(expected * PLACEMENT_FLOOR) - 1) is True
        assert placement_is_degraded(expected, int(expected * PLACEMENT_FLOOR) + 1) is False

    @pytest.mark.parametrize("expected,used", [(0, 2414), (5058, 0), (5058, None)])
    def test_unknown_numbers_are_never_a_reason_to_reload(self, expected, used):
        """A missing measurement must not trigger a reload loop."""
        assert placement_is_degraded(expected, used) is False


class TestReloadOfAMisplacedModel:
    def _manager(self, used_mb: int) -> ResourceManager:
        rm = ResourceManager()
        rm.hardware.total_vram_mb = 16311
        rm._poll_vram = MagicMock(side_effect=lambda: setattr(rm.hardware, "available_vram_mb", 16311 - used_mb))
        return rm

    def test_a_misplaced_resident_is_unloaded(self):
        rm = self._manager(2414)
        with patch("app.resource_manager.requests.post") as post:
            reloaded = rm._reload_if_misplaced("multimodal", 5058, {"model": "multimodal"})
        assert reloaded is True
        post.assert_called_once()
        assert post.call_args.args[0].endswith("/api/models/unload")
        assert post.call_args.kwargs["json"] == {"model": "multimodal"}

    def test_a_healthy_resident_is_left_alone(self):
        rm = self._manager(8660)
        with patch("app.resource_manager.requests.post") as post:
            assert rm._reload_if_misplaced("multimodal", 5058, {"model": "multimodal"}) is False
        post.assert_not_called()

    def test_the_second_call_within_the_window_does_not_unload_again(self):
        """A model that cannot be placed must not be reloaded on every request."""
        rm = self._manager(2414)
        with patch("app.resource_manager.requests.post"):
            assert rm._reload_if_misplaced("multimodal", 5058, {"model": "multimodal"}) is True
            assert rm._reload_if_misplaced("multimodal", 5058, {"model": "multimodal"}) is False

    def test_a_failed_unload_is_logged_not_raised(self):
        rm = self._manager(2414)
        with patch("app.resource_manager.requests.post", side_effect=OSError("down")):
            assert rm._reload_if_misplaced("multimodal", 5058, {"model": "multimodal"}) is True


def test_ensure_vram_for_does_not_trust_a_misplaced_resident():
    """The 'already loaded — skipping' shortcut must not hide a CPU placement."""
    rm = ResourceManager()
    rm.hardware.platform = "gpu"
    rm.hardware.cuda_detected = True
    rm.hardware.total_vram_mb = 16311
    rm.hardware.available_vram_mb = 16311 - 2414
    rm._poll_vram = MagicMock()
    rm.get_vram_needed_mb = MagicMock(return_value=5058)
    rm.estimate_cpu_ram_mb = MagicMock(return_value=0)
    running = {"running": [{"model": "multimodal", "cmd": "llama-server -m Qwen3VL-8B-Instruct-Q4_K_M.gguf"}]}
    response = MagicMock(status_code=200, json=MagicMock(return_value=running))

    with (
        patch("app.resource_manager.requests.get", return_value=response),
        patch("app.resource_manager.requests.post") as post,
        patch("app.model_config.get_model_config", return_value={"model_name": "Qwen3VL-8B-Instruct-Q4_K_M.gguf"}),
    ):
        rm.ensure_vram_for("multimodal")

    assert post.called, "the misplaced resident was treated as ready"
    assert any("api/models/unload" in str(call.args[0]) for call in post.call_args_list)
