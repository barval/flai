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

    def test_a_model_that_is_still_loading_is_not_judged(self):
        """Reading the card mid-upload calls a healthy load a failed placement.

        Measured live: 932 MB of a 7 597 MB model, seen while that same load was
        still running.
        """
        rm = self._manager(2414)
        with patch("app.resource_manager.requests.post") as post:
            assert rm._reload_if_misplaced("multimodal", 5058, {"model": "multimodal", "state": "starting"}) is False
        post.assert_not_called()

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


class TestAStoredMeasurementCannotBlindTheCheck:
    """The degraded placement measured itself into the model's own estimate.

    Live, 2026-11-05: `model_vram_estimates` held 3 230 MB for Qwen3VL at a
    32 768 window (4 917 writes, the last one during the slow answer), while the
    same model measured 7 893 MB at 16 384 and its metadata estimate is
    7 938 MB. `get_vram_needed_mb()` returned 3 230, so a card holding 2 587 MB
    looked healthy, the misplaced model was never reloaded, and every answer
    recorded the degraded number again.
    """

    def _manager(self, monkeypatch, measured_mb: int | None) -> ResourceManager:
        monkeypatch.setattr(
            "app.model_config.get_model_config",
            lambda module: {"model_name": "Qwen3VL-8B-Instruct-Q4_K_M.gguf", "context_length": 32768},
        )
        monkeypatch.setattr(
            "app.utils.get_gguf_models_cached",
            lambda path: {"Qwen3VL-8B-Instruct-Q4_K_M": {"file_size_mb": 5000, "block_count": 36}},
        )
        monkeypatch.setattr("app.utils.get_mmproj_size_mb", lambda name: 700)
        monkeypatch.setattr(
            "app.database.get_vram_estimate",
            lambda module: {
                "model_name": "Qwen3VL-8B-Instruct-Q4_K_M",
                "context_length": 32768,
                "measured_vram_mb": measured_mb,
            },
        )
        manager = ResourceManager()
        manager.hardware.total_vram_mb = 16311
        manager.compute_llamacpp_config = MagicMock(return_value={"n_gpu_layers": -1})
        return manager

    def test_the_weights_and_the_vision_encoder_are_the_floor(self, monkeypatch):
        manager = self._manager(monkeypatch, 3230)
        assert manager.model_weights_mb("multimodal") == 5700

    def test_a_measurement_below_the_model_files_is_ignored(self, monkeypatch):
        manager = self._manager(monkeypatch, 3230)
        assert manager.measured_total_mb("multimodal") is None

    def test_an_implausible_row_does_not_shrink_the_estimate(self, monkeypatch):
        manager = self._manager(monkeypatch, 3230)
        assert manager.get_vram_needed_mb("multimodal") >= 5700

    def test_a_plausible_measurement_is_used_as_before(self, monkeypatch):
        manager = self._manager(monkeypatch, 8650)
        assert manager.measured_total_mb("multimodal") == 8650


class TestTheMeasurementJudgesThePlacement:
    """The card is read after every answer — judge there, not only at the gate.

    Option 1 of the plan: the number is only this model's when it is the only one
    resident, and a number below the model's own files means llama.cpp kept the
    weights in RAM, so the model is unloaded at once and the next request places
    it again.
    """

    def _manager(self, monkeypatch, used_mb: int, running: list[dict]) -> ResourceManager:
        monkeypatch.setattr(
            "app.model_config.get_model_config",
            lambda module: {"model_name": "Qwen3VL-8B-Instruct-Q4_K_M.gguf", "context_length": 32768},
        )
        monkeypatch.setattr(
            "app.utils.get_gguf_models_cached",
            lambda path: {"Qwen3VL-8B-Instruct-Q4_K_M": {"file_size_mb": 5000, "block_count": 36}},
        )
        monkeypatch.setattr("app.utils.get_mmproj_size_mb", lambda name: 700)
        monkeypatch.setattr("app.resource_manager.requests.get", lambda *a, **k: MagicMock(
            status_code=200, json=MagicMock(return_value={"running": running})
        ))
        manager = ResourceManager()
        manager.hardware.total_vram_mb = 16311
        manager.hardware.available_vram_mb = 16311 - used_mb
        manager.compute_llamacpp_config = MagicMock(return_value={"n_gpu_layers": -1})
        manager._poll_vram = MagicMock()
        return manager

    _MM = {"model": "multimodal", "state": "ready", "cmd": "llama-server -m /models/Qwen3VL-8B-Instruct-Q4_K_M.gguf"}

    def test_a_lone_misplaced_model_is_unloaded_right_after_the_answer(self, monkeypatch):
        manager = self._manager(monkeypatch, 2587, [self._MM])
        with patch("app.database.upsert_vram_estimate") as upsert, patch(
            "app.resource_manager.requests.post"
        ) as post:
            manager.measure_model_vram("multimodal", "Qwen3VL-8B-Instruct-Q4_K_M.gguf", 32768, -1)
        upsert.assert_not_called()
        assert post.called, "the misplaced model was left in RAM"
        assert post.call_args.kwargs["json"] == {"model": "multimodal"}

    def test_a_shared_card_is_not_attributed_to_one_model(self, monkeypatch):
        """Two residents: the card-wide number is not this model's footprint."""
        other = {"model": "reasoning", "state": "ready", "cmd": "llama-server -m /models/reasoning.gguf"}
        manager = self._manager(monkeypatch, 15000, [self._MM, other])
        with patch("app.database.upsert_vram_estimate") as upsert, patch("app.resource_manager.requests.post") as post:
            manager.measure_model_vram("multimodal", "Qwen3VL-8B-Instruct-Q4_K_M.gguf", 32768, -1)
        upsert.assert_not_called()
        post.assert_not_called()

    def test_a_healthy_lone_model_is_stored_and_left_alone(self, monkeypatch):
        manager = self._manager(monkeypatch, 8600, [self._MM])
        with patch("app.database.upsert_vram_estimate") as upsert, patch("app.resource_manager.requests.post") as post:
            manager.measure_model_vram("multimodal", "Qwen3VL-8B-Instruct-Q4_K_M.gguf", 32768, -1)
        assert upsert.call_args.kwargs["measured_mb"] == 8600
        post.assert_not_called()


class TestTheChatPathChecksThePlacement:
    """Option 2: a throttled check before the fast path serves a message."""

    def _manager(self, used_mb: int) -> ResourceManager:
        rm = ResourceManager()
        rm.hardware.platform = "gpu"
        rm.hardware.total_vram_mb = 16311
        rm.hardware.available_vram_mb = 16311 - used_mb
        rm._poll_vram = MagicMock()
        rm.get_vram_needed_mb = MagicMock(return_value=7597)
        return rm

    def test_a_misplaced_resident_is_not_reported_as_ok(self):
        rm = self._manager(2587)
        with patch("app.resource_manager.requests.post") as post:
            assert rm.resident_placement_ok("multimodal", {"model": "multimodal"}) is False
        assert post.called

    def test_a_healthy_resident_is_reported_ok(self):
        rm = self._manager(8600)
        with patch("app.resource_manager.requests.post") as post:
            assert rm.resident_placement_ok("multimodal", {"model": "multimodal"}) is True
        post.assert_not_called()

    def test_the_card_is_not_read_on_every_message(self):
        """One nvidia-smi a minute, not one per message."""
        rm = self._manager(8600)
        rm.resident_placement_ok("multimodal", {"model": "multimodal"})
        rm.resident_placement_ok("multimodal", {"model": "multimodal"})
        rm.resident_placement_ok("multimodal", {"model": "multimodal"})
        assert rm._poll_vram.call_count == 1


def test_the_chat_fast_path_reloads_a_model_whose_weights_are_in_ram():
    """Option 2 end to end: the shortcut must not serve a misplaced model.

    `_ensure_vram()` returns early for the always-resident chat model. With the
    placement check in front of that shortcut, a model that llama.cpp left in RAM
    is unloaded there and the full path loads it again, instead of the answer
    running at a tenth of the speed.
    """
    from app.llamacpp_client import LlamaCppClient

    backend = LlamaCppClient()
    backend._active_model_type = "multimodal"
    running = {
        "running": [
            {
                "model": "multimodal",
                "state": "ready",
                "cmd": "llama-server -m /models/Qwen3VL-8B-Instruct-Q4_K_M.gguf",
            }
        ]
    }
    response = MagicMock(status_code=200, json=MagicMock(return_value=running))
    manager = MagicMock()
    manager.resident_placement_ok.return_value = False
    manager.ensure_vram_for.return_value = True

    with (
        patch("app.llamacpp_client.requests.get", return_value=response),
        patch("app.llamacpp_client.get_model_config", return_value={"model_name": "Qwen3VL-8B-Instruct-Q4_K_M.gguf"}),
        patch("app.resource_manager.get_resource_manager", return_value=manager),
    ):
        assert backend._ensure_vram("multimodal") is True

    manager.resident_placement_ok.assert_called_once()
    manager.ensure_vram_for.assert_called_once_with("multimodal")


def test_the_chat_fast_path_is_untouched_when_the_model_is_on_the_card():
    from app.llamacpp_client import LlamaCppClient

    backend = LlamaCppClient()
    backend._active_model_type = "multimodal"
    running = {
        "running": [
            {
                "model": "multimodal",
                "state": "ready",
                "cmd": "llama-server -m /models/Qwen3VL-8B-Instruct-Q4_K_M.gguf",
            }
        ]
    }
    response = MagicMock(status_code=200, json=MagicMock(return_value=running))
    manager = MagicMock()
    manager.resident_placement_ok.return_value = True

    with (
        patch("app.llamacpp_client.requests.get", return_value=response),
        patch("app.llamacpp_client.get_model_config", return_value={"model_name": "Qwen3VL-8B-Instruct-Q4_K_M.gguf"}),
        patch("app.resource_manager.get_resource_manager", return_value=manager),
    ):
        assert backend._ensure_vram("multimodal") is True

    manager.ensure_vram_for.assert_not_called()


class TestTheReloadOnlyHappensWhenItCanHelp:
    def _manager(self, used_mb: int) -> ResourceManager:
        rm = ResourceManager()
        rm.hardware.total_vram_mb = 16311
        rm.hardware.available_vram_mb = 16311 - used_mb
        rm._poll_vram = MagicMock()
        return rm

    def test_no_unload_when_the_card_is_smaller_than_the_model(self):
        """The card cannot hold the model at all: reloading would land in the same remainder."""
        rm = self._manager(2400)
        rm.hardware.total_vram_mb = 8192
        with patch("app.resource_manager.requests.post") as post:
            assert rm._unload_misplaced("reasoning", {"model": "reasoning"}, 2400, 9627) is False
        post.assert_not_called()

    def test_a_lone_misplaced_model_is_unloaded_even_when_the_current_free_space_is_smaller(self):
        """Live 2026-11-10: 7 095 MB on the card for an estimated 9 627 MB.

        The free space *before* the unload was 9 216 MB, so the old check refused
        the reload and the answer ran for 176 s. The model was the only resident,
        so unloading it returns its own 7 095 MB and the reload fits the card.
        """
        rm = self._manager(7095)
        with patch("app.resource_manager.requests.post") as post:
            assert rm._unload_misplaced("reasoning", {"model": "reasoning"}, 7095, 9627) is True
        assert post.call_args.kwargs["json"] == {"model": "reasoning"}

    def test_the_retry_window_is_a_minute_not_ten(self):
        """An unload followed by a fresh load must not block the next verdict.

        Live, 2026-11-05: the check correctly found the model degraded at 23:04
        but the ten-minute window opened by an earlier unload still held it, so
        the answer ran at 6.7 tok/s.
        """
        rm = self._manager(2400)
        with patch("app.resource_manager.requests.post") as post:
            assert rm._unload_misplaced("multimodal", {"model": "multimodal"}, 2400, 8217) is True
            assert rm._unload_misplaced("multimodal", {"model": "multimodal"}, 2400, 8217) is False
        assert post.call_count == 1

    def test_an_old_unload_does_not_block_a_new_verdict(self):
        rm = self._manager(2400)
        with patch("app.resource_manager.requests.post"):
            assert rm._unload_misplaced("multimodal", {"model": "multimodal"}, 2400, 8217) is True
            rm._placement_retry_at["multimodal"] -= 61
            assert rm._unload_misplaced("multimodal", {"model": "multimodal"}, 2400, 8217) is True
