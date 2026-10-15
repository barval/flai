# app/tasks/executor.py
"""TaskExecutor — one global daemon thread owning the Tasks lifecycle (v12.6).

Per cycle (spec §2/§3): select task → gate check → run steps → handle aborts
/ clarifications / budget → terminal states. Selection order: (1) the task
with the newest unprocessed ``user_input`` answer (resume-preempted — the
previously running task returns to queued with its in-flight step aborted as
``preempted``), (2) the task currently in progress (active/frozen/awaiting),
(3) queued tasks by ``task_class`` asc, then ``created_at`` asc.

Store/gate/runner collaborators are imported into this module's namespace so
tests can patch them at a single seam (see tests/test_tasks_executor.py);
the SQL helpers the store module does not expose yet live here as public
functions.

The executor does NOT hold ``_gpu_lock`` for whole steps ("chat never waits"):
VRAM is handled by the llama.cpp client's internal ``ensure_vram_for`` path
(same as queue.py workers), and chat/step GPU contention is resolved by the
mid-step abort mechanism (chat activity raises ``task:step_cancel`` flags;
see ``_mark_chat_activity`` in ``app/queue.py``).

Mid-step abort (chat preemption): while a direct step runs, the executor
publishes ``task:running_step:<task_id>`` = step_id and adds the task id to
the Redis SET ``task:active_running_steps``; both are cleared when the step
ends or the task reaches a terminal state. The chat-activity hook in
``app/queue.py:_mark_chat_activity`` turns each of those pointers into a
``task:step_cancel:<step_id>`` flag (TTL 3600 s), which the runner polls
between stream chunks and raises as ``StepAborted``. The executor deletes
the flag after processing it (consumer-clears rule) and before every re-run.

Server restart: on executor start every step row left in status 'running'
by a dead process is marked ``interrupted``/``restart`` (fresh attempt on
resume) and the pointer SET is rebuilt from scratch.

Known limitation (Task 8 completes it): ``subtask`` steps (rlm / image_gen /
image_edit / video_gen) are enqueued through the existing request queue with
the ``task_step_subtask`` tag and marked ``subtask_queued``; the step loop
then breaks for the cycle. Completion polling, deferred-subtask handling and
the slow-worker gate guard land with Task 8.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import re
import threading
import time
from datetime import UTC, datetime
from typing import Any

from flask import Flask

from app.database import get_db
from app.model_config import get_model_config
from app.tasks.audio_transcribe import ABORT_REASON as ATTACHMENT_TRANSCRIPT_REASON
from app.tasks.gate import is_gate_open
from app.tasks.runner import (
    StepAborted,
    recall_slm_facts,
    run_checkpoint,
    run_direct_step,
    wait_for_documents_indexed,
)
from app.tasks.store import add_step, get_step, get_task, update_step, update_task
from app.utils import get_current_time_in_timezone

logger = logging.getLogger(__name__)

# ── Config fallbacks (real values are read from app.config at runtime) ──
TASK_POLL_INTERVAL_S = 1.0
TASK_TOTAL_BUDGET_S = 86400
TASK_STEP_MAX_ATTEMPTS = 3
TASK_QUIET_PERIOD_S = 15

# Test-visible seed for an expired budget (tests/test_tasks_executor.py).
_BUDGET_CFG_FALLBACK = {"TASK_TOTAL_BUDGET_S": TASK_TOTAL_BUDGET_S}

CANCEL_KEY_PREFIX = "task:step_cancel:"
RUNNING_STEP_KEY_PREFIX = "task:running_step:"
RUNNING_STEPS_SET = "task:active_running_steps"
DEFERRED_SUBTASKS_KEY = "task:deferred_subtasks"
# Safety TTL on step-cancel flags so a lost consumer cannot leak a flag.
CANCEL_FLAG_TTL_S = 3600

# Direct tools run in-process (runner); everything else goes through the queue.
_SUBTASK_TOOLS = {"rlm", "image_gen", "image_edit", "video_gen", "camera", "crawl", "vision"}
# Queue task_type per subtask tool (Task 8 refines the exact payloads).
_SUBTASK_TYPE = {
    "image_gen": "image_gen",
    "image_edit": "api_image_edit",
    "video_gen": "video",
    "rlm": "rlm_analysis",
    "camera": "camera_task",
    "crawl": "crawl_task",
    "vision": "vision_task",
}

# Localized user-facing fallbacks (⚠️ prefix per the project error rule).
_MSG_PLAN_TEMPLATE_UNAVAILABLE = "Task could not be planned: the plan template is unavailable."
_MSG_PLAN_UNREADABLE = "Task could not be planned: the planner returned an unreadable plan. Please try again."
# Real token usage of the last planner call (module-level: the single
# executor thread is the only planner caller; the plan phase reads it for
# the plan message's chat-style header).
_LAST_PLANNER_USAGE: dict[str, int] = {}
_FALLBACK_NO_FINAL = "The final answer could not be generated; please check the step results above."
_FALLBACK_BUDGET_NO_SUMMARY = "Execution budget exhausted; the summary could not be generated."


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def audio_context_block(task: dict) -> str:
    """Request-phase audio transcripts of a task as a prompt block.

    Chat parity (v12.6): a voice message or an audio file attached to the task
    request is transcribed into text that the planner and every step read as
    the user's own words — the transcript is CONTEXT (like an image
    description), never a Documents-panel document. Answer-phase transcripts
    belong to their answer step and are appended there by the transcription
    thread (``app/tasks/audio_transcribe.py``), so they are skipped here.
    """
    parts = []
    for entry in task.get("audio") or []:
        if not isinstance(entry, dict) or str(entry.get("phase") or "") == "answer":
            continue
        text = str(entry.get("transcript") or "").strip()
        if not text:
            continue
        parts.append(f"[Audio transcript ({entry.get('file_name') or ''})]\n{text}")
    return "\n\n".join(parts)


def _request_text(task: dict) -> str:
    """The user's original request plus any transcribed audio (single source
    for the planner, every step prompt and the final summary)."""
    plan = task.get("step_plan") or {}
    if isinstance(plan, dict):
        base = str(plan.get("task_request") or task.get("title") or "")
    else:
        base = str(task.get("title") or "")
    block = audio_context_block(task)
    return f"{base}\n\n{block}" if block else base


_URL_RE = re.compile(r"https?://\S+")


def _first_url(text: str) -> str:
    """First http(s) URL in free text, or "" (same shape as queue's extractor)."""
    match = _URL_RE.search(text or "")
    return match.group(0).rstrip(".,;:!?»)]}") if match else ""


def _strip_data_url(value: str) -> str:
    """Raw base64 payload of a ``data:`` URL (the edit pipeline wants the bytes
    only); a plain base64 string is returned unchanged."""
    return value.split(",", 1)[1] if value.startswith("data:") and "," in value else value


# ── Executor-owned SQL (the store module does not expose these yet) ─────


def get_task_steps(task_id: str) -> list[dict]:
    """All step rows of a task, ordered by seq."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM task_steps WHERE task_id = %s ORDER BY seq ASC", (task_id,))
        return [dict(r) for r in c.fetchall()]


def get_queued_task() -> dict | None:
    """Next queued task: lowest task_class first, then FIFO by created_at."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM tasks WHERE status = 'queued' ORDER BY task_class ASC, created_at ASC LIMIT 1")
        row = c.fetchone()
        return dict(row) if row else None


def get_queued_task_positions() -> dict[str, int]:
    """1-based queue position per queued task, in ``get_queued_task()``'s order.

    The Tasks list shows the number the executor will honour (the queue is
    global, not per user) as an hourglass badge on every ``queued`` row.
    """
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT id, ROW_NUMBER() OVER (ORDER BY task_class ASC, created_at ASC) AS pos
            FROM tasks WHERE status = 'queued'
            """
        )
        return {str(r["id"]): int(r["pos"]) for r in c.fetchall()}


def get_in_progress_tasks() -> list[dict]:
    """Tasks currently holding or having released the slot (active/frozen/awaiting)."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM tasks WHERE status IN ('active','frozen','awaiting_user') ORDER BY created_at ASC")
        return [dict(r) for r in c.fetchall()]


def get_tasks_by_status(status: str) -> list[dict]:
    """All tasks in one status, oldest first."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM tasks WHERE status = %s ORDER BY created_at ASC", (status,))
        return [dict(r) for r in c.fetchall()]


def get_running_step(task_id: str) -> dict | None:
    """The in-flight (running) step row of a task, if any."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT * FROM task_steps WHERE task_id = %s AND status = 'running' ORDER BY seq DESC LIMIT 1",
            (task_id,),
        )
        row = c.fetchone()
        return dict(row) if row else None


def get_all_running_steps() -> list[dict]:
    """Every step row stuck in status 'running' (restart-marker sweep)."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM task_steps WHERE status = 'running' ORDER BY seq ASC")
        return [dict(r) for r in c.fetchall()]


def get_unprocessed_user_input() -> dict | None:
    """Newest unprocessed user_input step on an awaiting_user task (resume seam)."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT ui.* FROM task_steps ui "
            "JOIN tasks t ON t.id = ui.task_id "
            "WHERE ui.kind = 'user_input' AND ui.status = 'pending' "
            "AND t.status = 'awaiting_user' "
            "ORDER BY ui.seq DESC LIMIT 1"
        )
        row = c.fetchone()
        return dict(row) if row else None


def append_deep_analysis_step(task_id: str, lang: str = "ru") -> None:
    """Append a 🔬 deep-analysis (``rlm``) step to a plan that lacks one (v12.6).

    The planner only sees the toggle at plan time; when the user switches the
    toggle ON while answering a clarifying question, the plan already exists
    and the planner cannot amend it — this appends an ``rlm`` step whose
    question is the task request, so the attached documents receive the deep
    analysis the toggle promises (idempotent: a plan that already contains an
    ``rlm`` step is left untouched).
    """
    task = get_task(task_id)
    if not task:
        return
    plan = task.get("step_plan") or {}
    steps = list(plan.get("steps") or []) if isinstance(plan, dict) else list(plan or [])
    if any(isinstance(s, dict) and str(s.get("tool") or "") == "rlm" for s in steps):
        return
    from flask_babel import force_locale
    from flask_babel import gettext as _

    with force_locale(lang):
        criteria = _("the analysis contains the concrete data from the documents that answers the request")
    goal = (
        str(plan.get("task_request") or task.get("title") or "")
        if isinstance(plan, dict)
        else str(task.get("title") or "")
    )
    seq = len(steps) + 1
    steps.append(
        {
            "seq": seq,
            "goal": goal,
            "tool": "rlm",
            "checkpoint_criteria": criteria,
            "user_input_expected": False,
        }
    )
    merged_plan = {**plan, "steps": steps} if isinstance(plan, dict) else {"steps": steps}
    update_task(task_id, step_plan=merged_plan)
    logger.info(f"task {task_id}: appended rlm step (seq {seq}) — deep-analysis toggle ON at answer time")


def add_user_input_step(task_id: str, question_seq: int, answer: str) -> str:
    """API seam: store the user's answer to the question step at ``question_seq``.

    The answer becomes a ``user_input`` step linked to the question row via
    ``parent_step_id``; it stays ``pending`` until the executor consumes it.
    """
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COALESCE(MAX(seq), 0) AS mx FROM task_steps WHERE task_id = %s", (task_id,))
        max_seq = int(c.fetchone()["mx"] or 0)
        c.execute(
            "SELECT id FROM task_steps WHERE task_id = %s AND seq = %s LIMIT 1",
            (task_id, question_seq),
        )
        qrow = c.fetchone()
    parent_id = qrow["id"] if qrow else None
    step_id = add_step(task_id=task_id, seq=max_seq + 1, kind="user_input", parent_step_id=parent_id)
    update_step(step_id, result=answer, started_at=_now_iso())
    return step_id


def add_final_step(task_id: str, result_text: str, status: str = "passed", meta: dict[str, Any] | None = None) -> str:
    """Append the closing ``final`` step row (answer, error note or summary).

    Idempotent: a task may carry at most one ``final`` step — when one already
    exists it is updated in place instead of being duplicated. ``meta`` (model
    name + real token usage) goes into the row's extra for the chat-style
    header.
    """
    extra = dict(meta or {})
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COALESCE(MAX(seq), 0) AS mx FROM task_steps WHERE task_id = %s", (task_id,))
        max_seq = int(c.fetchone()["mx"] or 0)
        c.execute(
            "SELECT id FROM task_steps WHERE task_id = %s AND kind = 'final' ORDER BY seq ASC LIMIT 1",
            (task_id,),
        )
        existing = c.fetchone()
    if existing:
        update_step(
            existing["id"],
            result=result_text,
            status=status,
            finished_at=_now_iso(),
            extra=extra,
            exec_seconds=float(extra.get("exec_seconds") or 0.0),
        )
        return existing["id"]
    step_id = add_step(task_id=task_id, seq=max_seq + 1, kind="final")
    update_step(
        step_id,
        result=result_text,
        status=status,
        finished_at=_now_iso(),
        extra=extra,
        exec_seconds=float(extra.get("exec_seconds") or 0.0),
    )
    return step_id


# ── Runtime-support helpers ─────────────────────────────────────────────


def _get_request_queue():
    """The running app's RedisRequestQueue.

    ``current_app`` inside request contexts; the executor singleton's app in
    the executor thread (which runs without an app context).
    """
    try:
        from flask import current_app

        return current_app.request_queue
    except RuntimeError:
        with _executor_lock:
            executor = _EXECUTOR
        app = executor.app if executor is not None else None
        if app is None:
            raise
        return app.request_queue


def _gpu_busy() -> bool:
    """ResourceManager.is_gpu_busy(); failure counts as busy (never run over chat)."""
    try:
        from app.resource_manager import get_resource_manager

        return bool(get_resource_manager().is_gpu_busy())
    except Exception:
        logger.exception("is_gpu_busy check failed — treating the GPU as busy")
        return True


def _queues_empty() -> bool:
    """All four request queues empty (fast / slow / background / crawl)."""
    q = _get_request_queue()
    return all(
        q.redis.llen(key) == 0 for key in (q.queue_key, q.slow_queue_key, q.background_queue_key, q.crawl_queue_key)
    )


def _capabilities_section(camera_rooms: list[str] | None = None, has_images: bool = False) -> str:
    """Render the capabilities registry for the planner prompt.

    ``camera_rooms`` (the user's accessible camera rooms, when known) appends
    the exact room names so the planner can emit one camera step per room —
    answering "which room…" questions needs the concrete room list. ``vision``
    is advertised ONLY when images are attached to the task, so the planner
    never invents an image-analysis step without attachments.
    """
    from app.tasks.tools import get_capabilities

    lines = []
    for cap in get_capabilities():
        if cap["id"] == "vision" and not has_images:
            continue
        lines.append(f"- {cap['id']} ({cap['label']}): {cap['description']} Checkpoint hint: {cap['checkpoint_hint']}")
        if cap["id"] == "camera" and camera_rooms:
            lines.append(f"  Available rooms: {', '.join(camera_rooms)}")
    return "\n".join(lines)


def _response_language_name(lang: str) -> str:
    try:
        from modules.base import response_language_name

        return response_language_name(lang)
    except Exception:
        return "Russian" if lang == "ru" else "English"


def _attachments_section(task: dict, lang: str) -> str:
    """Localized list of the task's attachments for the planner prompt.

    The planner plans over WHAT EXISTS: 4 attached images must produce a
    vision/llm plan, never a «Прикрепите изображения…» question (live defect —
    the prompt carried only a boolean, so the planner asked for attachments it
    already had). Documents and audio ride their existing capability rules;
    this section only names the files.
    """
    from flask_babel import force_locale
    from flask_babel import gettext as _

    with force_locale(lang):
        lines: list[str] = []
        image_names = [
            str(i.get("file_name") or "")
            for i in (task.get("images") or [])
            if isinstance(i, dict) and i.get("file_name")
        ]
        image_names = [n for n in image_names if n]
        if image_names:
            lines.append(
                _("Attached images: {count} ({names}).").format(count=len(image_names), names=", ".join(image_names))
            )
        doc_ids = [str(d) for d in (task.get("doc_ids") or []) if d]
        if doc_ids:
            try:
                from app.db import get_user_documents

                by_id = {
                    str(d.get("id")): str(d.get("filename") or "")
                    for d in (get_user_documents(task.get("user_id") or "") or [])
                }
                doc_names = [by_id[d] for d in doc_ids if by_id.get(d)]
            except Exception as e:
                logger.warning(f"attachment doc names not resolved for task {task.get('id', '?')}: {e}")
                doc_names = []
            if doc_names:
                lines.append(
                    _("Attached documents: {count} ({names}).").format(count=len(doc_names), names=", ".join(doc_names))
                )
        audio_entries = [e for e in (task.get("audio") or []) if isinstance(e, dict) and e.get("file_name")]
        if audio_entries:
            audio_names = [str(e["file_name"]) for e in audio_entries]
            lines.append(
                _("Attached audio recordings: {count} ({names}).").format(
                    count=len(audio_names), names=", ".join(audio_names)
                )
            )
        if not lines:
            return _("No files are attached to the task.")
    return "\n".join(lines)


def _style_instruction(lang: str, style: str) -> str:
    try:
        from modules.base import get_style_instruction

        return get_style_instruction(lang, style)
    except Exception:
        return ""


def _plan_text(step_plan: Any) -> str:
    """Human-readable plan rendering for prompts and digests."""
    steps = step_plan.get("steps", []) if isinstance(step_plan, dict) else (step_plan or [])
    lines = []
    for i, s in enumerate(steps or []):
        if not isinstance(s, dict):
            continue
        marker = " (needs user input)" if s.get("user_input_expected") else ""
        lines.append(f"{i + 1}. [{s.get('tool', 'llm')}] {s.get('goal', '')}{marker}")
    return "\n".join(lines)


def _results_digest(task_id: str, char_cap: int = 20000) -> str:
    """All step results (the final-answer / summary corpus)."""
    parts = []
    for r in get_task_steps(task_id):
        if r["kind"] in ("question", "user_input", "final"):
            continue
        if r["status"] in ("passed", "failed", "interrupted", "subtask_queued"):
            label = f"Step {r['seq']} [{r['tool'] or r['kind']}] {r['goal']} → {r['status']}"
            body = (r["result"] or "")[:3000]
            parts.append(f"{label}: {body}" if body else label)
    return "\n".join(parts)[:char_cap]


def _parse_plan(raw: str) -> dict | None:
    """Fence-tolerant parse of the planner reply → {"title", "steps": [...]}.

    Returns None when the reply carries no usable plan (caller retries once,
    then fails the task). Mirrors the tolerance of runner._parse_verdict.
    """
    from app.llamacpp_client import _strip_generic_reasoning, _strip_thinking_tags

    text = _strip_thinking_tags(_strip_generic_reasoning(raw or "")).strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return None
    steps = []
    for i, s in enumerate(raw_steps):
        if not isinstance(s, dict):
            continue
        steps.append(
            {
                "seq": i + 1,
                "goal": str(s.get("goal", "")),
                "tool": str(s.get("tool", "llm")),
                "checkpoint_criteria": str(s.get("checkpoint_criteria", "")),
                "user_input_expected": bool(s.get("user_input_expected", False)),
            }
        )
    if not steps:
        return None
    return {"title": str(data.get("title", "")), "steps": steps}


def _last_meaningful_step(task_id: str) -> dict | None:
    """The last non-trivial step row (for the final-summary dedup)."""
    rows = [r for r in get_task_steps(task_id) if r.get("kind") in ("direct", "subtask")]
    return rows[-1] if rows else None


def _results_match(final_text: str, step_result: str) -> bool:
    """True when the final summary duplicates the last step's result.

    One-step plans make the final summary a re-render of the same answer:
    strip reasoning residue and compare normalized prefixes (a full equality
    never holds — the final LLM rewords slightly). The threshold is generous
    (0.9 of the shorter head) to catch real duplicates only.
    """
    import difflib

    def norm(s: str) -> str:
        s = re.sub(r"<think>[\s\S]*?</think>", "", s or "")
        s = re.sub(r"\s+", " ", s).strip().lower()
        return s

    a, b = norm(final_text), norm(step_result)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter = a if len(a) <= len(b) else b
    longer = b if shorter is a else a
    if len(shorter) < 200:  # too short to judge reliably — only exact match counts
        return False
    ratio = difflib.SequenceMatcher(None, shorter[:2000], longer[: len(shorter) + 200]).ratio()
    return ratio >= 0.9


def _run_planner(app: Flask, prompt: str, cancel_check, lang: str, status_callback=None) -> str:
    """One reasoning-model call for the plan phase (text only).

    Uses the runner's private streaming helper on purpose (the brief allows
    importing it): the reasoning model is JIT-loaded exactly as the workers
    do, and tests patch this wrapper to isolate the LLM. ``status_callback``
    (optional) fires with "generating" once the model is loaded — the plan
    phase turns it into the live stage event. The call's real token usage is
    remembered module-level (``_LAST_PLANNER_USAGE``) — the executor thread
    is the only planner caller, so the plan phase reads it for the header.
    """
    global _LAST_PLANNER_USAGE
    text, usage = _run_planner_full(app, prompt, cancel_check, lang, temperature=0.2, status_callback=status_callback)
    _LAST_PLANNER_USAGE = usage or {}
    return text


def _run_planner_full(
    app: Flask, prompt: str, cancel_check, lang: str, temperature: float | None = None, status_callback=None
) -> tuple[str, dict[str, int]]:
    """Same call, also returning the real token usage (final-summary header)."""
    from app.tasks import runner

    return runner._call_reasoning(
        app, prompt, cancel_check, lang=lang, temperature=temperature, status_callback=status_callback
    )


def _planner_usage() -> dict[str, int]:
    """Real token usage of the LAST planner call (see ``_run_planner``).

    ``_run_planner`` (the seam tests patch) returns text only; it stores the
    closed call's totals in ``_LAST_PLANNER_USAGE`` for the plan-phase header.
    """
    totals = _LAST_PLANNER_USAGE or {}
    return {k: int(v) for k, v in totals.items() if isinstance(v, (int, float)) and v > 0}


def _run_final_summary(app: Flask, task: dict, meta_out: dict[str, Any] | None = None) -> str | None:
    """Reasoning call over the accumulated results (task_final.template).

    Returns the cleaned final text, or None when generation fails (the caller
    then records a non-LLM fallback note). ``meta_out`` (optional dict) is
    filled with the model name + real token usage for the final step's
    chat-style header — a separate argument keeps the mocked str contract
    of the tests intact.
    """
    from app.utils import format_prompt

    lang = task.get("lang") or "ru"
    plan = task.get("step_plan") or {}
    request_text = _request_text(task)
    params = {
        "task_request": request_text,
        "plan": _plan_text(plan),
        "results_digest": _results_digest(task["id"]),
        "slm_facts": recall_slm_facts(
            app,
            request_text,
            task.get("user_id") or "",
            lang,
        ),
        "response_language": _response_language_name(lang),
        "response_style": _style_instruction(lang, task.get("response_style") or "neutral"),
        "current_time": get_current_time_in_timezone(app) or "",
    }
    meta: dict[str, Any] = meta_out if meta_out is not None else {}
    try:
        with app.app_context():
            prompt = format_prompt("task_final.template", params, lang=lang)
        if not prompt:
            raise RuntimeError("task_final prompt template unavailable")
        start = time.monotonic()
        raw, usage = _run_planner_full(app, prompt, lambda: False, lang)
        meta["exec_seconds"] = round(time.monotonic() - start, 2)
        config = get_model_config("reasoning") or {}
        meta["model_name"] = str(config.get("model_name") or "")
        meta["usage"] = {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
        }
    except Exception as e:
        logger.error(f"final summary generation failed for task {task.get('id', '?')}: {e}")
        return None
    from app.llamacpp_client import _strip_generic_reasoning, _strip_thinking_tags

    return _strip_thinking_tags(_strip_generic_reasoning(raw or "")).strip() or None


# ── The executor ────────────────────────────────────────────────────────


class TaskExecutor:
    """One global daemon thread: select → gate → run steps → terminal states."""

    def __init__(self, app: Flask | None = None, redis_client=None):
        self.app = app
        self._redis = redis_client
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None
        # Step owned by THIS process right now — the gate-closed freeze path
        # only interrupts a running row it owns itself (restart orphans are
        # marked with interrupt_reason='restart' at executor start instead).
        self._current_step_id: str | None = None
        self._restart_sweep_done = False

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the daemon loop (idempotent)."""
        if self._thread and self._thread.is_alive():
            return
        self._shutdown.clear()
        self._thread = threading.Thread(target=self._loop, name="tasks-executor", daemon=True)
        self._thread.start()
        logger.info("TaskExecutor thread started")

    def stop(self, timeout: float | None = None) -> None:
        """Signal the loop to stop and join the thread (idempotent)."""
        self._shutdown.set()
        thread = self._thread
        if thread and thread.is_alive():
            if timeout is None:
                timeout = max(0.5, float(self._cfg("TASK_POLL_INTERVAL_S", TASK_POLL_INTERVAL_S)) + 1.0)
            thread.join(timeout=timeout)
        self._thread = None

    def _loop(self) -> None:
        """Defensive daemon loop: every cycle in try/except — a crash must not
        kill the thread."""
        poll = max(0.1, float(self._cfg("TASK_POLL_INTERVAL_S", TASK_POLL_INTERVAL_S)))
        while not self._shutdown.is_set():
            try:
                if not self._restart_sweep_done:
                    self._mark_interrupted_by_restart()
                    self._restart_sweep_done = True
                self._process_one_cycle()
            except Exception as exc:  # noqa: BLE001 — deliberate catch-all
                logger.exception(f"TaskExecutor cycle failed: {exc}")
            self._shutdown.wait(poll)

    def _mark_interrupted_by_restart(self) -> None:
        """Server restart: no step is owned by this fresh process, so every row
        left in status 'running' by a dead process gets a restart marker (its
        task re-runs the step from scratch on resume, spec §2)."""
        try:
            orphans = get_all_running_steps()
        except Exception as e:
            logger.warning(f"restart sweep skipped (DB unavailable): {e}")
            return
        for row in orphans:
            update_step(row["id"], status="interrupted", interrupt_reason="restart", finished_at=_now_iso())
            update_task(row["task_id"], abort_reason="restart")
        # A frozen task parked for the answer image-description thread died
        # with the process (the daemon thread is gone): restore awaiting_user
        # so the task does not hang forever.
        for t in get_tasks_by_status("frozen"):
            if t.get("abort_reason") == "answer_images":
                update_task(t["id"], status="awaiting_user", abort_reason=None)
            elif t.get("abort_reason") == ATTACHMENT_TRANSCRIPT_REASON:
                # The transcription thread died with the process (its files are
                # still on disk): restart it instead of unfreezing — restoring
                # queued/awaiting here would hand the planner an empty request.
                from app.tasks.audio_transcribe import start_task_audio_transcription

                start_task_audio_transcription(self.app, t["id"])
        # The pointer SET died with the old process too — rebuild it empty.
        try:
            redis_client = self._redis or self.app.request_queue.redis
            redis_client.delete(RUNNING_STEPS_SET)
        except Exception as e:
            logger.warning(f"running-steps SET reset failed: {e}")
        if orphans:
            logger.info(f"restart sweep: marked {len(orphans)} orphaned running step(s) interrupted")

    # ── Config / gate ─────────────────────────────────────────────────

    def _cfg(self, key: str, default: Any) -> Any:
        try:
            value = self.app.config.get(key, default)
            return default if value is None else value
        except Exception:
            return default

    def _gate_value(self, override: bool | None = None) -> bool:
        """Gate predicate (spec §2 point 2); ``override`` is the test seam."""
        if override is not None:
            return bool(override)
        try:
            redis_client = self._redis or _get_request_queue().redis
        except Exception:
            logger.exception("gate Redis handle unavailable — treating the gate as CLOSED")
            return False
        return bool(
            is_gate_open(
                redis_client,
                int(self._cfg("TASK_QUIET_PERIOD_S", TASK_QUIET_PERIOD_S)),
                _gpu_busy,
                _queues_empty,
            )
        )

    def _redis_flag(self, key: str) -> bool:
        """Read a Redis flag (step cancel / task cancel); failure ⇒ False."""
        try:
            redis_client = self._redis or self.app.request_queue.redis
            return bool(redis_client.get(key))
        except Exception:
            return False

    def _publish_running_step(self, task_id: str, step_id: str) -> None:
        """Announce the in-flight step so the chat hook can raise its cancel flag.

        Best effort: Redis failure must never break step execution.
        """
        try:
            redis_client = self._redis or self.app.request_queue.redis
            pipe = redis_client.pipeline()
            pipe.set(f"{RUNNING_STEP_KEY_PREFIX}{task_id}", step_id)
            pipe.sadd(RUNNING_STEPS_SET, task_id)
            pipe.execute()
        except Exception as e:
            logger.warning(f"running-step pointer publish failed (task {task_id}): {e}")

    def _clear_running_step(self, task_id: str) -> None:
        """Retract the in-flight pointer once the step left ``running`` (or the
        task reached a terminal state)."""
        try:
            redis_client = self._redis or self.app.request_queue.redis
            pipe = redis_client.pipeline()
            pipe.delete(f"{RUNNING_STEP_KEY_PREFIX}{task_id}")
            pipe.srem(RUNNING_STEPS_SET, task_id)
            pipe.execute()
        except Exception as e:
            logger.warning(f"running-step pointer clear failed (task {task_id}): {e}")

    def _publish_task_update(self, task_id: str) -> None:
        """Announce the task's post-cycle state on the user's SSE channel.

        Best effort (Tasks panel live status, spec §5); a publish failure never
        breaks the cycle.
        """
        try:
            from app.tasks.events import publish_task_update

            publish_task_update(self.app, get_task(task_id))
        except Exception as e:
            logger.warning(f"task-update publish failed (task {task_id}): {e}")

    def _clear_cancel_flag(self, step_id: str) -> None:
        """Consumer-clears rule: the flag is removed once the abort is processed
        (and before every re-run, so a stale flag cannot abort a fresh attempt)."""
        try:
            redis_client = self._redis or self.app.request_queue.redis
            redis_client.delete(f"{CANCEL_KEY_PREFIX}{step_id}")
        except Exception as e:
            logger.warning(f"cancel-flag clear failed (step {step_id}): {e}")

    # ── One cycle ─────────────────────────────────────────────────────

    def _process_one_cycle(self, gate_open: bool | None = None) -> None:
        """One cycle: select → gate → act.

        ``gate_open`` overrides the gate predicate (executor tests); in the
        live thread it stays None and the real gate is evaluated.
        """
        if self.app is None:
            raise RuntimeError("TaskExecutor has no app bound")

        if not self._gate_value(gate_open):
            # Gate closed: freeze the active task (only if it was active), then
            # sleep — a queued task is simply not started on a closed gate. A
            # running step is interrupted ONLY when this process owns it
            # (self._current_step_id); a row left running by a previous process
            # already got its restart marker and is left alone here.
            for t in get_tasks_by_status("active"):
                running = get_running_step(t["id"])
                if running and self._current_step_id == running["id"]:
                    self._current_step_id = None
                    update_step(
                        running["id"],
                        status="interrupted",
                        interrupt_reason="chat",
                        finished_at=_now_iso(),
                    )
                    self._clear_running_step(t["id"])
                update_task(t["id"], status="frozen", abort_reason="chat")
            return

        # Gate open: consume the newest unprocessed user answer first (resume-
        # preempted, spec §2 rule 6).
        ui = get_unprocessed_user_input()
        if ui is not None:
            self._process_user_answer(ui)

        # Deferred subtasks (Task 8): with the gate open, the slow-worker guard
        # may have parked tagged media tasks — move them back onto the slow
        # queue so the worker can run them. Fail-open: a broken queue must not
        # kill the cycle.
        try:
            requeued = _get_request_queue().requeue_deferred_subtasks()
            if requeued:
                logger.info(f"requeued {requeued} deferred subtask(s) onto the slow queue")
        except Exception as e:
            logger.warning(f"deferred subtask requeue skipped: {e}")

        # Selection loop. Chained only while the slot keeps being RELEASED
        # (awaiting_user) — an active/frozen task holds the slot, a terminal
        # task waits for the next poll. ``seen`` guards against a task that
        # stays awaiting (answer still missing) being re-picked forever.
        # Publish only on a status TRANSITION: an awaiting task is re-picked
        # every poll cycle and a per-cycle SSE event made the task window
        # fully re-render (flicker) once a second.
        seen: set[str] = set()
        while True:
            pick = next((t for t in get_in_progress_tasks() if t["id"] not in seen), None)
            if pick is not None:
                seen.add(pick["id"])
                status_before = pick.get("status")
                self._run_task(pick, gate_open=gate_open)
                current = get_task(pick["id"])
                if current is not None and current.get("status") != status_before:
                    self._publish_task_update(pick["id"])
                if current is None or current["status"] != "awaiting_user":
                    return  # slot held (active/frozen) or terminal — stop the cycle
                continue  # slot released — select the next task in this cycle

            queued = get_queued_task()
            if queued is None or queued["id"] in seen:
                return
            seen.add(queued["id"])
            update_task(queued["id"], status="active", started_at=_now_iso())
            self._run_task(get_task(queued["id"]) or queued, gate_open=gate_open)
            current = get_task(queued["id"])
            # A queued task always transitions (queued → anything) — publish.
            self._publish_task_update(queued["id"])
            if current is None or current["status"] != "awaiting_user":
                return
            continue

    # ── User answer resume (spec §2 rule 6) ───────────────────────────

    def _process_user_answer(self, ui: dict) -> None:
        """Consume an unprocessed user_input step: close the question as passed,
        preempt any running task, bring the answered task back to active."""
        task_id = ui["task_id"]
        parent_id = ui.get("parent_step_id")
        question_seq: int | None = None
        if parent_id:
            question = get_step(parent_id)
            if question is not None:
                if question.get("parent_step_id"):
                    # Verifier question (kind=question, parent = the rejected
                    # direct step): the direct step re-runs, so the loop must
                    # resume AT its 0-based plan position (parent seq - 1).
                    parent = get_step(question["parent_step_id"])
                    question_seq = int(parent["seq"]) - 1 if parent else None
                else:
                    # Plan question: the question step itself is consumed —
                    # resume at the next plan position.
                    question_seq = int(question["seq"])
                if question["status"] in ("pending", "awaiting_user"):
                    update_step(parent_id, status="passed", finished_at=_now_iso())
        if question_seq is None:
            # No linked question row — advance past the current pointer.
            task = get_task(task_id) or {}
            question_seq = int(task.get("next_step_index") or 0) + 1
        update_step(ui["id"], status="passed")
        self._preempt_running_task(task_id)
        # Plan question: resume at question_seq (past the consumed question).
        # Verifier question: resume at the direct step's plan position — it
        # re-runs with the answer in _user_answers_text.
        update_task(task_id, status="active", next_step_index=question_seq)
        self._publish_task_update(task_id)

    def _preempt_running_task(self, keep_task_id: str) -> None:
        """One active task globally: any OTHER active task returns to queued with
        its in-flight step aborted as ``preempted`` (spec §2 rule 6)."""
        for t in get_tasks_by_status("active"):
            if t["id"] == keep_task_id:
                continue
            running = get_running_step(t["id"])
            if running:
                update_step(
                    running["id"],
                    status="interrupted",
                    interrupt_reason="preempted",
                    finished_at=_now_iso(),
                )
            update_task(t["id"], status="queued", abort_reason="preempted")

    # ── Task run (spec §3 step loop) ──────────────────────────────────

    def _run_task(self, task: dict, gate_open: bool | None = None) -> None:
        """Run steps until a break condition (frozen / awaiting / subtask queued /
        terminal). Every direct attempt lands in the DB before the next one."""
        task_id = task["id"]
        task = get_task(task_id) or task
        if task["status"] == "frozen":
            if task.get("abort_reason") in ("answer_images", ATTACHMENT_TRANSCRIPT_REASON):
                # Attachment work is still running: either the answer's
                # image-description thread (the pending user_input row must NOT
                # be consumed until the descriptions are appended) or the
                # Whisper transcription of a request/answer audio (a dictated
                # task must not be planned before its text exists). A later
                # cycle resumes the task once the worker flips the status back.
                return
            # Resuming after a gate re-open: back to active before stepping.
            # The stale abort_reason goes with the freeze — a task that later
            # completes must not keep carrying «chat» (the live list column
            # rendered a finished task as still running).
            update_task(task_id, status="active", abort_reason=None)
            task["status"] = "active"

        # A user stop may have arrived while the task was still queued — honour
        # the flag BEFORE the plan phase so no LLM call is spent on it.
        if self._redis_flag(f"task:cancelled:{task_id}"):
            self._cancel_task(task)
            return

        plan = task.get("step_plan") or {}
        if not (plan.get("steps") if isinstance(plan, dict) else plan):
            # PLAN PHASE: decompose the request before any step can run.
            # Publish first so the panel switches «Составляю план…» (active,
            # total_steps=0) immediately — the phase lasts tens of seconds.
            self._publish_task_update(task_id)
            try:
                plan_status = self._run_plan_phase(task)
            except StepAborted:
                # A stop/cancel arrived while the planner was streaming (tens of
                # seconds) — the same clean user cancellation a direct step gets,
                # instead of an unhandled abort that killed the whole cycle.
                self._cancel_task(get_task(task_id) or task)
                return
            if plan_status != "ok":
                return
            task = get_task(task_id) or task
            # The plan just materialized — the conversation view needs it.
            self._publish_task_update(task_id)

        while True:
            task = get_task(task_id)
            if task is None or task["status"] not in ("active", "frozen"):
                break  # awaiting / cancelled / terminal — stop the loop
            plan = task.get("step_plan") or {}
            steps = plan.get("steps", []) if isinstance(plan, dict) else plan
            idx = int(task.get("next_step_index") or 0)

            if idx >= len(steps):
                self._finalize(task)
                return

            # Budget check before every step start (spec rule).
            if float(task.get("accumulated_exec_seconds") or 0.0) >= float(
                self._cfg("TASK_TOTAL_BUDGET_S", TASK_TOTAL_BUDGET_S)
            ):
                self._budget_expired(task)
                return

            # Per-step gate (spec §2 point 2: the gate is checked before each step).
            if not self._gate_value(gate_open):
                update_task(task_id, status="frozen", abort_reason="chat")
                return

            # Task cancelled via API stop (Task 9 sets the Redis flag).
            if self._redis_flag(f"task:cancelled:{task_id}"):
                self._cancel_task(task)
                return

            step = steps[idx]
            if step.get("user_input_expected"):
                self._ask_user(task, idx, step)
                return  # slot freed

            # Media/RLM subtask (Task 8): a fresh step is enqueued onto the
            # request queue; an already-enqueued one is driven to completion by
            # polling its result store. One peek per cycle — "hold" yields the
            # cycle to the executor loop (which re-checks gate/deferred/answers
            # and resumes polling), while "done" advances past the consumed step.
            if str(step.get("tool", "llm")) in _SUBTASK_TOOLS:
                row = self._step_row_for(task_id, idx, step)
                if row["status"] in ("subtask_queued", "subtask_running"):
                    if self._poll_subtask(task, idx, row, gate_open=gate_open) in ("stop", "hold"):
                        return  # interrupted (frozen) or still running — release the cycle
                    # Consumed — publish so the finished step lands in the
                    # conversation view immediately (previously every subtask
                    # step appeared at once when the whole task completed).
                    self._publish_task_update(task_id)
                    continue  # consumed — re-read the loop state for the next step
                self._enqueue_subtask(task, idx, step, gate_open)
                # The queued step must reach the view now, not on the next cycle.
                self._publish_task_update(task_id)
                return  # the queue owns it now — polling resumes on the next cycle

            if self._execute_direct(task, idx, step) == "stop":
                return
            # The step just finished (result + metadata persisted) — publish so
            # the conversation view fills in while the task keeps running
            # (previously everything arrived at once when the task completed).
            self._publish_task_update(task_id)

    # ── Plan phase ────────────────────────────────────────────────────

    def _task_image_parts(self, task: dict) -> list[dict[str, str]]:
        """The task's attached images as ``{data, name}`` vision parts.

        The stored paths live under the user's upload folder; a missing or
        unreadable file is skipped (the vision step answers over what arrived).
        Each part carries the image's own type — the stored ``file_type``, else
        the type of the file name — so a PNG is never sent as a JPEG.
        """
        parts: list[dict[str, str]] = []
        upload_folder = str(self.app.config.get("UPLOAD_FOLDER") or "")
        for img in task.get("images") or []:
            if not isinstance(img, dict):
                continue
            rel = str(img.get("file_path") or "")
            name = str(img.get("file_name") or "")
            if not rel or not upload_folder:
                continue
            path = os.path.realpath(os.path.join(upload_folder, rel))
            if not path.startswith(os.path.realpath(upload_folder) + os.sep) or not os.path.isfile(path):
                logger.warning(f"task {task.get('id')}: attached image missing on disk: {rel}")
                continue
            mime = str(img.get("file_type") or "")
            if not mime.startswith("image/"):
                mime = str(mimetypes.guess_type(name)[0] or "")
            if not mime.startswith("image/"):
                mime = "image/jpeg"
            try:
                with open(path, "rb") as fh:
                    parts.append({"data": f"data:{mime};base64," + base64.b64encode(fh.read()).decode(), "name": name})
            except OSError as e:
                logger.warning(f"task {task.get('id')}: attached image unread ({rel}): {e}")
        return parts

    def _prior_step_media_image(self, task_id: str, idx: int) -> str | None:
        """Raw base64 of the latest image artifact saved by a PRIOR step.

        The video_gen sub-task animates the image a previous step produced (the
        image_edit/image_gen artifact lives in that step's ``extra.media``);
        ``None`` when no prior step saved an image (text-to-video then).
        """
        best: tuple[int, str] | None = None
        for r in get_task_steps(task_id):
            if int(r.get("seq") or 0) > idx:
                continue
            media = (r.get("extra") or {}).get("media") or {}
            file_type = str(media.get("file_type") or "")
            rel = str(media.get("file_path") or "")
            if not rel or not file_type.startswith("image/"):
                continue
            seq = int(r.get("seq") or 0)
            if best is None or seq > best[0]:
                best = (seq, rel)
        if best is None:
            return None
        upload_folder = str(self.app.config.get("UPLOAD_FOLDER") or "")
        if not upload_folder:
            return None
        path = os.path.realpath(os.path.join(upload_folder, best[1]))
        if not path.startswith(os.path.realpath(upload_folder) + os.sep) or not os.path.isfile(path):
            logger.warning(f"task {task_id}: prior step image missing on disk: {best[1]}")
            return None
        try:
            with open(path, "rb") as fh:
                return "data:image/jpeg;base64," + base64.b64encode(fh.read()).decode()
        except OSError as e:
            logger.warning(f"task {task_id}: prior step image unread ({best[1]}): {e}")
            return None

    def _camera_rooms(self, task: dict) -> list[str]:
        """The user's accessible camera room names (empty when cameras are off)."""
        try:
            cam = self.app.modules.get("cam")
            if cam is None or not getattr(cam, "available", False):
                return []
            rooms = cam.get_available_rooms(task.get("user_id") or "")
            return sorted(str(n) for n in (rooms or {}).values())
        except Exception as e:
            logger.warning(f"camera rooms not resolved for task {task.get('id')}: {e}")
            return []

    def _run_plan_phase(self, task: dict) -> str:
        """Planner call → parse → step rows. Returns "ok" or "failed"."""
        task_id = task["id"]
        lang = task.get("lang") or "ru"
        deep_note = ""
        if task.get("deep_analysis"):
            from flask_babel import force_locale
            from flask_babel import gettext as _

            with force_locale(lang):
                deep_note = _(
                    "Deep analysis: the user turned on 🔬 Deep Analysis — include a step with tool "
                    '"rlm" that deeply analyzes the attached documents (all of the user\'s documents '
                    "when none are attached) and answers the task request; do not ask which documents "
                    "to analyze."
                )
        request_text = _request_text(task)
        attachments = _attachments_section(task, lang)
        params = {
            "task_request": request_text,
            "capabilities_section": _capabilities_section(
                camera_rooms=self._camera_rooms(task), has_images=bool(task.get("images"))
            ),
            "slm_facts": recall_slm_facts(self.app, request_text, task.get("user_id") or "", lang),
            "response_language": _response_language_name(lang),
            "deep_analysis_note": deep_note,
            "current_time": get_current_time_in_timezone(self.app) or "",
            "attachments_section": attachments,
        }
        from app.utils import format_prompt

        with self.app.app_context():
            prompt = format_prompt("task_plan.template", params, lang=lang)
        if not prompt:
            self._fail_task(task_id, lang, _MSG_PLAN_TEMPLATE_UNAVAILABLE)
            return "failed"

        # The planner streams for tens of seconds — a UI/API stop arriving
        # mid-plan must take effect at once (spec §2: the button marks the
        # running step), so the cancel flag is checked between chunks. The
        # live stage line mirrors the chat's reasoning stages: model load
        # (the long JIT cold load) → the plan itself → cleared.
        from app.tasks.events import publish_task_stage

        def _on_model_loaded(phase: str) -> None:
            if phase == "generating":
                publish_task_stage(self.app, task, "making_plan")

        cancel_check = lambda: self._redis_flag(f"task:cancelled:{task_id}")  # noqa: E731
        publish_task_stage(self.app, task, "loading_reasoning_model")
        start = time.monotonic()
        try:
            plan = None
            raw, usage = "", {}
            for _attempt in range(2):
                raw = _run_planner(self.app, prompt, cancel_check, lang, status_callback=_on_model_loaded)
                usage = _planner_usage()
                plan = _parse_plan(raw)
                if plan is not None:
                    break
                # One retry before giving up (transient planner noise); the
                # model is already loaded and the header shows the LAST call's
                # totals (each attempt re-bills the prompt).
            planner_meta = {
                "exec_seconds": round(time.monotonic() - start, 2),
                "usage": {
                    "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                    "completion_tokens": int(usage.get("completion_tokens") or 0),
                },
            }
        finally:
            publish_task_stage(self.app, task, None)
        if plan is None:
            self._fail_task(task_id, lang, _MSG_PLAN_UNREADABLE)
            return "failed"

        # Deep Analysis toggle at submit time: if the user turned on 🔬 but the
        # planner omitted the rlm step, append it now (idempotent). The note in
        # the planner prompt is advisory; we enforce the contract here.
        if task.get("deep_analysis"):
            has_rlm = any(isinstance(s, dict) and str(s.get("tool") or "") == "rlm" for s in (plan.get("steps") or []))
            if not has_rlm:
                from flask_babel import force_locale
                from flask_babel import gettext as _

                with force_locale(lang):
                    criteria = _("the analysis contains the concrete data from the documents that answers the request")
                goal = str(plan.get("task_request") or task.get("title") or "")
                seq = len(plan.get("steps") or []) + 1
                plan.setdefault("steps", []).append(
                    {
                        "seq": seq,
                        "goal": goal,
                        "tool": "rlm",
                        "checkpoint_criteria": criteria,
                        "user_input_expected": False,
                    }
                )
                logger.info(f"task {task_id}: forced rlm step (seq {seq}) — deep-analysis toggle ON at submit time")

        # Keep the user's original request as the title — the planner's own
        # title (an LLM rephrase like «Ответить на вопрос о количестве дней в
        # году») never overwrites it. ``plan_meta`` carries the chat-style
        # header of the plan message itself (model + real tokens + duration).
        config = get_model_config("reasoning") or {}
        plan_meta = {
            "model_name": str(config.get("model_name") or ""),
            "model_type": "reasoning",
            **planner_meta,
        }
        fields: dict[str, Any] = {"step_plan": {**plan, "plan_meta": plan_meta}, "next_step_index": 0}
        update_task(task_id, **fields)
        self._create_plan_step_rows(task_id, plan["steps"])
        return "ok"

    def _create_plan_step_rows(self, task_id: str, plan_steps: list[dict]) -> None:
        """Create one step row per plan position (kind per capability class)."""
        # The plan carries the planner's own header metadata (a reasoning
        # call): a plan-typed clarifying question (user_input_expected step)
        # borrows it so its message shows the model + tokens + time too.
        task = get_task(task_id) or {}
        plan_meta = (task.get("step_plan") or {}).get("plan_meta") if isinstance(task.get("step_plan"), dict) else None
        plan_meta = plan_meta if isinstance(plan_meta, dict) else {}
        for pos, s in enumerate(plan_steps or []):
            kind = self._step_kind(s)
            sid = add_step(
                task_id=task_id,
                seq=pos + 1,
                kind=kind,
                goal=str(s.get("goal", "")),
                tool=str(s.get("tool", "")),
                checkpoint_criteria=str(s.get("checkpoint_criteria", "")),
            )
            if kind == "question" and (plan_meta.get("model_name") or plan_meta.get("usage")):
                update_step(
                    sid,
                    extra={
                        "model_name": str(plan_meta.get("model_name") or ""),
                        "model_type": "reasoning",
                        "usage": dict(plan_meta.get("usage") or {}),
                        "exec_seconds": float(plan_meta.get("exec_seconds") or 0.0),
                    },
                )

    @staticmethod
    def _step_kind(step: dict) -> str:
        """Row kind for a plan step: question (user input) / subtask (queue) / direct."""
        if step.get("user_input_expected"):
            return "question"
        if str(step.get("tool", "")) in _SUBTASK_TOOLS:
            return "subtask"
        return "direct"

    def _fail_task(self, task_id: str, lang: str, message: str) -> None:
        """Terminal failure with a localized ⚠️ error step (order per M3:
        error step persisted first, then the terminal status write)."""
        from app.llamacpp_client import _tr

        self._clear_running_step(task_id)
        add_final_step(task_id, f"⚠️ {_tr(message, lang=lang)}", status="failed")
        update_task(task_id, status="failed", completed_at=_now_iso())
        logger.error(f"task {task_id} failed: {message}")

    # ── Direct steps ──────────────────────────────────────────────────

    def _step_row_for(self, task_id: str, idx: int, step: dict) -> dict:
        """The step row for plan position ``idx`` (created defensively if absent)."""
        want_seq = idx + 1
        for r in get_task_steps(task_id):
            if int(r["seq"]) == want_seq:
                return r
        sid = add_step(
            task_id=task_id,
            seq=want_seq,
            kind=self._step_kind(step),
            goal=str(step.get("goal", "")),
            tool=str(step.get("tool", "")),
            checkpoint_criteria=str(step.get("checkpoint_criteria", "")),
        )
        return get_step(sid)  # type: ignore[return-value]

    def _execute_direct(self, task: dict, idx: int, step: dict) -> str:
        """One direct step incl. corrective checkpoint retries (spec §3).

        Returns "continue" (move to the next step) or "stop" (abort → frozen).
        """
        task_id = task["id"]
        lang = task.get("lang") or "ru"
        row = self._step_row_for(task_id, idx, step)
        max_attempts = int(self._cfg("TASK_STEP_MAX_ATTEMPTS", TASK_STEP_MAX_ATTEMPTS))
        plan_text = _plan_text(task.get("step_plan"))
        user_answers = self._user_answers_text(task_id)
        corrective_note = ""  # checkpoint mismatch carried into the retry digest

        while True:
            attempts = int(row.get("attempts") or 0)
            if attempts >= max_attempts:
                from app.llamacpp_client import _tr

                last_reason = str(corrective_note[:300]) if corrective_note else str(row.get("checkpoint_result") or "")
                user_error = _tr("step attempts exhausted", lang)
                if last_reason:
                    user_error += f": {last_reason[:300]}"
                update_step(
                    row["id"],
                    status="failed",
                    last_error=user_error,
                    finished_at=_now_iso(),
                )
                logger.warning(f"task {task_id} step {idx + 1} failed after {attempts} attempts — continuing")
                update_task(task_id, next_step_index=idx + 1)
                return "continue"  # the failure is carried into the context

            if self._redis_flag(f"{CANCEL_KEY_PREFIX}{row['id']}"):
                # Flag set by the chat hook (or a leftover): abort BEFORE running.
                self._clear_cancel_flag(row["id"])
                update_step(
                    row["id"],
                    status="interrupted",
                    interrupt_reason="chat",
                    finished_at=_now_iso(),
                )
                update_task(task_id, status="frozen", abort_reason="chat")
                return "stop"

            # Publish the running-step pointer BEFORE the row goes running so a
            # chat request arriving mid-stream can raise the cancel flag.
            self._publish_running_step(task_id, row["id"])
            self._current_step_id = row["id"]
            update_step(row["id"], status="running", started_at=_now_iso(), attempts=attempts + 1)
            digest = self._context_digest(task_id, idx, corrective_note=corrective_note)
            start = time.monotonic()
            try:
                outcome = run_direct_step(
                    self.app,
                    row,
                    digest,
                    lambda step_id=row["id"]: self._redis_flag(f"{CANCEL_KEY_PREFIX}{step_id}"),
                    user_id=task.get("user_id") or "",
                    lang=lang,
                    response_style=task.get("response_style") or "neutral",
                    task_request=self._task_request(task),
                    plan_text=plan_text,
                    user_answers=user_answers,
                    doc_ids=task.get("doc_ids") or [],
                )
            except StepAborted:
                elapsed = round(time.monotonic() - start, 2)
                self._add_budget(task_id, elapsed)
                self._end_step(task_id, row["id"], status="interrupted", interrupt_reason="chat", exec_seconds=elapsed)
                update_task(task_id, status="frozen", abort_reason="chat")
                return "stop"
            except Exception as e:
                elapsed = round(time.monotonic() - start, 2)
                self._add_budget(task_id, elapsed)
                self._end_step(task_id, row["id"], status="failed", exec_seconds=elapsed, last_error=str(e)[:500])
                logger.error(f"task {task_id} step {idx + 1} raised: {e} — continuing with the next step")
                update_task(task_id, next_step_index=idx + 1)
                return "continue"

            elapsed = float(outcome.get("exec_seconds") or 0.0)
            # Chat-style header data (per step): model + real token usage.
            step_extra: dict[str, Any] = {}
            if outcome.get("model_name"):
                step_extra["model_name"] = str(outcome["model_name"])
            if outcome.get("usage"):
                step_extra["usage"] = {
                    "prompt_tokens": int(outcome["usage"].get("prompt_tokens") or 0),
                    "completion_tokens": int(outcome["usage"].get("completion_tokens") or 0),
                }
            checkpoint = outcome.get("checkpoint") or {}
            self._add_budget(task_id, elapsed + float(checkpoint.get("exec_seconds") or 0.0))
            # The step's displayed duration covers the tool/step call AND its
            # checkpoint verification, so the header's ⏱️/🚀 match the
            # model+tokens shown there (a tool step reports its verifier).
            step_seconds = round(elapsed + float(checkpoint.get("exec_seconds") or 0.0), 2)

            status = outcome.get("status")
            result = outcome.get("result") or ""
            if status == "passed":
                self._end_step(
                    task_id,
                    row["id"],
                    status="passed",
                    result=result,
                    exec_seconds=step_seconds,
                    extra=step_extra,
                    checkpoint_result=str(checkpoint.get("explanation", "")),
                    checkpoint_verdict=bool(checkpoint.get("verdict")),
                )
                update_task(task_id, next_step_index=idx + 1)
                self._maybe_extract_facts(task, row, result)
                return "continue"
            if status == "aborted":
                # Race-cancel on a fast tool: the result is kept but not trusted.
                self._end_step(
                    task_id,
                    row["id"],
                    status="interrupted",
                    interrupt_reason="chat",
                    result=result,
                    exec_seconds=elapsed,
                )
                update_task(task_id, status="frozen", abort_reason="chat")
                return "stop"
            if status == "error":
                # Tool failure (not a verifier rejection): step failed, task continues.
                self._end_step(
                    task_id,
                    row["id"],
                    status="failed",
                    result=result,
                    last_error=result[:500] or "tool error",
                    exec_seconds=elapsed,
                    extra=step_extra,
                )
                update_task(task_id, next_step_index=idx + 1)
                return "continue"

            # status == "failed": the checkpoint rejected the result — corrective
            # retry of the SAME step with the mismatch fed back into the next
            # attempt's context digest (I2). Special third verdict: user data
            # missing — a dedicated question row is appended (like plan
            # questions) and the direct step re-runs with the answer; the
            # attempt is NOT counted (an incomplete spec is not a model
            # failure).
            needs_input = checkpoint.get("needs_user_input") is True and str(checkpoint.get("question") or "").strip()
            if needs_input:
                question = str(checkpoint["question"]).strip()
                self._current_step_id = None
                self._clear_running_step(task_id)
                self._clear_cancel_flag(row["id"])
                # Reset the rejected attempt: the row re-runs from scratch.
                update_step(row["id"], status="pending", result="")
                with get_db() as conn:
                    c = conn.cursor()
                    c.execute("SELECT COALESCE(MAX(seq), 0) AS mx FROM task_steps WHERE task_id = %s", (task_id,))
                    next_seq = int(c.fetchone()["mx"] or 0) + 1
                q_id = add_step(task_id=task_id, seq=next_seq, kind="question", goal=question, parent_step_id=row["id"])
                # The question comes from the checkpoint verifier (short
                # reasoning call): carry its model + real token usage + time so
                # the question message renders the same chat-style header as
                # every other task message.
                q_extra: dict[str, Any] = {}
                q_extra["model_name"] = str(checkpoint.get("model_name") or "")
                q_extra["model_type"] = "reasoning"
                q_extra["usage"] = dict(checkpoint.get("usage") or {})
                q_extra["exec_seconds"] = float(checkpoint.get("exec_seconds") or 0.0)
                update_step(q_id, status="awaiting_user", result=question, started_at=_now_iso(), extra=q_extra)
                update_task(task_id, status="awaiting_user")
                self._publish_task_update(task_id)
                logger.info(f"task {task_id} step {idx + 1} needs user input — question asked")
                return "stop"
            self._end_step(
                task_id,
                row["id"],
                status="failed",
                result=result,
                exec_seconds=step_seconds,
                extra=step_extra,
                checkpoint_result=str(checkpoint.get("explanation", "")),
                checkpoint_verdict=False,
            )
            corrective_note = str(checkpoint.get("explanation") or "")
            logger.info(
                f"task {task_id} step {idx + 1} checkpoint rejected (attempt {attempts + 1}/{max_attempts})"
                f" — retrying with the mismatch in context"
            )
            row = get_step(row["id"]) or row

    def _task_request(self, task: dict) -> str:
        """The user's original request plus transcribed audio (module helper)."""
        return _request_text(task)

    def _user_answers_text(self, task_id: str) -> str:
        """Q&A pairs accumulated so far (fed to the step prompt)."""
        parts = []
        for r in get_task_steps(task_id):
            if r["kind"] == "question":
                parts.append(f"Q: {r['result'] or r['goal'] or ''}")
            elif r["kind"] == "user_input":
                parts.append(f"A: {r['result'] or ''}")
        return "\n".join(parts)[:4000]

    def _end_step(self, task_id: str, step_id: str, status: str, **fields: Any) -> None:
        """Close a step attempt: persist the final row state and retract the
        running-step pointer (the chat hook can no longer cancel it)."""
        self._current_step_id = None
        self._clear_running_step(task_id)
        self._clear_cancel_flag(step_id)
        update_step(step_id, status=status, finished_at=_now_iso(), **fields)

    def _context_digest(self, task_id: str, upto_idx: int, corrective_note: str = "", char_cap: int = 12000) -> str:
        """Previous step results as the running context for the next step.

        ``corrective_note`` (I2): the checkpoint mismatch of the just-rejected
        attempt of THIS step, so the model fixes the actual problem on retry.
        """
        parts = []
        for r in get_task_steps(task_id):
            if int(r["seq"]) > upto_idx:
                continue
            if r["kind"] in ("question", "user_input", "final", "plan"):
                continue
            if r["status"] in ("passed", "failed", "interrupted"):
                body = (r["result"] or "")[:3000]
                if r["status"] == "failed":
                    if r.get("checkpoint_result") and r.get("checkpoint_verdict") is False:
                        body = f"[checkpoint rejected: {r.get('checkpoint_result')}] {body}"
                    else:
                        body = f"[failed: {r.get('last_error') or 'checkpoint rejected'}] {body}"
                parts.append(f"Step {r['seq']} ({r['tool'] or 'llm'}) — {r['goal']}: {body}")
        if corrective_note:
            parts.append(
                f"Corrective note: the verifier REJECTED the previous attempt of step {upto_idx + 1}. "
                f"Reason: {corrective_note[:1000]}. Fix the stated problem and redo this step."
            )
        return "\n".join(parts)[:char_cap]

    def _add_budget(self, task_id: str, seconds: float) -> None:
        """Read-modify-write the task budget (single writer: the executor)."""
        task = get_task(task_id)
        if task is None:
            return
        accumulated = float(task.get("accumulated_exec_seconds") or 0.0) + float(seconds or 0.0)
        update_task(task_id, accumulated_exec_seconds=round(accumulated, 2))

    # ── Clarifying question / subtask / terminal states ───────────────

    def _ask_user(self, task: dict, idx: int, step: dict) -> None:
        """question step: the question text lands in ``result``, task → awaiting_user,
        slot released (the cycle's selection loop picks the next task)."""
        task_id = task["id"]
        row = self._step_row_for(task_id, idx, step)
        self._clear_running_step(task_id)
        # started_at doubles as the message timestamp for the conversation
        # view (task_steps has no created_at — a row without started_at
        # rendered «📅 » with no time).
        update_step(row["id"], status="awaiting_user", result=str(step.get("goal", "")), started_at=_now_iso())
        update_task(task_id, status="awaiting_user")
        self._publish_task_update(task_id)
        logger.info(f"task {task_id} awaiting user input at step {idx + 1}")

    def _wait_for_attachments(self, task: dict, row: dict, gate_open: bool | None = None) -> bool:
        """Wait until the task's attached documents finished indexing.

        A scanned PDF has no text on disk (no ``.recognized_text`` sidecar)
        before its OCR finished, so the RLM corpus would be read empty — the
        same wait the rag step performs. The wait aborts as soon as the gate
        closes (a chat request), exactly like the per-step gate check: the step
        dies ``interrupted`` (``chat``) and re-executes on resume. Returns
        False when the wait was preempted, True when the documents settled.
        """
        try:
            wait_for_documents_indexed(
                self.app,
                str(task.get("user_id") or ""),
                [str(d) for d in (task.get("doc_ids") or [])],
                lambda: not self._gate_value(gate_open),
            )
        except StepAborted:
            self._end_step(task["id"], row["id"], status="interrupted", interrupt_reason="chat")
            update_task(task["id"], status="frozen", abort_reason="chat")
            return False
        return True

    def _enqueue_subtask(self, task: dict, idx: int, step: dict, gate_open: bool | None = None) -> None:
        """Media/RLM subtask → the existing request queue with the task tag.

        Task 8 adds completion polling; the step loop breaks after enqueueing.
        """
        task_id = task["id"]
        tool = str(step.get("tool", ""))
        row = self._step_row_for(task_id, idx, step)
        task_type = _SUBTASK_TYPE.get(tool, "image_gen")
        goal = str(step.get("goal", ""))
        request_data: dict[str, Any] = {
            "type": task_type,
            # The queue handlers read the step's instruction from ``text`` (the
            # planner goal); ``prompt`` stays for the tools that look for it.
            "text": goal,
            "prompt": goal,
            # Gate hook excludes tagged requests from chat activity.
            "task_step_subtask": row["id"],
        }
        if tool == "rlm":
            # RLM corpus = the task's attached documents; with none attached,
            # every indexed document of the user is analyzed (the same scope
            # rule the rag step follows).
            attached_ids = [str(d) for d in (task.get("doc_ids") or [])]
            doc_ids = attached_ids
            if not doc_ids:
                try:
                    from app.db import INDEX_STATUS_INDEXED, get_user_documents

                    doc_ids = [
                        str(d["id"])
                        for d in (get_user_documents(task.get("user_id") or "") or [])
                        if d.get("index_status") == INDEX_STATUS_INDEXED and d.get("id")
                    ]
                except Exception as e:
                    logger.warning(f"rlm doc scope resolution failed for task {task_id}: {e}")
            elif not self._wait_for_attachments(task, row, gate_open):
                return  # a chat request preempted the wait — task frozen
            request_data["doc_ids"] = doc_ids
        if tool == "crawl":
            # The site address comes from the user's own request (the planner
            # keeps it verbatim in the goal); the handler re-extracts it too.
            request_data["url"] = _first_url(str(step.get("goal") or "")) or _first_url(str(task.get("title") or ""))
        if tool == "camera":
            # The goal names the room (one step per room); the handler resolves
            # the name against the user's available rooms. The task's request
            # travels along as the analysis QUESTION (chat parity): the raw
            # goal («Сделайте снимок…») reads as an image-generation request
            # and misroutes the multimodal classifier to a router marker.
            request_data["room"] = str(step.get("goal") or "")
            request_data["task_question"] = str(task.get("title") or step.get("goal") or "")
        if tool == "vision":
            # The attached images travel as base64 data (the stored files are
            # read back into data: URLs at enqueue time) — the sub-task runs
            # the same joint multimodal call the chat image path uses.
            request_data["images"] = self._task_image_parts(task)
            request_data["task_question"] = str(task.get("title") or step.get("goal") or "")
        if tool == "image_edit":
            # The source to edit is the task's first attached image (its bytes
            # are read back from the stored file, chat parity: the edit pipeline
            # takes raw base64). Without an attachment there is nothing to edit —
            # fail the step instead of spending a multimodal call on it.
            parts = self._task_image_parts(task)
            if not parts:
                logger.warning(f"task {task_id} step {idx + 1} (image_edit) has no attached source image")
                self._end_step(task_id, row["id"], status="failed", last_error="no source image attached")
                update_task(task_id, next_step_index=idx + 1)
                self._publish_task_update(task_id)
                return
            request_data["file_data"] = _strip_data_url(str(parts[0].get("data") or ""))
            request_data["file_name"] = str(parts[0].get("name") or "")
        if tool == "video_gen":
            # Image+text pipeline when a PRIOR step produced an image artifact
            # (e.g. step 1 edited a photo, step 2 animates THE EDITED IMAGE —
            # the live defect made the video from the goal text instead). Chat
            # parity: a request with no image stays text-to-video, so a missing
            # prior image is not an error — the goal text alone goes through.
            media_img = self._prior_step_media_image(task_id, idx)
            if media_img is not None:
                request_data["file_data"] = media_img
        # Write-ahead (v12.6 fix): the step row must already say
        # ``subtask_queued`` BEFORE the request reaches the queue. An idle slow
        # worker dequeues it within milliseconds and its orphan guard drops a
        # tagged sub-task whose step is still 'pending' — every camera step then
        # died with "subtask result expired" and no snapshot was ever taken
        # (live log: "dequeued task … (type=camera_task)" 6 ms BEFORE the
        # executor's own "queued as subtask" line).
        update_step(row["id"], status="subtask_queued")
        try:
            queue = _get_request_queue()
            subtask_queue_id, _info = queue.add_request(
                user_id=task.get("user_id") or "",
                # The queue session id IS the task id (not ``task:<id>``): the
                # media the sub-task saves lands in the upload folder named after
                # the task, which /api/files resolves through the tasks table —
                # a «task:<id>» folder was rejected by save_uploaded_file (not a
                # UUID) and an unowned folder 403'd the task view's <img>.
                session_id=task_id,
                request_data=request_data,
                user_class=int(task.get("task_class") or 5),
                lang=task.get("lang") or "ru",
                task_type=task_type,
            )
        except Exception as e:
            logger.error(f"subtask enqueue failed for task {task_id} step {idx + 1}: {e}")
            self._end_step(task_id, row["id"], status="failed", last_error=str(e)[:500])
            update_task(task_id, next_step_index=idx + 1)
            return
        self._current_step_id = None
        self._clear_running_step(task_id)
        # Resume of an interrupted subtask: keep the superseded queue id(s) in
        # extra for the audit trail (the old request was cancelled on abort —
        # re-cancel defensively so a late worker completion can never be
        # mistaken for this step's result).
        merged_extra = {**(row.get("extra") or {}), "subtask_queue_id": subtask_queue_id}
        old_id = (row.get("extra") or {}).get("subtask_queue_id")
        if old_id and str(old_id) != str(subtask_queue_id):
            merged_extra.setdefault("superseded_queue_ids", []).append(old_id)
            try:
                _get_request_queue().cancel_task(str(old_id))
            except Exception as e:
                logger.warning(f"old subtask re-cancel failed ({old_id}): {e}")
        update_step(row["id"], extra=merged_extra)
        logger.info(f"task {task_id} step {idx + 1} ({tool}) queued as subtask {subtask_queue_id}")

    # ── Subtask polling (Task 8) ──────────────────────────────────────

    def _peek_subtask_result(self, queue_id: str) -> dict | None:
        """Non-blocking peek of a subtask's queue result (the API bridge's key).

        Returns the stored envelope ``{"status": "completed"|"error", ...}``
        or None while the result is absent/queued. Test seam.
        """
        try:
            return _get_request_queue().check_result(queue_id)
        except Exception as e:
            logger.warning(f"subtask result peek failed ({queue_id}): {e}")
            return None

    def _subtask_alive(self, queue_id: str) -> bool:
        """Is the subtask still pending in the queue machinery?

        Two states count as alive: the worker holds it in a processing hash, or
        the id still WAITS in a queue list (slow/deferred — the guard parks
        tagged work there, and a subtask behind other queue work has not been
        picked up yet). Checking only the processing hashes would wrongly
        declare a merely-queued subtask expired once the poll budget runs out.
        Fail-closed (False) so a broken queue never freezes a task forever.
        """
        try:
            queue = _get_request_queue()
            for key in (
                queue.processing_key,
                queue.slow_processing_key,
                queue.background_processing_key,
                queue.crawl_processing_key,
            ):
                if queue.redis.hexists(key, queue_id):
                    return True
            for key in (
                queue.slow_queue_key,
                queue.queue_key,
                queue.background_queue_key,
                queue.crawl_queue_key,
                DEFERRED_SUBTASKS_KEY,
            ):
                if queue_id in (queue.redis.lrange(key, 0, -1) or []):
                    return True
            return False
        except Exception as e:
            logger.warning(f"subtask liveness check failed ({queue_id}): {e}")
            return False

    def _poll_budget_polls(self) -> int:
        """How many cycles a subtask result may stay absent before a liveness check."""
        return max(1, int(self._cfg("TASK_SUBTASK_POLL_CYCLES", 10)))

    def _poll_subtask(self, task: dict, idx: int, row: dict, gate_open: bool | None = None) -> str:
        """One poll of a subtask step's queue result (one peek per cycle).

        Returns:
          "done" — the step was consumed (passed / failed / expired) and the
                   caller advances to the next plan step;
          "hold" — the result is not ready yet; the caller yields the cycle so
                   the executor loop can re-check the gate and resume polling;
          "stop" — the subtask was aborted (chat) and the task is frozen.

        Budget approximation: the queue result carries no per-task exec
        duration, so ``now − step.started_at`` is the practical upper bound of
        the GPU work. ``TASK_SUBTASK_POLL_CYCLES`` bounds how many cycles a
        result may stay absent before a liveness check decides between "the
        worker is still on it" (keep waiting) and "the result expired" (fail).
        """
        task_id = task["id"]
        extra = dict(row.get("extra") or {})
        queue_id = extra.get("subtask_queue_id") or ""
        if not queue_id:
            # Defensive: a tagged row without a queue id cannot ever complete.
            self._end_step(task_id, row["id"], status="failed", last_error="subtask queue id missing")
            update_task(task_id, next_step_index=idx + 1)
            return "done"

        if row["status"] == "subtask_queued":
            # First poll flips the status; only stamp the start when absent
            # (an enqueued row may already carry a start for the budget clock).
            started = row.get("started_at") or _now_iso()
            update_step(row["id"], status="subtask_running", started_at=started)
            row = {**row, "status": "subtask_running", "started_at": started}
            # Announce the running state — the step's long GPU work otherwise
            # left the view silent until the result landed.
            self._publish_task_update(task_id)

        # Chat abort: the hook raised our cancel flag mid-subtask.
        if self._redis_flag(f"{CANCEL_KEY_PREFIX}{row['id']}"):
            return self._abort_subtask(task_id, idx, row, queue_id, reason="chat")

        # Gate closed mid-poll: chat took over — same interrupted path.
        if not self._gate_value(gate_open):
            return self._abort_subtask(task_id, idx, row, queue_id, reason="chat")

        stored = self._peek_subtask_result(queue_id)
        if stored is not None:
            return self._consume_subtask_result(task_id, idx, row, queue_id, stored)

        # No result yet: spend one poll from the cycle budget and keep waiting.
        polls = int(extra.get("subtask_polls") or 0) + 1
        extra["subtask_polls"] = polls
        update_step(row["id"], extra=extra)
        if polls < self._poll_budget_polls():
            return "hold"

        # Budget exhausted: a live worker still owns it (keep waiting); a gone
        # one means the result expired (fail the step and move on).
        if self._subtask_alive(queue_id):
            logger.info(f"task {task_id} step {idx + 1}: subtask {queue_id} still running — holding")
            return "hold"
        from app.llamacpp_client import _tr

        lang = task.get("lang") or "ru"
        self._end_step(
            task_id,
            row["id"],
            status="failed",
            exec_seconds=self._subtask_seconds(row),
            last_error=_tr("Result of subtask expired while task was paused. Please restart the task.", lang),
        )
        update_task(task_id, next_step_index=idx + 1)
        return "done"

    def _subtask_seconds(self, row: dict) -> float:
        """Wall-clock approximation of a subtask's exec time (now − started_at)."""
        started = row.get("started_at")
        if not started:
            return 0.0
        try:
            started_dt = datetime.fromisoformat(str(started))
            if started_dt.tzinfo is None:
                started_dt = started_dt.replace(tzinfo=UTC)
            return round(max(0.0, (datetime.now(UTC) - started_dt).total_seconds()), 2)
        except (TypeError, ValueError):
            return 0.0

    def _abort_subtask(self, task_id: str, idx: int, row: dict, queue_id: str, reason: str) -> str:
        """Cancel the queue task, mark the step interrupted, freeze the task."""
        try:
            _get_request_queue().cancel_task(queue_id)
        except Exception as e:
            logger.warning(f"subtask cancel failed ({queue_id}): {e}")
        elapsed = self._subtask_seconds(row)
        self._add_budget(task_id, elapsed)
        self._end_step(task_id, row["id"], status="interrupted", interrupt_reason=reason, exec_seconds=elapsed)
        update_task(task_id, status="frozen", abort_reason="chat")
        logger.info(f"task {task_id} step {idx + 1}: subtask aborted ({reason}) — frozen")
        return "stop"

    def _consume_subtask_result(self, task_id: str, idx: int, row: dict, queue_id: str, stored: dict) -> str:
        """A result envelope arrived: consume it into the step row.

        Error envelope (worker marked the task failed) ⇒ step failed (attempts
        NOT incremented — a queue failure is a tool error, spec §3), the task
        continues. Success ⇒ a normal checkpoint verifies the result against
        the step's checkpoint_criteria (spec §4), then the step passes with
        the media refs in extra["media"]; a rejected verification fails the
        step and the task continues.
        """
        task = get_task(task_id) or {}
        step = {
            "goal": row.get("goal", ""),
            "checkpoint_criteria": row.get("checkpoint_criteria", ""),
        }
        elapsed = self._subtask_seconds(row)
        self._add_budget(task_id, elapsed)
        result_data = stored.get("result") if isinstance(stored.get("result"), dict) else {}
        error_text = str(stored.get("error") or result_data.get("error") or "")
        is_error = stored.get("status") == "error" or bool(error_text)

        # Chat-style header media + meta (file refs, model name / type / token
        # usage) are collected BEFORE the error check: the envelope reports
        # which model produced the artifact, so even a step that FAILED (a
        # rejected checkpoint or a camera that answered but was then rejected)
        # keeps the image and its model/tokens in the conversation, exactly
        # like the chat header. The task view renders them regardless of the
        # step status.
        media: dict = {}
        for key in ("file_path", "file_name", "file_type", "file_size", "message_id"):
            value = stored.get(key) if key in stored else result_data.get(key)
            if value:
                media[key] = value
        meta: dict = {}
        for key in ("model_name", "model_type", "usage"):
            value = stored.get(key) if key in stored else result_data.get(key)
            if value:
                meta[key] = value
        merged_extra = {**(row.get("extra") or {}), "subtask_queue_id": queue_id, "media": media, **meta}

        if is_error:
            if media or meta:
                update_step(row["id"], extra=merged_extra)
            self._end_step(
                task_id,
                row["id"],
                status="failed",
                exec_seconds=elapsed,
                last_error=error_text[:500] or "subtask failed",
            )
            logger.warning(f"task {task_id} step {idx + 1}: subtask failed — continuing")
            update_task(task_id, next_step_index=idx + 1)
            return "done"

        response_text = str(stored.get("response") or result_data.get("response") or "")
        result_text = response_text or "done"
        if media.get("file_path"):
            # The verifier judges TEXT only: a media step whose artifact was
            # saved but never named in the checkpoint input got rejected with
            # «текстовая инструкция вместо файла» (live defect). Name the
            # artifact so the criteria "file exists" is decidable.
            from flask_babel import force_locale
            from flask_babel import gettext as _t

            with force_locale(task.get("lang") or "ru"):
                artifact_line = _t("Media artifact saved: {name} ({type}).").format(
                    name=str(media.get("file_name") or media["file_path"]),
                    type=str(media.get("file_type") or "file"),
                )
            result_text = result_text + "\n" + artifact_line
        # Spec §4: a subtask result is verified like any other step result —
        # a normal checkpoint runs against the step's checkpoint_criteria
        # (e.g. "file exists, non-empty, matches expectation"). A verifier
        # rejection fails the step (the task continues), matching the
        # attempts-exhausted semantics of a direct step.
        checkpoint: dict = {}
        if str(step.get("checkpoint_criteria", "") or "").strip():
            checkpoint = run_checkpoint(self.app, step, result_text, lang=task.get("lang") or "ru")
            self._add_budget(task_id, float(checkpoint.get("exec_seconds") or 0.0))
        if checkpoint and not checkpoint.get("verdict"):
            self._end_step(
                task_id,
                row["id"],
                status="failed",
                exec_seconds=elapsed,
                result=result_text,
                checkpoint_result=str(checkpoint.get("explanation", "")),
                checkpoint_verdict=False,
                extra=merged_extra,
            )
            logger.warning(f"task {task_id} step {idx + 1}: subtask checkpoint rejected — continuing")
            update_task(task_id, next_step_index=idx + 1)
            return "done"
        self._end_step(
            task_id,
            row["id"],
            status="passed",
            exec_seconds=elapsed,
            result=result_text,
            checkpoint_result=str(checkpoint.get("explanation", "")),
            checkpoint_verdict=bool(checkpoint.get("verdict")) if checkpoint else None,
        )
        update_step(row["id"], extra=merged_extra)
        logger.info(f"task {task_id} step {idx + 1}: subtask completed ({queue_id})")
        update_task(task_id, next_step_index=idx + 1)
        return "done"

    def _extract_facts_text(self, task: dict, text: str) -> None:
        """Feed the user's long-term memory from a task answer (Tasks ↔ SLM, v12.6).

        Mirrors the chat's post-answer background extraction (CPU-only,
        rule-based, no LLM, no GPU lock): a daemon thread calls the queue's
        ``_extract_facts_bg`` so the single executor thread never blocks on SLM.
        Error fallbacks (the localized ⚠️ notice) and short texts are not
        memories. Errors are logged, never fatal.
        """
        text = str(text or "")
        if not text.strip() or len(text) <= 20 or text.strip().startswith("⚠️"):
            return
        try:
            from app.queue import _extract_facts_bg

            threading.Thread(
                target=_extract_facts_bg,
                args=(
                    self.app,
                    task.get("title") or "",
                    text[:3000],
                    f"task:{task['id']}",
                    task.get("user_id") or "",
                    task.get("lang") or "ru",
                ),
                daemon=True,
            ).start()
        except Exception as e:
            logger.warning(f"task {task['id']}: fact extraction thread failed to start: {e}")

    def _maybe_extract_facts(self, task: dict, row: dict, result_text: str) -> None:
        """Extract facts from a passed llm step's Q&A only (chat parity).

        Tool-produced fragments (search/rag/calculate) are data, not user facts.
        """
        if str(row.get("tool") or "llm") != "llm":
            return
        self._extract_facts_text(task, result_text)

    def _finalize(self, task: dict) -> None:
        """Plan exhausted → the final answer over the accumulated results.

        Order (M3): generate → persist the final step → terminal status, so a
        crash between steps cannot lose the summary.
        """
        task_id = task["id"]
        lang = task.get("lang") or "ru"
        final_meta: dict[str, Any] = {}
        text = _run_final_summary(self.app, task, meta_out=final_meta)
        if not text:
            from app.llamacpp_client import _tr

            text = f"⚠️ {_tr(_FALLBACK_NO_FINAL, lang=lang)}"
        # The final summary must not repeat the last step's answer verbatim
        # (a one-step task's summary IS that answer — the user saw it twice).
        last_step = _last_meaningful_step(task_id)
        if last_step and _results_match(text, str(last_step.get("result") or "")):
            text = str(last_step.get("result") or "")
            final_meta = dict(last_step.get("extra") or {})  # reuse the step's header meta
        self._clear_running_step(task_id)
        add_final_step(task_id, text, meta=final_meta)
        self._extract_facts_text(task, text)
        update_task(task_id, status="completed", completed_at=_now_iso())
        logger.info(f"task {task_id} completed")
        self._publish_task_update(task_id)

    def _budget_expired(self, task: dict) -> None:
        """accumulated >= budget ⇒ expired + final summary from the results so far.

        Order (M3): summary persisted BEFORE the terminal status write.
        """
        task_id = task["id"]
        lang = task.get("lang") or "ru"
        final_meta: dict[str, Any] = {}
        text = _run_final_summary(self.app, task, meta_out=final_meta)
        if not text:
            from app.llamacpp_client import _tr

            text = f"⚠️ {_tr(_FALLBACK_BUDGET_NO_SUMMARY, lang=lang)}"
        self._clear_running_step(task_id)
        add_final_step(task_id, text, meta=final_meta)
        self._extract_facts_text(task, text)
        update_task(task_id, status="expired", abort_reason="budget_expired", completed_at=_now_iso())
        logger.info(f"task {task_id} expired (budget) — final summary recorded")
        self._publish_task_update(task_id)

    def _cancel_task(self, task: dict) -> None:
        """User abort (API stop, Task 9): running step marked, terminal cancelled."""
        task_id = task["id"]
        running = get_running_step(task_id)
        if running:
            self._current_step_id = None
            update_step(
                running["id"],
                status="interrupted",
                interrupt_reason="user_cancel",
                finished_at=_now_iso(),
            )
        self._clear_running_step(task_id)
        update_task(task_id, status="cancelled", completed_at=_now_iso())
        logger.info(f"task {task_id} cancelled by user")
        self._publish_task_update(task_id)


# ── Module-level singleton (same pattern as the resource manager) ───────

_EXECUTOR: TaskExecutor | None = None
_executor_lock = threading.Lock()


def get_task_executor(app: Flask | None = None) -> TaskExecutor:
    """Module-level singleton accessor (app required on the first call)."""
    global _EXECUTOR
    with _executor_lock:
        if _EXECUTOR is None:
            if app is None:
                raise RuntimeError("get_task_executor(app) requires the app on first call")
            _EXECUTOR = TaskExecutor(app=app)
        return _EXECUTOR


def stop_task_executor(timeout: float = 3.0) -> None:
    """Stop the singleton executor if one is running (graceful shutdown / tests)."""
    with _executor_lock:
        executor = _EXECUTOR
    if executor is not None:
        executor.stop(timeout=timeout)
