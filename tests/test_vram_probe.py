# tests/test_vram_probe.py
"""VRAM measurement and the eviction plan built on it."""

import pytest

from app.vram_probe import (
    derive_kv_per_token,
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


def test_derive_kv_per_token_from_a_measured_total():
    """measured total = weights + mmproj + fixed buffers + KV."""
    kv = derive_kv_per_token(measured_mb=7700, weights_mb=4795, mmproj_mb=1105, ctx_size=32768)

    assert kv == pytest.approx(0.0427, abs=0.001)


def test_derive_kv_per_token_rejects_an_impossible_measurement():
    """A total below the weights means the sample caught the wrong process."""
    assert derive_kv_per_token(measured_mb=1000, weights_mb=4795, mmproj_mb=0, ctx_size=32768) is None


def test_measured_kv_is_used_for_the_model_and_window_it_was_taken_with():
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
        patch("app.utils.get_gguf_models_cached", return_value={"m": {"file_size_mb": 4795}}),
        patch("app.utils.get_mmproj_size_mb", return_value=1105),
    ):
        assert rm.measured_kv_per_token("multimodal") == pytest.approx(0.0427, abs=0.001)


def test_measured_kv_is_dropped_when_the_window_changed():
    """A bigger window needs more KV; the old sample would under-budget it."""
    from unittest.mock import patch

    from app.resource_manager import ResourceManager

    rm = ResourceManager()
    with (
        patch(
            "app.database.get_vram_estimate",
            return_value={"model_name": "m.gguf", "context_length": 16384, "measured_vram_mb": 7700},
        ),
        patch(
            "app.model_config.get_model_config",
            return_value={"model_name": "m.gguf", "context_length": 32768},
        ),
        patch("app.utils.get_gguf_models_cached", return_value={"m": {"file_size_mb": 4795}}),
        patch("app.utils.get_mmproj_size_mb", return_value=1105),
    ):
        assert rm.measured_kv_per_token("multimodal") is None


def test_measured_kv_is_dropped_when_the_model_changed():
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
        assert rm.measured_kv_per_token("multimodal") is None


def test_parse_proc_status_rss_reads_kib_as_mib():
    """A CPU-only host has no VRAM; the model server's RSS is its footprint."""
    status = "Name:\tllama-server\nVmPeak:\t 9999999 kB\nVmRSS:\t 6144000 kB\n"

    assert parse_proc_status_rss_mb(status) == 6000


def test_parse_proc_status_rss_without_the_field_is_zero():
    assert parse_proc_status_rss_mb("Name:\tllama-server\n") == 0
