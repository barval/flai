"""One scheduled action: a short tool loop, an answer, and a run row that says so.

The run is not a task. It gets the chat tool catalogue plus ``start_task``, three rounds at
most, and a deadline checked before each round; its answer is the message the tick delivers.
When a tool ends the run — ``start_task`` — the tool's own confirmation is the answer, so the
notice the person reads is deterministic rather than a paraphrase.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

START_TASK_TOOL = "start_task"

# Raised from the executor when a tool finishes the run; caught around the loop.
class RunCompleteError(Exception):
    """The run has its answer and needs no further round."""


def _language(user_id: str | None) -> str:
    from app.scheduler import _user_language

    return _user_language(user_id)


def _is_cancelled(run_id: str) -> bool:
    """A run is cancelled when its row stopped being the one the worker is executing."""
    from app import scheduler_runs

    run = scheduler_runs.get_run(run_id)
    return run is None or run.get("status") != scheduler_runs.STATUS_RUNNING


def _ask_model(app, messages: list[dict], tools: list[dict], tool_choice: str | None, language: str):
    """One round through the multimodal role — the one whose tools these are."""
    from app.engine.loop import Turn

    try:
        answer = app.modules["base"].llamacpp.call(
            messages,
            "multimodal",
            False,
            language,
            tools=tools,
            temperature=0.2,
            tool_choice=tool_choice,
        )
    except Exception as exc:  # noqa: BLE001 - a failed round is a sentence, not a crash
        logger.exception("scheduler run: the model call failed")
        return Turn(error=f"the model could not be called ({type(exc).__name__})")

    if isinstance(answer, dict):
        return Turn(
            text=str(answer.get("content") or ""),
            tool_calls=list(answer.get("tool_calls") or []),
        )
    return Turn(text=str(answer or ""))


def run_service_action(app, run_id: str) -> dict:
    """Execute one run and store what it said. Returns a small status dict."""
    from app import scheduler_runs, scheduler_store
    from app.engine.loop import run_tool_loop
    from app.tools import execute_tool, parse_tool_arguments, scheduler_run_tool_definitions
    from app.utils import format_prompt, get_current_time_in_timezone

    run = scheduler_runs.get_run(run_id)
    if run is None:
        # The row is gone because its item was deleted (the foreign key cascades): the
        # person removed the schedule, so nothing runs and nothing is delivered.
        logger.info(f"service run {run_id} is gone, its item was deleted")
        return {"status": "cancelled"}
    item = scheduler_store.get_item(str(run.get("item_id")))
    if item is None:
        scheduler_runs.finish_run(run_id, scheduler_runs.STATUS_CANCELLED)
        return {"status": "cancelled"}

    language = _language(item.get("user_id"))
    rounds = int(app.config.get("SCHEDULER_RUN_ROUNDS", 3) or 3)
    timeout_s = int(app.config.get("SCHEDULER_RUN_TIMEOUT_S", 180) or 0)

    prompt = format_prompt(
        "scheduler_action.template",
        {
            "current_time_str": get_current_time_in_timezone(app) or "",
            "goal": str(item.get("body") or ""),
            "title": str(item.get("title") or ""),
        },
        lang=language,
    )
    if not prompt:
        scheduler_runs.finish_run(run_id, scheduler_runs.STATUS_FAILED, error="the prompt template is missing")
        return {"status": "failed"}

    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    tools = scheduler_run_tool_definitions(language)
    context = {
        "app": app,
        "user_id": item.get("user_id"),
        "lang": language,
        "response_style": "neutral",
        "message_text": str(item.get("body") or ""),
    }
    deadline = time.monotonic() + timeout_s if timeout_s > 0 else 0.0

    def generate(tool_choice: str | None):
        from app.engine.loop import Turn

        if deadline and time.monotonic() > deadline:
            return Turn(error="the run exceeded its time budget")
        return _ask_model(app, messages, tools, tool_choice, language)

    def execute(tool_calls: list[dict], assistant_text: str) -> str:
        messages.append(
            {"role": "assistant", "content": assistant_text or "", "tool_calls": tool_calls}
        )
        last = ""
        for call in tool_calls:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            arguments, _repaired = parse_tool_arguments(function.get("arguments"), None)
            result = execute_tool(name, arguments, context)
            messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": result})
            last = result
            if name == START_TASK_TOOL:
                # The task is launched; the run's answer is the tool's confirmation.
                raise RunCompleteError(result)
        return last

    answer = ""
    error = ""
    used_rounds = 0
    try:
        outcome = run_tool_loop(
            max_iterations=rounds,
            generate=generate,
            execute=execute,
            is_cancelled=lambda: _is_cancelled(run_id),
            on_final_round=lambda: None,
        )
        answer = (outcome.text or outcome.last_tool_result or "").strip()
        error = outcome.error or ""
        used_rounds = outcome.rounds
        if outcome.cancelled:
            scheduler_runs.finish_run(run_id, scheduler_runs.STATUS_CANCELLED, rounds=used_rounds)
            return {"status": "cancelled"}
    except RunCompleteError as done:
        answer = str(done).strip()
        used_rounds = 1
    except Exception as exc:  # noqa: BLE001 - every failure belongs to this run's row
        logger.exception(f"scheduler run {run_id} failed")
        error = str(exc) or type(exc).__name__

    if error and not answer:
        scheduler_runs.finish_run(
            run_id, scheduler_runs.STATUS_FAILED, error=error[:500], rounds=used_rounds
        )
        return {"status": "failed"}

    scheduler_runs.finish_run(
        run_id, scheduler_runs.STATUS_COMPLETED, result=answer, rounds=used_rounds
    )
    return {"status": "completed", "result": answer}
