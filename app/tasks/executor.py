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

import json
import logging
import re
import threading
import time
from datetime import UTC, datetime
from typing import Any

from flask import Flask

from app.database import get_db
from app.tasks.gate import is_gate_open
from app.tasks.runner import StepAborted, run_direct_step
from app.tasks.store import add_step, get_step, get_task, update_step, update_task

logger = logging.getLogger(__name__)

# ── Config fallbacks (real values are read from app.config at runtime) ──
TASK_POLL_INTERVAL_S = 1.0
TASK_TOTAL_BUDGET_S = 86400
TASK_STEP_MAX_ATTEMPTS = 3
TASK_QUIET_PERIOD_S = 60

# Test-visible seed for an expired budget (tests/test_tasks_executor.py).
_BUDGET_CFG_FALLBACK = {"TASK_TOTAL_BUDGET_S": TASK_TOTAL_BUDGET_S}

CANCEL_KEY_PREFIX = "task:step_cancel:"
RUNNING_STEP_KEY_PREFIX = "task:running_step:"
RUNNING_STEPS_SET = "task:active_running_steps"
DEFERRED_SUBTASKS_KEY = "task:deferred_subtasks"
# Safety TTL on step-cancel flags so a lost consumer cannot leak a flag.
CANCEL_FLAG_TTL_S = 3600

# Direct tools run in-process (runner); everything else goes through the queue.
_SUBTASK_TOOLS = {"rlm", "image_gen", "image_edit", "video_gen"}
# Queue task_type per subtask tool (Task 8 refines the exact payloads).
_SUBTASK_TYPE = {"image_gen": "image", "image_edit": "image_edit", "video_gen": "video", "rlm": "rlm_analysis"}

# Localized user-facing fallbacks (⚠️ prefix per the project error rule).
_MSG_PLAN_TEMPLATE_UNAVAILABLE = "Task could not be planned: the plan template is unavailable."
_MSG_PLAN_UNREADABLE = "Task could not be planned: the planner returned an unreadable plan. Please try again."
_FALLBACK_NO_FINAL = "The final answer could not be generated; please check the step results above."
_FALLBACK_BUDGET_NO_SUMMARY = "Execution budget exhausted; the summary could not be generated."


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


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
    update_step(step_id, result=answer)
    return step_id


def add_final_step(task_id: str, result_text: str, status: str = "passed") -> str:
    """Append the closing ``final`` step row (answer, error note or summary).

    Idempotent: a task may carry at most one ``final`` step — when one already
    exists it is updated in place instead of being duplicated.
    """
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
        update_step(existing["id"], result=result_text, status=status, finished_at=_now_iso())
        return existing["id"]
    step_id = add_step(task_id=task_id, seq=max_seq + 1, kind="final")
    update_step(step_id, result=result_text, status=status, finished_at=_now_iso())
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


def _capabilities_section() -> str:
    """Render the capabilities registry for the planner prompt."""
    from app.tasks.tools import get_capabilities

    lines = []
    for cap in get_capabilities():
        lines.append(f"- {cap['id']} ({cap['label']}): {cap['description']} Checkpoint hint: {cap['checkpoint_hint']}")
    return "\n".join(lines)


def _response_language_name(lang: str) -> str:
    try:
        from modules.base import response_language_name

        return response_language_name(lang)
    except Exception:
        return "Russian" if lang == "ru" else "English"


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


def _run_planner(app: Flask, prompt: str, cancel_check, lang: str) -> str:
    """One reasoning-model call for the plan phase.

    Uses the runner's private streaming helper on purpose (the brief allows
    importing it): the reasoning model is JIT-loaded exactly as the workers
    do, and tests patch this wrapper to isolate the LLM.
    """
    from app.tasks import runner

    return runner._call_reasoning(app, prompt, cancel_check, lang=lang, temperature=0.2)


def _run_final_summary(app: Flask, task: dict) -> str | None:
    """Reasoning call over the accumulated results (task_final.template).

    Returns the cleaned final text, or None when generation fails (the caller
    then records a non-LLM fallback note).
    """
    from app.utils import format_prompt

    lang = task.get("lang") or "ru"
    plan = task.get("step_plan") or {}
    params = {
        "task_request": (plan.get("task_request") if isinstance(plan, dict) else None) or task.get("title") or "",
        "plan": _plan_text(plan),
        "results_digest": _results_digest(task["id"]),
        "response_language": _response_language_name(lang),
        "response_style": _style_instruction(lang, task.get("response_style") or "neutral"),
    }
    try:
        with app.app_context():
            prompt = format_prompt("task_final.template", params, lang=lang)
        if not prompt:
            raise RuntimeError("task_final prompt template unavailable")
        raw = _run_planner(app, prompt, lambda: False, lang)
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

        # Selection loop. Chained only while the slot keeps being RELEASED
        # (awaiting_user) — an active/frozen task holds the slot, a terminal
        # task waits for the next poll. ``seen`` guards against a task that
        # stays awaiting (answer still missing) being re-picked forever.
        seen: set[str] = set()
        while True:
            pick = next((t for t in get_in_progress_tasks() if t["id"] not in seen), None)
            if pick is not None:
                seen.add(pick["id"])
                self._run_task(pick, gate_open=gate_open)
                current = get_task(pick["id"])
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
                question_seq = int(question["seq"])
                if question["status"] in ("pending", "awaiting_user"):
                    update_step(parent_id, status="passed")
        if question_seq is None:
            # No linked question row — advance past the current pointer.
            task = get_task(task_id) or {}
            question_seq = int(task.get("next_step_index") or 0) + 1
        update_step(ui["id"], status="passed")
        self._preempt_running_task(task_id)
        # The question sits at plan position question_seq-1 ⇒ resume at question_seq.
        update_task(task_id, status="active", next_step_index=question_seq)

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
            # Resuming after a gate re-open: back to active before stepping.
            update_task(task_id, status="active")
            task["status"] = "active"

        plan = task.get("step_plan") or {}
        if not (plan.get("steps") if isinstance(plan, dict) else plan):
            # PLAN PHASE: decompose the request before any step can run.
            if self._run_plan_phase(task) != "ok":
                return
            task = get_task(task_id) or task

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
            if str(step.get("tool", "llm")) in _SUBTASK_TOOLS:
                self._enqueue_subtask(task, idx, step)
                return  # loop breaks — completion polling lands with Task 8
            if self._execute_direct(task, idx, step) == "stop":
                return

    # ── Plan phase ────────────────────────────────────────────────────

    def _run_plan_phase(self, task: dict) -> str:
        """Planner call → parse → step rows. Returns "ok" or "failed"."""
        task_id = task["id"]
        lang = task.get("lang") or "ru"
        params = {
            "task_request": task.get("title") or "",
            "capabilities_section": _capabilities_section(),
            "response_language": _response_language_name(lang),
        }
        from app.utils import format_prompt

        with self.app.app_context():
            prompt = format_prompt("task_plan.template", params, lang=lang)
        if not prompt:
            self._fail_task(task_id, lang, _MSG_PLAN_TEMPLATE_UNAVAILABLE)
            return "failed"

        plan = _parse_plan(_run_planner(self.app, prompt, lambda: False, lang))
        if plan is None:
            # One retry before giving up (transient planner noise).
            plan = _parse_plan(_run_planner(self.app, prompt, lambda: False, lang))
        if plan is None:
            self._fail_task(task_id, lang, _MSG_PLAN_UNREADABLE)
            return "failed"

        # Keep the user's original request for the step/final prompts.
        plan["task_request"] = task.get("title") or ""
        fields: dict[str, Any] = {"step_plan": plan, "next_step_index": 0}
        if plan.get("title"):
            fields["title"] = plan["title"]
        update_task(task_id, **fields)
        self._create_plan_step_rows(task_id, plan["steps"])
        return "ok"

    def _create_plan_step_rows(self, task_id: str, plan_steps: list[dict]) -> None:
        """Create one step row per plan position (kind per capability class)."""
        for pos, s in enumerate(plan_steps or []):
            kind = self._step_kind(s)
            add_step(
                task_id=task_id,
                seq=pos + 1,
                kind=kind,
                goal=str(s.get("goal", "")),
                tool=str(s.get("tool", "")),
                checkpoint_criteria=str(s.get("checkpoint_criteria", "")),
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
                update_step(
                    row["id"],
                    status="failed",
                    last_error="step attempts exhausted",
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
            checkpoint = outcome.get("checkpoint") or {}
            self._add_budget(task_id, elapsed + float(checkpoint.get("exec_seconds") or 0.0))

            status = outcome.get("status")
            result = outcome.get("result") or ""
            if status == "passed":
                self._end_step(
                    task_id,
                    row["id"],
                    status="passed",
                    result=result,
                    exec_seconds=elapsed,
                    checkpoint_result=str(checkpoint.get("explanation", "")),
                    checkpoint_verdict=bool(checkpoint.get("verdict")),
                )
                update_task(task_id, next_step_index=idx + 1)
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
                )
                update_task(task_id, next_step_index=idx + 1)
                return "continue"

            # status == "failed": the checkpoint rejected the result — corrective
            # retry of the SAME step with the mismatch fed back into the next
            # attempt's context digest (I2).
            self._end_step(
                task_id,
                row["id"],
                status="failed",
                result=result,
                exec_seconds=elapsed,
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
        """The user's original request (kept in the plan payload at plan phase)."""
        plan = task.get("step_plan") or {}
        if isinstance(plan, dict):
            return str(plan.get("task_request") or task.get("title") or "")
        return str(task.get("title") or "")

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
        update_step(row["id"], status="awaiting_user", result=str(step.get("goal", "")))
        update_task(task_id, status="awaiting_user")
        logger.info(f"task {task_id} awaiting user input at step {idx + 1}")

    def _enqueue_subtask(self, task: dict, idx: int, step: dict) -> None:
        """Media/RLM subtask → the existing request queue with the task tag.

        Task 8 adds completion polling; the step loop breaks after enqueueing.
        """
        task_id = task["id"]
        tool = str(step.get("tool", ""))
        row = self._step_row_for(task_id, idx, step)
        task_type = _SUBTASK_TYPE.get(tool, "image")
        try:
            queue = _get_request_queue()
            subtask_queue_id, _info = queue.add_request(
                user_id=task.get("user_id") or "",
                session_id=f"task:{task_id}",
                request_data={
                    "type": task_type,
                    "prompt": str(step.get("goal", "")),
                    # Gate hook excludes tagged requests from chat activity.
                    "task_step_subtask": row["id"],
                },
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
        update_step(row["id"], status="subtask_queued", extra={"subtask_queue_id": subtask_queue_id})
        logger.info(f"task {task_id} step {idx + 1} ({tool}) queued as subtask {subtask_queue_id}")

    def _finalize(self, task: dict) -> None:
        """Plan exhausted → the final answer over the accumulated results.

        Order (M3): generate → persist the final step → terminal status, so a
        crash between steps cannot lose the summary.
        """
        task_id = task["id"]
        lang = task.get("lang") or "ru"
        text = _run_final_summary(self.app, task)
        if not text:
            from app.llamacpp_client import _tr

            text = f"⚠️ {_tr(_FALLBACK_NO_FINAL, lang=lang)}"
        self._clear_running_step(task_id)
        add_final_step(task_id, text)
        update_task(task_id, status="completed", completed_at=_now_iso())
        logger.info(f"task {task_id} completed")

    def _budget_expired(self, task: dict) -> None:
        """accumulated >= budget ⇒ expired + final summary from the results so far.

        Order (M3): summary persisted BEFORE the terminal status write.
        """
        task_id = task["id"]
        lang = task.get("lang") or "ru"
        text = _run_final_summary(self.app, task)
        if not text:
            from app.llamacpp_client import _tr

            text = f"⚠️ {_tr(_FALLBACK_BUDGET_NO_SUMMARY, lang=lang)}"
        self._clear_running_step(task_id)
        add_final_step(task_id, text)
        update_task(task_id, status="expired", abort_reason="budget_expired", completed_at=_now_iso())
        logger.info(f"task {task_id} expired (budget) — final summary recorded")

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
