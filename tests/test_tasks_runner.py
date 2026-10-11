# tests/test_tasks_runner.py
"""Unit tests for the direct step runner + checkpoint verifier (Tasks subsystem)."""

import re
from unittest.mock import MagicMock, patch

import pytest

from app.tasks.runner import StepAborted, run_checkpoint, run_direct_step


def _step(tool="llm", goal="g", criteria="c", extra=None):
    step = {"id": "s1", "kind": "direct", "tool": tool, "goal": goal, "checkpoint_criteria": criteria}
    if extra is not None:
        step["extra"] = extra
    return step


def _passed_checkpoint():
    return {"verdict": True, "explanation": "ok", "exec_seconds": 0.1}


def test_direct_llm_step_passed(test_app):
    with (
        patch("app.tasks.runner._call_reasoning", return_value="R"),
        patch("app.tasks.runner.run_checkpoint", return_value=_passed_checkpoint()),
    ):
        out = run_direct_step(test_app, _step(), context_digest="", cancel_check=lambda: False)
    assert out["result"] == "R"
    assert out["status"] == "passed"
    assert out["exec_seconds"] >= 0
    assert out["checkpoint"]["verdict"] is True


def test_direct_llm_step_failed_checkpoint(test_app):
    cp = {"verdict": False, "explanation": "missing the number", "exec_seconds": 0.1}
    with (
        patch("app.tasks.runner._call_reasoning", return_value="R"),
        patch("app.tasks.runner.run_checkpoint", return_value=cp),
    ):
        out = run_direct_step(test_app, _step(), context_digest="", cancel_check=lambda: False)
    assert out["status"] == "failed" and out["checkpoint"] == cp


def test_llm_error_string_is_error(test_app):
    err = "⚠️ GPU memory unavailable. Please try again."
    with patch("app.tasks.runner._call_reasoning", return_value=err):
        out = run_direct_step(test_app, _step(), context_digest="", cancel_check=lambda: False)
    assert out["status"] == "error" and out["checkpoint"] is None and out["result"] == err


def test_aborted_before_llm_call(test_app):
    """Pre-call cancel → StepAborted; the reasoning call is never started."""
    with patch("app.tasks.runner._call_reasoning") as mock_call, pytest.raises(StepAborted):
        run_direct_step(test_app, _step(), context_digest="", cancel_check=lambda: True)
    mock_call.assert_not_called()


def test_aborted_mid_stream(test_app):
    """Cancel during the streamed LLM call raises StepAborted — no partial result."""
    calls = {"n": 0}

    def cancel_after_first():
        calls["n"] += 1
        return calls["n"] > 1  # pre-check False, first in-stream check True

    def chunks(app, messages, lang, temperature=None):
        yield "partial "
        yield "text"

    with (
        patch("app.tasks.runner._reasoning_chat", side_effect=chunks),
        patch("app.tasks.runner.run_checkpoint") as mock_cp,
        pytest.raises(StepAborted),
    ):
        run_direct_step(test_app, _step(), context_digest="", cancel_check=cancel_after_first)
    mock_cp.assert_not_called()


def test_post_call_abort_search(test_app):
    """Fast tools finish a race-cancel (cancel appears after the call): result kept, status aborted."""
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] >= 2  # pre-check False, post-call check True

    fake_search = MagicMock()
    fake_search.search_with_fallback.return_value = ([{"title": "t", "url": "u", "content": "c"}], "tavily")
    fake_search.format_results_context.return_value = "results..."
    test_app.modules["search"] = fake_search
    with (
        patch("app.tavily_keys.get_tavily_key", return_value=None),
        patch("app.tasks.runner.run_checkpoint") as mock_cp,
    ):
        out = run_direct_step(test_app, _step(tool="search"), context_digest="", cancel_check=cancel)
    assert out["status"] == "aborted" and out["result"] == "results..." and out["checkpoint"] is None
    mock_cp.assert_not_called()


def test_calculate_step_boundary(test_app):
    """safe_eval returns raw int/float — the runner normalizes to str at one boundary."""
    with patch("app.tasks.runner.run_checkpoint", return_value=_passed_checkpoint()):
        out = run_direct_step(
            test_app, _step(tool="calculate", goal="2+2*3"), context_digest="", cancel_check=lambda: False
        )
    assert out["result"] == "8" and isinstance(out["result"], str)
    assert out["status"] == "passed"


def test_search_step(test_app):
    fake_search = MagicMock()
    fake_search.search_with_fallback.return_value = ([{"title": "t", "url": "u", "content": "c"}], "searxng")
    fake_search.format_results_context.return_value = "results..."
    test_app.modules["search"] = fake_search
    with (
        patch("app.tavily_keys.get_tavily_key", return_value=None),
        patch("app.tasks.runner.run_checkpoint", return_value=_passed_checkpoint()),
    ):
        out = run_direct_step(
            test_app, _step(tool="search", goal="rate lookup"), context_digest="", cancel_check=lambda: False
        )
    assert out["result"] == "results..." and out["status"] == "passed"
    assert fake_search.search_with_fallback.call_args.kwargs["lang"] == "ru"


def test_search_query_extra_override(test_app):
    """A query stored in step extra replaces the goal as the engine-facing query."""
    fake_search = MagicMock()
    fake_search.search_with_fallback.return_value = ([], "searxng")
    fake_search.format_results_context.return_value = ""
    test_app.modules["search"] = fake_search
    with (
        patch("app.tavily_keys.get_tavily_key", return_value=None),
        patch("app.tasks.runner.run_checkpoint", return_value=_passed_checkpoint()),
    ):
        run_direct_step(
            test_app,
            _step(tool="search", goal="goal text", extra={"query": "explicit query"}),
            context_digest="",
            cancel_check=lambda: False,
        )
    assert fake_search.search_with_fallback.call_args.args[0] == "explicit query"


def test_unknown_tool_is_error(test_app):
    out = run_direct_step(test_app, _step(tool="video_gen"), context_digest="", cancel_check=lambda: False)
    assert out["status"] == "error" and out["checkpoint"] is None


def test_step_prompt_contains_goal_and_no_placeholders(test_app):
    captured = {}

    def fake_call(app, prompt, cancel_check, lang="ru", temperature=None):
        captured["prompt"] = prompt
        return "R"

    with (
        patch("app.tasks.runner._call_reasoning", side_effect=fake_call),
        patch("app.tasks.runner.run_checkpoint", return_value=_passed_checkpoint()),
    ):
        run_direct_step(
            test_app,
            _step(goal="Проверить курс", tool="llm"),
            context_digest="Step 1: done",
            cancel_check=lambda: False,
            task_request="Узнать курс",
            plan_text="1. шаг",
            lang="ru",
        )
    prompt = captured["prompt"]
    assert "Проверить курс" in prompt
    assert "Узнать курс" in prompt
    assert "1. шаг" in prompt
    assert "Step 1: done" in prompt
    assert not re.search(r"\{[a-z_]+\}", prompt), f"unsubstituted placeholder in prompt: {prompt}"


def test_checkpoint_clean_json(test_app):
    base = test_app.modules["base"]
    with patch.object(base.llamacpp, "call", return_value='{"verdict": true, "explanation": "well done"}'):
        out = run_checkpoint(test_app, {"goal": "g", "checkpoint_criteria": "c"}, "result text", lang="ru")
    assert out["verdict"] is True and out["explanation"] == "well done"
    assert out["exec_seconds"] >= 0


def test_checkpoint_fenced_json(test_app):
    base = test_app.modules["base"]
    reply = '```json\n{"verdict": false, "explanation": "no number found"}\n```'
    with patch.object(base.llamacpp, "call", return_value=reply):
        out = run_checkpoint(test_app, {"goal": "g", "checkpoint_criteria": "c"}, "result text", lang="ru")
    assert out["verdict"] is False and out["explanation"] == "no number found"


def test_checkpoint_garbage(test_app):
    base = test_app.modules["base"]
    with patch.object(base.llamacpp, "call", return_value="Просто текст без JSON"):
        out = run_checkpoint(test_app, {"goal": "g", "checkpoint_criteria": "c"}, "result text", lang="ru")
    assert out["verdict"] is False and out["explanation"] == "unparseable checkpoint reply"


@pytest.mark.parametrize(
    "raw_verdict,expected",
    [
        ('{"verdict": "false", "explanation": "x"}', False),  # truthy string must NOT pass as True
        ('{"verdict": "результат выглядит хорошо"}', False),  # free text → unparseable
        ('{"verdict": 1}', False),  # int is not a bool verdict
        ('{"verdict": null}', False),  # missing/null → unparseable
        ('{"verdict": "true", "explanation": "ok"}', True),  # exact word "true" accepted
        ('{"verdict": "True", "explanation": "ok"}', True),  # case-insensitive word accepted
        ('{"verdict": true, "explanation": "ok"}', True),  # real bool
        ('{"verdict": false, "explanation": "mismatch"}', False),  # real bool
    ],
)
def test_checkpoint_verdict_strict_coercion(test_app, raw_verdict, expected):
    base = test_app.modules["base"]
    with patch.object(base.llamacpp, "call", return_value=raw_verdict):
        out = run_checkpoint(test_app, {"goal": "g", "checkpoint_criteria": "c"}, "result text", lang="ru")
    assert out["verdict"] is expected


def test_checkpoint_nonbool_verdict_explanation(test_app):
    """Non-bool verdicts are reported as unparseable, not silently False-with-explanation."""
    base = test_app.modules["base"]
    with patch.object(base.llamacpp, "call", return_value='{"verdict": "результат выглядит хорошо"}'):
        out = run_checkpoint(test_app, {"goal": "g", "checkpoint_criteria": "c"}, "result text", lang="ru")
    assert out["explanation"] == "unparseable checkpoint reply"
