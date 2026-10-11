# app/tasks/runner.py
"""Executes a single direct task step and its checkpoint verifier (Tasks subsystem).

The TaskExecutor (app/tasks/executor.py, Task 7) calls ``run_direct_step`` for
each direct step (tool: llm | search | calculate) and ``run_checkpoint`` to
judge the step result against its readiness criterion.

Runner contract (Task 4 review ruling): the registry/tool runners return raw
values; ``run_direct_step`` normalizes to ``str`` at ONE boundary after tool
execution.

LLM steps stream through the same client the workers use (the reasoning model
is JIT-loaded by llama-swap) and poll ``cancel_check`` between chunks; a cancel
raises the module-level ``StepAborted`` — no partial text is ever returned.
Search/calculate are fast (<30 s): cancel is checked before and after the call
only; a race-cancel returns the completed result with status ``aborted``.

Both functions self-time with ``time.monotonic()``: ``exec_seconds`` of
``run_direct_step`` covers the tool execution only — ``run_checkpoint`` returns
its own ``exec_seconds`` and the executor adds it to the task budget itself.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable, Generator
from typing import Any

from flask import Flask

from app.model_config import get_model_config
from app.utils import begin_usage_account, finish_usage_account

logger = logging.getLogger(__name__)

# The verifier prompt keeps the step result bounded — a huge intermediate
# result would waste the reasoning window on repeated context.
CHECKPOINT_RESULT_MAX_CHARS = 8000

# Real token usage of the LAST _call_reasoning on this thread (executor
# threads run one step at a time): _run_llm_step stashes it, run_direct_step
# reads it into the returned payload (→ the step row's extra → the UI header).
_last_call_usage = threading.local()


class StepAborted(Exception):  # noqa: N818 - name fixed by the Task 6 implementer brief
    """A task step was cancelled mid-execution (a new chat request preempted it)."""


def _result_to_str(raw: Any) -> str:
    """Normalize a tool's raw value to str at a single boundary (Task 4 ruling)."""
    return raw if isinstance(raw, str) else str(raw)


def _reasoning_chat(
    app: Flask, messages: list[dict], lang: str = "ru", temperature: float | None = None
) -> Generator[str | dict[str, Any], None, None]:
    """Yield reasoning-model chunks via the same client the workers use.

    The client resolves the reasoning model config itself (JIT load through
    llama-swap) exactly as ``RedisRequestQueue`` does via ``base.llamacpp``.
    Runner-facing seam: tests patch this helper to isolate the LLM client.
    """
    base = app.modules.get("base")
    if base is None or not hasattr(base, "llamacpp"):
        raise RuntimeError("base module (llamacpp client) is unavailable")
    yield from base.llamacpp.chat_stream(messages, model_type="reasoning", lang=lang, temperature=temperature)


def _call_reasoning(
    app: Flask, prompt: str, cancel_check: Callable[[], bool], lang: str = "ru", temperature: float | None = None
) -> tuple[str, dict[str, int]]:
    """Stream one reasoning-model call for a step; abort mid-stream on cancel.

    Opens a per-call usage account (keyed by the prompt hash) so the real
    stream-usage totals (stream_options.include_usage) are attributable to
    THIS call; returns ``(text, usage)``. The executor stores usage into the
    step's extra for the chat-style header (🔢 tokens).
    """
    messages = [{"role": "user", "content": prompt}]
    account_id = f"task-call:{uuid.uuid4()}"
    begin_usage_account(account_id)
    text = ""
    for chunk in _reasoning_chat(app, messages, lang, temperature=temperature):
        if cancel_check():
            finish_usage_account(account_id)
            raise StepAborted("step cancelled during reasoning generation")
        if isinstance(chunk, dict):
            continue
        text += chunk
    if cancel_check():
        finish_usage_account(account_id)
        # Cancel arrived with the tail of the stream — discard it entirely
        # rather than return a partial result.
        raise StepAborted("step cancelled at end of reasoning generation")
    usage = finish_usage_account(account_id) or {}
    return text, {k: int(v) for k, v in usage.items() if isinstance(v, (int, float)) and v > 0}


def _is_error_text(text: str) -> bool:
    """Route through the queue's LLM-error detector (⚠️ prefix / known patterns)."""
    from app.queue import RedisRequestQueue

    return RedisRequestQueue._is_llm_error_string(text)


def _run_llm_step(
    app: Flask,
    step: dict[str, Any],
    context_digest: str,
    cancel_check: Callable[[], bool],
    lang: str,
    response_style: str,
    task_request: str,
    plan_text: str,
    user_answers: str,
) -> str:
    """Build the step prompt from the task_step template and stream the model."""
    from app.utils import format_prompt
    from modules.base import get_style_instruction, response_language_name

    params = {
        "task_request": task_request,
        "plan": plan_text,
        "step_goal": step.get("goal", ""),
        "step_tool": step.get("tool", "llm"),
        "step_checkpoint": step.get("checkpoint_criteria", ""),
        "previous_results": context_digest,
        "user_answers": user_answers,
        "response_language": response_language_name(lang),
        "response_style": get_style_instruction(lang, response_style),
    }
    prompt = format_prompt("task_step.template", params, lang=lang)
    if not prompt:
        raise RuntimeError("task_step prompt template unavailable")
    call_out = _call_reasoning(app, prompt, cancel_check, lang=lang)
    text, usage = call_out if isinstance(call_out, tuple) else (call_out, {})
    _last_call_usage.value = usage  # thread-local stash, read by run_direct_step
    return text


def _run_search_step(app: Flask, step: dict[str, Any], user_id: str, lang: str) -> str:
    """Web-search runner: query from step extra (fallback: goal), formatted context out.

    Mirrors ``RedisRequestQueue._process_search_task``: the per-user Tavily key
    is fetched by the caller (search_with_fallback degrades to SearXNG without
    one) and results go through ``format_results_context`` with the same
    context budget the search pipeline uses.
    """
    from app.tavily_keys import get_tavily_key

    search = app.modules.get("search")
    if search is None:
        raise RuntimeError("web search module is not enabled")
    step_extra = step.get("extra") or {}
    query = str(step_extra.get("query") or step.get("goal") or "")
    api_key = get_tavily_key(user_id) if user_id else None
    results, _provider = search.search_with_fallback(query, lang=lang, api_key=api_key)
    base = app.modules.get("base")
    max_chars = base.get_search_context_limit() if base and hasattr(base, "get_search_context_limit") else 10000
    return search.format_results_context(results or [], lang=lang, max_chars=max_chars)


def _run_calculate_step(step: dict[str, Any]) -> Any:
    """Calculator runner via the capabilities registry (raw value out).

    The planner should put the bare expression in the goal; when prose slips
    in («Вычислите результат выражения 2 + 2 * 2») the expression is extracted
    from the text before safe_eval — a prose goal is a SyntaxError otherwise.
    """
    from app.tasks.tools import TOOL_RUNNERS

    runner = TOOL_RUNNERS.get("calculate")
    if runner is None:
        raise RuntimeError("calculate runner is not registered")
    step_extra = step.get("extra") or {}
    expression = str(step_extra.get("expression") or step.get("goal") or "")
    try:
        return runner(expression)
    except SyntaxError:
        extracted = _extract_math_expression(expression)
        if not extracted or extracted == expression:
            raise
        return runner(extracted)


_MATH_TOKEN_RE = re.compile(r"[-+*/().,0-9\s^%√πe]+", re.UNICODE)


def _extract_math_expression(text: str) -> str:
    """Longest contiguous math-ish chunk containing at least one digit+operator.

    Used only as a fallback when safe_eval rejects the raw goal as prose.
    """
    candidates = []
    buf = ""
    for ch in text:
        if _MATH_TOKEN_RE.match(ch):
            buf += ch
        else:
            if buf.strip():
                candidates.append(buf.strip())
            buf = ""
    if buf.strip():
        candidates.append(buf.strip())
    candidates = [c for c in candidates if any(d.isdigit() for d in c)]
    if not candidates:
        return ""
    # A prose sentence leaves its trailing period inside the chunk («2 + 2 * 2.»)
    # — safe_eval reads it as a float literal (6.0). Strip trailing dots so the
    # result keeps its natural int shape (6).
    best = max(candidates, key=len)
    while best.endswith("."):
        best = best[:-1].rstrip()
    return best.replace(",", ".").strip()


def run_direct_step(
    app: Flask,
    step: dict[str, Any],
    context_digest: str,
    cancel_check: Callable[[], bool],
    user_id: str = "",
    lang: str = "ru",
    response_style: str = "neutral",
    task_request: str = "",
    plan_text: str = "",
    user_answers: str = "",
) -> dict[str, Any]:
    """Run ONE direct step (tool: llm | search | calculate) and its checkpoint.

    Returns ``{"result": str, "exec_seconds": float,
    "status": "passed"|"failed"|"aborted"|"error",
    "checkpoint": {"verdict": bool, "explanation": str, "exec_seconds": float}|None}``.

    ``exec_seconds`` covers the tool execution only — the checkpoint's own
    duration is inside ``checkpoint["exec_seconds"]`` for the executor to add.

    Raises ``StepAborted`` when the step is cancelled (pre-call check or an
    LLM cancel mid-stream) — no partial result is ever returned.
    """
    with app.app_context():
        if cancel_check():
            raise StepAborted("step cancelled before execution")
        tool = str(step.get("tool", "llm"))
        start = time.monotonic()
        try:
            if tool == "llm":
                raw = _run_llm_step(
                    app, step, context_digest, cancel_check, lang, response_style, task_request, plan_text, user_answers
                )
            elif tool == "search":
                raw = _run_search_step(app, step, user_id, lang)
            elif tool == "calculate":
                raw = _run_calculate_step(step)
            else:
                raise RuntimeError(f"unknown direct step tool: {tool}")
        except StepAborted:
            raise
        except Exception as e:
            logger.error(f"direct step {step.get('id', '?')} tool={tool} failed: {e}")
            return {
                "result": "",
                "exec_seconds": round(time.monotonic() - start, 2),
                "status": "error",
                "checkpoint": None,
            }
        exec_seconds = round(time.monotonic() - start, 2)
        result = _result_to_str(raw)
        if tool == "llm" and _is_error_text(result):
            return {"result": result, "exec_seconds": exec_seconds, "status": "error", "checkpoint": None}
        if cancel_check():
            # Race-cancel on a fast tool: the work is done, the result is kept
            # but reported aborted so the executor does not advance the plan.
            return {"result": result, "exec_seconds": exec_seconds, "status": "aborted", "checkpoint": None}
        checkpoint = run_checkpoint(app, step, result, lang=lang)
        payload = {
            "result": result,
            "exec_seconds": exec_seconds,
            "status": "passed" if checkpoint.get("verdict") else "failed",
            "checkpoint": checkpoint,
        }
        if tool == "llm":
            # Chat-style header data (model + 🔢 tokens) for the conversation view.
            config = get_model_config("reasoning") or {}
            payload["model_name"] = str(config.get("model_name") or "")
            payload["usage"] = dict(getattr(_last_call_usage, "value", {}) or {})
        return payload


def _parse_verdict(reply: str) -> dict[str, Any]:
    """Parse a verifier reply into ``{"verdict": bool, "explanation": str}``
    plus the optional "needs_user_input" verdict: ``{"question": str}``.

    Tolerates thinking-tag residue, markdown fences and any prose around the
    JSON object. Any parse failure is a failed verdict — never a crash.
    """
    from app.llamacpp_client import _strip_generic_reasoning, _strip_thinking_tags

    text = _strip_thinking_tags(_strip_generic_reasoning(reply or "")).strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {"verdict": False, "explanation": "unparseable checkpoint reply"}
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return {"verdict": False, "explanation": "unparseable checkpoint reply"}
    if not isinstance(data, dict):
        return {"verdict": False, "explanation": "unparseable checkpoint reply"}
    # Strict verdict coercion: a truthy string ("false", free text) must never
    # pass as True. Only a real bool (or the exact words true/false) counts;
    # anything else is an unparseable reply.
    raw_verdict = data.get("verdict")
    if isinstance(raw_verdict, bool):
        verdict = raw_verdict
    elif isinstance(raw_verdict, str) and raw_verdict.strip().lower() in ("true", "false"):
        verdict = raw_verdict.strip().lower() == "true"
    else:
        return {"verdict": False, "explanation": "unparseable checkpoint reply"}
    parsed = {"verdict": verdict, "explanation": str(data.get("explanation", ""))}
    # Third verdict: user data missing — the question goes to the user and the
    # step re-runs with the answer. Only honored on a failed verdict (the
    # templates emit it as verdict=false + needs_user_input=true).
    if not verdict and data.get("needs_user_input") is True:
        question = str(data.get("question") or "").strip()
        if question:
            parsed["needs_user_input"] = True
            parsed["question"] = question
    return parsed


def run_checkpoint(app: Flask, step: dict[str, Any], result: str, lang: str = "ru") -> dict[str, Any]:
    """Judge a step result against its readiness criterion (short reasoning call).

    Returns ``{"verdict": bool, "explanation": str, "exec_seconds": float}``.
    A non-verdict system failure (template/module unavailable) is reported as
    ``verdict=False`` with explanation ``"checkpoint verification failed"``.
    """
    from app.utils import format_prompt

    start = time.monotonic()
    with app.app_context():
        try:
            from modules.base import response_language_name

            base = app.modules.get("base")
            if base is None or not hasattr(base, "call_llamacpp"):
                raise RuntimeError("base module (llamacpp client) is unavailable")
            params = {
                "step_goal": step.get("goal", ""),
                "step_checkpoint": step.get("checkpoint_criteria", ""),
                "step_result": (result or "")[:CHECKPOINT_RESULT_MAX_CHARS],
                "response_language": response_language_name(lang),
            }
            prompt = format_prompt("task_checkpoint.template", params, lang=lang)
            if not prompt:
                raise RuntimeError("task_checkpoint prompt template unavailable")
            reply = base.call_llamacpp(
                [{"role": "user", "content": prompt}],
                model_type="reasoning",
                lang=lang,
                temperature=0.2,
            )
            verdict = _parse_verdict(str(reply))
        except Exception as e:
            logger.error(f"run_checkpoint failed for step {step.get('id', '?')}: {e}")
            verdict = {"verdict": False, "explanation": "checkpoint verification failed"}
        verdict["exec_seconds"] = round(time.monotonic() - start, 2)
        return verdict
