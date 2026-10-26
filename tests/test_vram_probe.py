# tests/test_vram_probe.py
"""VRAM measurement and the eviction plan built on it."""

import pytest

from app.vram_probe import plan_unloads, summarize_probe

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
