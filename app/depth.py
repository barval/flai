"""The depth axis: how much work the harness spends on one request.

Depth is measured in **model calls**, not in thinking tokens. That choice is
deliberate:

* it works on a non-reasoning model, where a "thinking budget" is a dead control;
* it can be priced before the call (turns x calls per turn), so a strictly
  sequential GPU queue can plan instead of guess;
* the mechanisms already exist in the codebase as seven scattered constants,
  which this module names and groups.

The presets are the **defaults** of the config keys that already exist:
``DEPTH_LEVEL`` (default ``norm``) fills ``TASK_MAX_STEPS``, ``TASK_STEP_MAX_ATTEMPTS``
and ``TASK_MAX_REVISIONS`` and sets ``DEPTH_VERIFY``. An explicit key in the
environment still wins, so an operator can tune one number without adopting a
whole profile, and ``norm`` reproduces the historical values exactly — the wiring
changes nothing until someone chooses a different level.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DepthBudget:
    """How much work one request may spend.

    ``turns``     — loop iterations (model call -> tool calls -> observe).
    ``attempts``  — corrective retries of one step after a failed check.
    ``variants``  — independent answers generated before one is chosen.
    ``revisions`` — accepted rewrites of the remaining plan.
    ``verify``    — ``off`` (no check), ``final`` (check the answer),
                    ``each`` (check every step).
    """

    turns: int
    attempts: int
    variants: int
    revisions: int
    verify: str


# ``norm`` mirrors today's execution exactly. ``max`` raises the effort fields but
# keeps the step and revision ceilings: those are safety limits against a runaway
# loop, and raising them raises the blast radius rather than the quality.
DEPTH_PRESETS: dict[str, DepthBudget] = {
    "min": DepthBudget(turns=1, attempts=1, variants=1, revisions=0, verify="off"),
    "norm": DepthBudget(turns=40, attempts=3, variants=1, revisions=8, verify="each"),
    "max": DepthBudget(turns=40, attempts=3, variants=3, revisions=8, verify="each"),
}

# Where each existing constant now belongs. A constant missing from this map means
# the axis is incomplete and the next refactor will forget it.
DEPTH_SOURCES: dict[str, str] = {
    "TASK_STEP_MAX_ATTEMPTS": "attempts",
    # A repair is an edit inside a generation, not a full retry, but it is still
    # an effort knob on the same step rather than on the remaining plan.
    "TASK_STEP_MAX_REPAIRS": "attempts",
    "TASK_STEP_REDO_MAX": "attempts",
    "TASK_MAX_REVISIONS": "revisions",
    "TASK_MAX_STEPS": "turns",
    "RLM_MAX_STEPS": "turns",
}
# ``MAX_TOOL_ITERATIONS`` is deliberately absent: chat is bounded by the user's
# patience, not by a background budget. Spending forty rounds on one message is a
# defect, not depth, so the chat loop keeps its own constant.


def depth_for(level: str) -> DepthBudget:
    """Return the preset for ``level``, defaulting to ``norm``.

    An unknown level must never widen the budget: a typo in an env var would
    otherwise turn into an unbounded loop.
    """
    return DEPTH_PRESETS.get(level, DEPTH_PRESETS["norm"])


def depth_defaults(level: str) -> dict[str, int | str]:
    """The config values a depth level implies.

    Used as the *default* of each config key: an explicit environment value still
    wins, so one number can be tuned without adopting a profile.
    """
    budget = depth_for(level)
    return {
        "TASK_MAX_STEPS": budget.turns,
        "TASK_STEP_MAX_ATTEMPTS": budget.attempts,
        "TASK_MAX_REVISIONS": budget.revisions,
        "DEPTH_VERIFY": budget.verify,
    }
