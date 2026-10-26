# tests/test_tasks_verdict_language.py
"""A verifier reason must never reach a Russian profile in English.

The model occasionally ignores the ``response_language`` instruction, and the
verdict explanation is stored verbatim and printed in the task view.
"""

import pytest

from app.tasks.runner import run_checkpoint

pytestmark = pytest.mark.requires_db

STEP = {"id": "step-1", "goal": "Раздели документ на сцены", "checkpoint_criteria": "Список сцен"}
ENGLISH_VERDICT = '{"verdict": false, "explanation": "The output contains only three numbered items and no scenes."}'
RUSSIAN_VERDICT = '{"verdict": false, "explanation": "В результате только три пункта и нет сцен."}'


class _FakeBase:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def call_llamacpp(self, messages, **kwargs):
        self.calls.append(messages)
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]


def _run(test_app, replies):
    base = _FakeBase(replies)
    test_app.modules["base"] = base
    verdict = run_checkpoint(test_app, STEP, "результат", lang="ru")
    return verdict, base


def test_russian_verdict_makes_one_call(test_app):
    verdict, base = _run(test_app, [RUSSIAN_VERDICT])

    assert verdict["explanation"] == "В результате только три пункта и нет сцен."
    assert len(base.calls) == 1


def test_english_verdict_is_retried_once_and_the_russian_reason_kept(test_app):
    verdict, base = _run(test_app, [ENGLISH_VERDICT, RUSSIAN_VERDICT])

    assert len(base.calls) == 2
    assert verdict["explanation"] == "В результате только три пункта и нет сцен."
    assert verdict["verdict"] is False, "the retry fixes the wording, not the verdict"


def test_english_after_the_retry_falls_back_to_a_localized_reason(test_app):
    verdict, base = _run(test_app, [ENGLISH_VERDICT, ENGLISH_VERDICT])

    assert len(base.calls) == 2
    assert verdict["explanation"].strip()
    assert "verifier" not in verdict["explanation"].lower()
