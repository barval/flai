"""The depth axis must name today's values exactly, so later work cannot move them."""

from __future__ import annotations

import pytest

from app.depth import DEPTH_PRESETS, DEPTH_SOURCES, DepthBudget, depth_for


@pytest.mark.unit
def test_norm_preset_matches_the_documented_defaults():
    norm = DEPTH_PRESETS["norm"]
    assert (norm.turns, norm.attempts, norm.revisions, norm.variants) == (40, 3, 8, 1)
    assert norm.verify == "each"


@pytest.mark.unit
def test_norm_preset_matches_the_live_configuration(app):
    norm = DEPTH_PRESETS["norm"]
    assert app.config["TASK_MAX_STEPS"] == norm.turns
    assert app.config["TASK_MAX_REVISIONS"] == norm.revisions
    assert app.config["TASK_STEP_MAX_ATTEMPTS"] == norm.attempts


@pytest.mark.unit
def test_presets_are_ordered():
    for field in ("turns", "attempts", "variants", "revisions"):
        assert getattr(DEPTH_PRESETS["min"], field) <= getattr(DEPTH_PRESETS["norm"], field)
        assert getattr(DEPTH_PRESETS["norm"], field) <= getattr(DEPTH_PRESETS["max"], field)
    assert DEPTH_PRESETS["min"].turns < DEPTH_PRESETS["max"].turns


@pytest.mark.unit
def test_min_verifies_nothing_and_max_generates_variants():
    assert DEPTH_PRESETS["min"].verify == "off"
    assert DEPTH_PRESETS["max"].variants > DEPTH_PRESETS["norm"].variants


@pytest.mark.unit
def test_unknown_level_falls_back_to_norm():
    assert depth_for("turbo") == DEPTH_PRESETS["norm"]
    assert depth_for("min") == DEPTH_PRESETS["min"]


@pytest.mark.unit
def test_verify_is_a_closed_set():
    assert {preset.verify for preset in DEPTH_PRESETS.values()} <= {"off", "final", "each"}


@pytest.mark.unit
def test_every_scattered_constant_has_a_home():
    # The seven constants that describe depth today must be accounted for, or the
    # axis is incomplete and the next refactor will miss one.
    assert set(DEPTH_SOURCES) == {
        "TASK_STEP_MAX_ATTEMPTS",
        "TASK_STEP_MAX_REPAIRS",
        "TASK_STEP_REDO_MAX",
        "TASK_MAX_REVISIONS",
        "TASK_MAX_STEPS",
        "RLM_MAX_STEPS",
    }
    # Chat is bounded by the user's patience, not by a background budget, so the
    # tool-iteration cap stays outside the axis on purpose.
    assert "MAX_TOOL_ITERATIONS" not in DEPTH_SOURCES
    assert set(DEPTH_SOURCES.values()) <= set(DepthBudget.__dataclass_fields__)
