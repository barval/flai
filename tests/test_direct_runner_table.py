"""The direct-step dispatch is a table, not a chain.

A capability added to the manifest must either have a runner here or fail a test
— the if/elif chain it replaces had no such check, so a typo in a plan step
degraded to "unknown direct step tool" at runtime.
"""

from __future__ import annotations

import pytest

from app.tasks.runner import DIRECT_RUNNERS, ERROR_TEXT_TOOLS, OWN_METADATA_TOOLS
from app.tool_registry import TOOL_SPECS


def _direct_ids() -> set[str]:
    return {spec.id for spec in TOOL_SPECS if spec.exec == "direct"}


@pytest.mark.unit
def test_every_direct_capability_has_a_runner():
    assert _direct_ids() <= set(DIRECT_RUNNERS)


@pytest.mark.unit
def test_the_table_holds_nothing_that_is_not_a_direct_capability():
    assert set(DIRECT_RUNNERS) <= _direct_ids()


@pytest.mark.unit
def test_every_runner_is_callable():
    for tool_id, runner in DIRECT_RUNNERS.items():
        assert callable(runner), tool_id


@pytest.mark.unit
def test_the_policy_sets_name_real_runners():
    assert set(DIRECT_RUNNERS) >= OWN_METADATA_TOOLS
    assert set(DIRECT_RUNNERS) >= ERROR_TEXT_TOOLS


@pytest.mark.unit
def test_the_policy_sets_are_what_they_were():
    # These two sets decide where a step's header metadata comes from and whether
    # a failure reason is shown; they must not drift silently.
    assert {"llm", "continuity"} == OWN_METADATA_TOOLS
    assert {"calculate", "continuity"} == ERROR_TEXT_TOOLS


@pytest.mark.unit
def test_an_unknown_tool_is_reported_not_guessed(app):
    import app.tasks.runner as runner_module

    with app.app_context():

        def cancel_check():
            return False

        result = runner_module.run_direct_step(
            app,
            {"id": "s1", "tool": "no_such_tool", "goal": "x"},
            "",
            cancel_check,
        )
    assert result["status"] == "error"
    assert "no_such_tool" in result["result"] or result["result"] == ""
