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

    def chunks(app, messages, lang, temperature=None, status_callback=None):
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


def test_calculate_step_variable_name_readable_error(test_app):
    """A planner-drifting variable name (rate_eur) surfaces a readable error
    instead of the bare 'tool error' — the calculator only knows numbers."""
    with patch("app.tasks.runner.run_checkpoint", return_value=_passed_checkpoint()):
        out = run_direct_step(
            test_app,
            _step(tool="calculate", goal="(rate_eur + rate_usd) / 2"),
            context_digest="",
            cancel_check=lambda: False,
        )
    assert out["status"] == "error"
    assert "rate_eur" in out["result"]
    assert out["result"].startswith("calculator:")


def test_calculate_step_unknown_function_readable_error(test_app):
    """Unknown functions are surfaced the same way."""
    with patch("app.tasks.runner.run_checkpoint", return_value=_passed_checkpoint()):
        out = run_direct_step(
            test_app, _step(tool="calculate", goal="rate_eur / 2"), context_digest="", cancel_check=lambda: False
        )
    assert out["status"] == "error"
    assert "rate_eur" in out["result"] and "calculator:" in out["result"]


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


def _fake_rag(chunks, scores, available=True):
    fake = MagicMock()
    fake.available = available
    fake.search.return_value = (chunks, scores)
    return fake


def test_rag_step_formats_fragments_and_scopes_documents(test_app):
    test_app.modules["rag"] = _fake_rag(
        [
            {"filename": "contract.pdf", "text": "Работы: 100", "doc_id": "d1", "coverage": True},
            {"filename": "report.pdf", "text": "Итого: 200", "doc_id": "d2"},
        ],
        [0.91, 0.42],
    )
    with (
        patch(
            "app.db.get_user_documents",
            return_value=[{"id": "d1", "index_status": "indexed"}, {"id": "d2", "index_status": "indexed"}],
        ),
        patch("app.tasks.runner.run_checkpoint", return_value=_passed_checkpoint()),
    ):
        out = run_direct_step(
            test_app,
            _step(tool="rag", goal="найти перечень работ"),
            context_digest="",
            cancel_check=lambda: False,
            user_id="valery",
            doc_ids=["d1", "d2"],
        )
    assert out["status"] == "passed"
    assert "contract.pdf (score: 0.91)" in out["result"]
    assert "Работы: 100" in out["result"] and "Итого: 200" in out["result"]
    kwargs = test_app.modules["rag"].search.call_args.kwargs
    assert kwargs["doc_ids"] == ["d1", "d2"]
    assert kwargs["file_coverage"] is True


def test_rag_step_drops_low_scores_keeps_coverage(test_app):
    test_app.modules["rag"] = _fake_rag(
        [
            {"filename": "a.pdf", "text": "high", "doc_id": "d1"},
            {"filename": "b.pdf", "text": "low", "doc_id": "d2"},
            {"filename": "c.pdf", "text": "cov", "doc_id": "d3", "coverage": True},
        ],
        [0.8, 0.05, 0.01],
    )
    with patch("app.tasks.runner.run_checkpoint", return_value=_passed_checkpoint()):
        out = run_direct_step(
            test_app, _step(tool="rag", goal="q"), context_digest="", cancel_check=lambda: False, user_id="u"
        )
    assert "high" in out["result"] and "cov" in out["result"]
    assert "low" not in out["result"]


def test_rag_step_reports_no_fragments(test_app):
    test_app.modules["rag"] = _fake_rag([], [])
    with patch("app.tasks.runner.run_checkpoint", return_value=_passed_checkpoint()):
        out = run_direct_step(
            test_app, _step(tool="rag", goal="найти акт"), context_digest="", cancel_check=lambda: False, user_id="u"
        )
    assert out["result"] == "Релевантные документы не найдены для запроса: найти акт"


def test_rag_step_unavailable_module_is_error(test_app):
    test_app.modules["rag"] = _fake_rag([], [], available=False)
    out = run_direct_step(
        test_app, _step(tool="rag", goal="q"), context_digest="", cancel_check=lambda: False, user_id="u"
    )
    assert out["status"] == "error" and out["checkpoint"] is None


def test_rag_step_waits_for_attached_documents_to_be_indexed(test_app):
    """A rag step over the task's OWN attachments waits for them to be indexed.

    Documents attached with the paperclip are uploaded at submit time, so the
    search must not run against a half-indexed document (chat parity: a chat
    answer over an attached document is produced only after its indexing).
    """
    polls = []

    def fake_docs(user_id):
        polls.append(user_id)
        # First two polls: the attachment is still indexing; then it settles.
        status = "indexing" if len(polls) < 3 else "indexed"
        return [{"id": "d1", "index_status": status}]

    test_app.modules["rag"] = _fake_rag([{"filename": "c.pdf", "text": "T", "doc_id": "d1"}], [0.9])
    with (
        patch("app.db.get_user_documents", side_effect=fake_docs),
        patch("time.sleep"),
        patch("app.tasks.runner.run_checkpoint", return_value=_passed_checkpoint()),
    ):
        out = run_direct_step(
            test_app,
            _step(tool="rag", goal="q"),
            context_digest="",
            cancel_check=lambda: False,
            user_id="u",
            doc_ids=["d1"],
        )
    assert len(polls) == 3, "the wait re-polls until the document leaves the indexing state"
    assert out["status"] == "passed" and "T" in out["result"]


def test_rag_step_wait_is_interrupted_by_a_chat_request(test_app):
    """The indexing wait is abortable: a chat request kills the step.

    The pre-call check passes and the cancel arrives only inside the wait (the
    exception message proves which check fired).
    """
    test_app.modules["rag"] = _fake_rag([{"filename": "c.pdf", "text": "T", "doc_id": "d1"}], [0.9])
    checks = {"n": 0}

    def cancel_check():
        checks["n"] += 1
        return checks["n"] > 1

    with (
        patch("app.db.get_user_documents", return_value=[{"id": "d1", "index_status": "indexing"}]),
        patch("time.sleep"),
        pytest.raises(StepAborted, match="waiting for document indexing"),
    ):
        run_direct_step(
            test_app,
            _step(tool="rag", goal="q"),
            context_digest="",
            cancel_check=cancel_check,
            user_id="u",
            doc_ids=["d1"],
        )


def test_rag_step_proceeds_when_the_index_status_check_fails(test_app):
    """A failing status check never fails the step — the search runs as before."""
    test_app.modules["rag"] = _fake_rag([{"filename": "c.pdf", "text": "T", "doc_id": "d1"}], [0.9])
    with (
        patch("app.db.get_user_documents", side_effect=RuntimeError("db down")),
        patch("app.tasks.runner.run_checkpoint", return_value=_passed_checkpoint()),
    ):
        out = run_direct_step(
            test_app,
            _step(tool="rag", goal="q"),
            context_digest="",
            cancel_check=lambda: False,
            user_id="u",
            doc_ids=["d1"],
        )
    assert out["status"] == "passed" and "T" in out["result"]


def test_rag_step_without_attachments_does_not_touch_documents(test_app):
    """An unscoped search (no doc_ids) never consults the document table."""
    test_app.modules["rag"] = _fake_rag([{"filename": "c.pdf", "text": "T", "doc_id": "d1"}], [0.9])
    with (
        patch("app.db.get_user_documents") as docs,
        patch("app.tasks.runner.run_checkpoint", return_value=_passed_checkpoint()),
    ):
        out = run_direct_step(
            test_app, _step(tool="rag", goal="q"), context_digest="", cancel_check=lambda: False, user_id="u"
        )
    assert out["status"] == "passed"
    docs.assert_not_called()


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


def test_checkpoint_reply_with_marker_like_phrases_still_parses(test_app):
    """A verdict whose JSON carries reasoning-marker phrases is not truncated.

    Regression (2026-10-16): ``_strip_generic_reasoning()`` cut a valid JSON
    reply at its last "Analyze …"/"Review …" marker, so a legitimate verdict
    came back as "unparseable checkpoint reply" and the step was retried.
    """
    base = test_app.modules["base"]
    reply = (
        '{"verdict": true, "explanation": "Analyze attached images: all four show the same chart. '
        'Review the columns — the values match across all four files."}'
    )
    with patch.object(base.llamacpp, "call", return_value=reply):
        out = run_checkpoint(test_app, {"goal": "g", "checkpoint_criteria": "c"}, "result text", lang="ru")
    assert out["verdict"] is True, "a marker phrase inside the JSON must not break the verdict"
    assert out["explanation"].startswith("Analyze attached images")


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


def test_checkpoint_reports_verifier_model_and_usage(test_app):
    """The verifier's model + real token usage ride back with the verdict — a
    tool-produced step (calculate/search) shows them in its message header."""
    base = test_app.modules["base"]

    def _fake_call(*args, **kwargs):
        from app.utils import record_usage_for_current

        record_usage_for_current(150, 8)
        return '{"verdict": true, "explanation": "ok"}'

    with (
        patch.object(base.llamacpp, "call", side_effect=_fake_call),
        patch("app.tasks.runner.get_model_config", return_value={"model_name": "Test-Verifier.gguf"}),
    ):
        out = run_checkpoint(test_app, {"goal": "g", "checkpoint_criteria": "c"}, "result text", lang="ru")
    assert out["usage"] == {"prompt_tokens": 150, "completion_tokens": 8}
    assert out["model_name"] == "Test-Verifier.gguf"


def test_tool_step_header_uses_verifier_meta(test_app):
    """A tool step carries no model call of its own — the header reports the
    checkpoint verifier's model and tokens instead."""
    cp = {
        "verdict": True,
        "explanation": "ok",
        "exec_seconds": 0.1,
        "model_name": "Test-Verifier.gguf",
        "usage": {"prompt_tokens": 40, "completion_tokens": 5},
    }
    with patch("app.tasks.runner.run_checkpoint", return_value=cp):
        out = run_direct_step(
            test_app, _step(tool="calculate", goal="2 + 2"), context_digest="", cancel_check=lambda: False
        )
    assert out["result"] == "4" and out["status"] == "passed"
    assert out["model_name"] == "Test-Verifier.gguf"
    assert out["usage"] == {"prompt_tokens": 40, "completion_tokens": 5}


def test_calculate_prose_goal_extracts_expression(test_app):
    """A prose goal («Вычислите результат выражения 2 + 2 * 2.») does not kill
    the step: the bare expression is extracted from the text and evaluated."""
    with patch("app.tasks.runner.run_checkpoint", return_value=_passed_checkpoint()):
        out = run_direct_step(
            test_app,
            _step(tool="calculate", goal="Вычислите результат арифметического выражения 2 + 2 * 2."),
            context_digest="",
            cancel_check=lambda: False,
        )
    assert out["result"] == "6" and out["status"] == "passed"


def test_calculate_prose_no_numbers_still_errors(test_app):
    """A prose goal without any math-looking chunk keeps the error path
    (status=error, the step fails honestly instead of guessing)."""
    with patch("app.tasks.runner.run_checkpoint", return_value=_passed_checkpoint()):
        out = run_direct_step(
            test_app,
            _step(tool="calculate", goal="Сложите два числа."),
            context_digest="",
            cancel_check=lambda: False,
        )
    assert out["status"] == "error"
