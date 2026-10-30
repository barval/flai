"""The depth axis supplies the budgets, and the default profile changes nothing."""

from __future__ import annotations

import pytest

from app.depth import DEPTH_PRESETS, DEPTH_SOURCES, depth_defaults, depth_for


@pytest.mark.unit
def test_norm_is_exactly_the_behaviour_that_shipped():
    # These four numbers are the historical constants: wiring the axis must be a
    # no-op until an operator picks another level.
    assert depth_defaults("norm") == {
        "TASK_MAX_STEPS": 40,
        "TASK_STEP_MAX_ATTEMPTS": 3,
        "TASK_MAX_REVISIONS": 8,
        "DEPTH_VERIFY": "each",
    }


@pytest.mark.unit
def test_an_unknown_level_never_widens_the_budget():
    assert depth_for("no_such_level") == DEPTH_PRESETS["norm"]


@pytest.mark.unit
def test_min_is_cheap_and_max_only_raises_effort():
    cheap = depth_defaults("min")
    assert cheap["TASK_MAX_STEPS"] == 1
    assert cheap["DEPTH_VERIFY"] == "off"
    # ``max`` buys a second opinion; it does not raise the safety ceilings.
    assert depth_defaults("max")["TASK_MAX_STEPS"] == depth_defaults("norm")["TASK_MAX_STEPS"]
    assert depth_defaults("max")["TASK_MAX_REVISIONS"] == depth_defaults("norm")["TASK_MAX_REVISIONS"]


@pytest.mark.unit
def test_every_mapped_constant_names_a_real_field():
    fields = set(DEPTH_PRESETS["norm"].__dataclass_fields__)
    for constant, field in DEPTH_SOURCES.items():
        assert field in fields, f"{constant} maps to an unknown field {field}"


@pytest.mark.unit
def test_the_chat_loop_is_not_part_of_the_background_budget():
    assert "MAX_TOOL_ITERATIONS" not in DEPTH_SOURCES


@pytest.mark.unit
def test_a_step_is_not_checked_when_the_profile_says_off(app, monkeypatch):
    from app.tasks import runner

    monkeypatch.setattr(runner, "DIRECT_RUNNERS", {"llm": lambda context: "an answer"})
    monkeypatch.setattr(runner, "run_checkpoint", lambda *a, **k: pytest.fail("checkpoint must not run"))
    outcome = runner.run_direct_step(
        app,
        {"id": "s1", "tool": "llm", "goal": "g", "checkpoint_criteria": "c"},
        "",
        lambda: False,
        lang="en",
        verify="off",
    )
    assert outcome["status"] == "passed"
    assert outcome["checkpoint"] == {"verdict": True, "explanation": "", "exec_seconds": 0.0}
