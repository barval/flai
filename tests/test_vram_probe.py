# tests/test_vram_probe.py
"""VRAM measurement and the eviction plan built on it."""

import pytest

from app.vram_probe import (
    is_plausible_measurement,
    kv_per_token_from_metadata,
    parse_compute_apps,
    parse_proc_status_rss_mb,
    plan_unloads,
    summarize_probe,
)

pytestmark = pytest.mark.unit


def test_summarize_probe_separates_weights_from_kv():
    """The footprint of a loaded model is weights + vision encoder + KV/buffers."""
    result = summarize_probe(
        baseline_mb=800,
        loaded_mb=8494,
        weights_mb=4795,
        mmproj_mb=1105,
        ctx_size=32768,
    )

    assert result["total_mb"] == 7694
    assert result["kv_and_buffers_mb"] == 1794
    assert result["kv_mb_per_token"] == pytest.approx(0.0547, abs=0.001)


def test_summarize_probe_handles_a_model_without_a_vision_encoder():
    result = summarize_probe(baseline_mb=0, loaded_mb=13736, weights_mb=11715, mmproj_mb=0, ctx_size=24576)

    assert result["kv_and_buffers_mb"] == 2021
    assert result["kv_mb_per_token"] == pytest.approx(0.0822, abs=0.001)


def test_summarize_probe_without_context_reports_zero_per_token():
    result = summarize_probe(baseline_mb=0, loaded_mb=600, weights_mb=600, mmproj_mb=0, ctx_size=0)

    assert result["kv_mb_per_token"] == 0.0


def test_plan_unloads_keeps_everything_when_it_fits():
    """Co-residency is safe: the model loads next to what is already resident."""
    loaded = [{"name": "multimodal", "mb": 7700, "last_used": 100.0}]

    plan = plan_unloads(loaded, needed_mb=700, total_mb=16311, headroom_mb=1200)

    assert plan == []


def test_plan_unloads_evicts_the_least_recently_used_model():
    loaded = [
        {"name": "multimodal", "mb": 7700, "last_used": 100.0},
        {"name": "embedding", "mb": 700, "last_used": 50.0},
    ]

    plan = plan_unloads(loaded, needed_mb=13736, total_mb=16311, headroom_mb=1200)

    assert plan == ["embedding", "multimodal"]


def test_plan_unloads_stops_as_soon_as_the_new_model_fits():
    loaded = [
        {"name": "multimodal", "mb": 7700, "last_used": 10.0},
        {"name": "video", "mb": 6000, "last_used": 90.0},
    ]

    plan = plan_unloads(loaded, needed_mb=13736, total_mb=24000, headroom_mb=1200)

    assert plan == ["multimodal"]


def test_plan_unloads_never_keeps_a_model_it_cannot_afford():
    """A protected model still goes when the window cannot hold both."""
    loaded = [{"name": "multimodal", "mb": 7700, "last_used": 100.0}]

    plan = plan_unloads(loaded, needed_mb=13736, total_mb=16311, headroom_mb=1200, protect={"multimodal"})

    assert plan == ["multimodal"]


def test_plan_unloads_keeps_the_protected_model_while_possible():
    loaded = [
        {"name": "multimodal", "mb": 7700, "last_used": 100.0},
        {"name": "embedding", "mb": 700, "last_used": 5.0},
    ]

    plan = plan_unloads(loaded, needed_mb=800, total_mb=16311, headroom_mb=1200, protect={"multimodal", "embedding"})

    assert plan == []


def test_plan_unloads_with_nothing_loaded_is_a_no_op():
    assert plan_unloads([], needed_mb=13736, total_mb=16311, headroom_mb=1200) == []


def test_parse_compute_apps_sums_only_the_model_server():
    """A stray CUDA context on the card is not part of the model's footprint."""
    csv = "1102684, /opt/conda/bin/python3.11, 128\n1530235, llama-server, 7694\n"

    assert parse_compute_apps(csv) == 7694


def test_parse_compute_apps_without_a_model_server():
    assert parse_compute_apps("1102684, /opt/conda/bin/python3.11, 128\n") == 0


def test_kv_per_token_follows_the_model_shapes():
    """Reasoning: 40 blocks, 2 KV heads, 256+256 — 0.022 MB/token at q4_0."""
    meta = {"block_count": 40, "head_count_kv": 2, "key_length": 256, "value_length": 256}

    assert kv_per_token_from_metadata(meta) == pytest.approx(0.022, abs=0.0005)


def test_kv_per_token_of_a_wide_vision_model_is_larger():
    meta = {"block_count": 36, "head_count_kv": 8, "key_length": 128, "value_length": 128}

    assert kv_per_token_from_metadata(meta) == pytest.approx(0.0396, abs=0.0005)


def test_kv_per_token_without_shapes_is_unknown():
    assert kv_per_token_from_metadata({"block_count": 40}) is None


def test_measured_total_is_used_when_model_and_window_match():
    from unittest.mock import patch

    from app.resource_manager import ResourceManager

    rm = ResourceManager()
    with (
        patch(
            "app.database.get_vram_estimate",
            return_value={"model_name": "m.gguf", "context_length": 32768, "measured_vram_mb": 7700},
        ),
        patch(
            "app.model_config.get_model_config",
            return_value={"model_name": "m.gguf", "context_length": 32768},
        ),
    ):
        assert rm.measured_total_mb("multimodal") == 7700


def test_measured_total_is_dropped_when_the_window_changed():
    """A bigger window needs more KV; the old sample would under-budget it."""
    from unittest.mock import patch

    from app.resource_manager import ResourceManager

    rm = ResourceManager()
    with (
        patch(
            "app.database.get_vram_estimate",
            return_value={"model_name": "m.gguf", "context_length": 16384, "measured_vram_mb": 5000},
        ),
        patch(
            "app.model_config.get_model_config",
            return_value={"model_name": "m.gguf", "context_length": 32768},
        ),
    ):
        assert rm.measured_total_mb("multimodal") is None


def test_measured_total_is_dropped_when_the_model_changed():
    from unittest.mock import patch

    from app.resource_manager import ResourceManager

    rm = ResourceManager()
    with (
        patch(
            "app.database.get_vram_estimate",
            return_value={"model_name": "old.gguf", "context_length": 32768, "measured_vram_mb": 7700},
        ),
        patch(
            "app.model_config.get_model_config",
            return_value={"model_name": "new.gguf", "context_length": 32768},
        ),
    ):
        assert rm.measured_total_mb("multimodal") is None


def test_a_measurement_below_the_weights_is_not_plausible():
    """The sample missed the unload: the model was still resident at baseline."""
    assert is_plausible_measurement(measured_mb=2, weights_mb=4794, mmproj_mb=1105) is False


def test_a_full_measurement_is_plausible():
    assert is_plausible_measurement(measured_mb=7700, weights_mb=4794, mmproj_mb=1105) is True


def test_a_model_smaller_than_its_file_is_still_plausible():
    """bge-m3 Q8_0: 541 MB resident against a 605 MB file (tokenizer + padding)."""
    assert is_plausible_measurement(measured_mb=541, weights_mb=605, mmproj_mb=0) is True
