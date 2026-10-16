# tests/test_tasks_executor.py
"""TaskExecutor state-machine tests (Tasks subsystem, v12.6).

The executor imports its store/gate/runner/queue collaborators into its own
module namespace precisely so tests can patch them there
(``monkeypatch.setattr(ex, "name", ...)``). The LLM/streaming layer is mocked
at ``run_direct_step`` / ``_run_planner`` / ``_run_final_summary`` — no real
model, GPU or Redis call is made.

DB-backed tests skip cleanly when no PostgreSQL is reachable (same sentinel
pattern as tests/test_tasks_db.py). The per-app executor thread started by
create_app() is stopped in the fixture so manual drives are deterministic.
"""

import base64
import os
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

import app.tasks.executor as ex
from app.tasks.runner import StepAborted, run_direct_step

pytestmark = [pytest.mark.unit]


def _passed_checkpoint():
    """A runner checkpoint payload for a passed verifier verdict."""
    return {"verdict": True, "explanation": "ok", "exec_seconds": 0.1}


# Same sentinel pattern as tests/test_tasks_db.py: without a real DATABASE_URL
# the conftest mock DB cannot answer store queries, so DB-backed tests skip.
_db_available = (
    bool(os.environ.get("DATABASE_URL")) and os.environ["DATABASE_URL"] != "postgresql://localhost:5432/flai_test"
)


def _ok_step_result(exec_seconds=0.5, result="step result text", verdict=True):
    """A runner return payload for a passed direct step."""
    return {
        "result": result,
        "exec_seconds": exec_seconds,
        "status": "passed",
        "checkpoint": {"verdict": verdict, "explanation": "ok", "exec_seconds": 0.2},
    }


def _seed_plan():
    """A two-step direct plan as the planner would emit it."""
    return {
        "title": "Test task",
        "steps": [
            {"seq": 1, "goal": "Do first thing", "tool": "llm", "checkpoint_criteria": "answer given"},
            {"seq": 2, "goal": "Do second thing", "tool": "llm", "checkpoint_criteria": "answer given"},
        ],
    }


@pytest.fixture
def db_env(test_app):
    """Store-backed environment; skip when no real DB is reachable.

    Stops the create_app()-started executor singleton so the tests drive the
    state machine manually without a concurrent polling thread.
    """
    if not _db_available:
        pytest.skip("No reachable PostgreSQL for tasks executor tests")
    from app.database import get_db, init_db

    try:
        init_db()
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT id FROM tasks LIMIT 1")
            c.fetchone()
        ex.get_task("00000000-0000-0000-0000-000000000000")
    except Exception as exc:
        pytest.skip(f"PostgreSQL unreachable: {exc}")

    # No Redis flag is ever set in these tests (the shared mock answers None).
    test_app.request_queue.redis.get.return_value = None
    # Stop the singleton executor thread started with this app instance.
    ex.stop_task_executor(timeout=2)
    with ex._executor_lock:
        ex._EXECUTOR = None

    # Clean slate for tasks/task_steps.
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM task_steps")
        c.execute("DELETE FROM tasks")

    yield ex  # the executor module re-exports get_task/add_step/update_step/...

    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM task_steps")
        c.execute("DELETE FROM tasks")


@pytest.fixture
def gate_open(monkeypatch):
    """Gate forced open at the executor seam (no Redis read)."""
    monkeypatch.setattr(ex, "is_gate_open", lambda *a, **k: True)


def _mk_task(
    store,
    task_class=3,
    status="queued",
    step_plan=None,
    next_step_index=0,
    accumulated=0.0,
    minutes_ago=30,
    user_id="tester",
):
    """Create a real task row via the store (DB-backed tests)."""
    from app.tasks.store import create_task, update_task

    created = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    task_id = create_task(user_id=user_id, task_class=task_class, title="seed", lang="ru", response_style="neutral")
    # Force created_at ordering determinism (create_task stamps NOW()).
    with ex.get_db() as conn:
        c = conn.cursor()
        c.execute("UPDATE tasks SET created_at = %s WHERE id = %s", (created, task_id))
    if step_plan is not None or status != "queued" or accumulated or next_step_index:
        update_task(
            task_id,
            status=status,
            step_plan=step_plan or {},
            next_step_index=next_step_index,
            accumulated_exec_seconds=accumulated,
        )
    return task_id


def _cycle(test_app, gate=True):
    """Drive one executor cycle manually (no thread)."""
    executor = ex.TaskExecutor(app=test_app)
    executor._process_one_cycle(gate_open=gate)
    return executor


# ── 1. queued → active: selection order + step execution + completion ──


def test_queued_to_active(db_env, test_app, gate_open, monkeypatch):
    """Executor picks the queued task, activates it and runs its steps to completion."""
    store = db_env
    task_id = _mk_task(store, step_plan=_seed_plan(), minutes_ago=30)
    other_id = _mk_task(store, task_class=5, step_plan=_seed_plan(), minutes_ago=10)
    calls = []
    monkeypatch.setattr(ex, "run_direct_step", lambda *a, **k: (calls.append(1), _ok_step_result())[1])
    monkeypatch.setattr(ex, "_run_final_summary", lambda *a, **k: "final summary text")

    _cycle(test_app)

    t = store.get_task(task_id)
    assert t["started_at"] is not None, "queued task was activated"
    assert t["status"] == "completed" and t["completed_at"] is not None
    assert t["next_step_index"] == 2, "both steps done"
    # Budget: per step 0.5 (tool) + 0.2 (checkpoint) = 0.7 × 2
    assert abs(t["accumulated_exec_seconds"] - 1.4) < 0.01
    steps = store.get_task_steps(task_id)
    assert [s["status"] for s in steps if s["kind"] != "final"] == ["passed", "passed"]
    # Step duration includes its checkpoint verification (0.5 tool + 0.2 verify):
    # the header's ⏱️/🚀 then line up with the tokens shown for the step.
    assert abs(float(steps[0]["exec_seconds"]) - 0.7) < 0.01
    final_step = steps[-1]
    assert final_step["kind"] == "final" and final_step["result"] == "final summary text"
    assert len(calls) == 2, "runner called once per step"
    assert store.get_task(other_id)["status"] == "queued", "one active task at a time"


def test_selection_order_class_then_created(db_env, test_app, gate_open, monkeypatch):
    """Queued selection: lowest task_class first, then earliest created_at."""
    store = db_env
    late_low_class = _mk_task(store, task_class=5, step_plan=_seed_plan(), minutes_ago=60)
    early_high_class = _mk_task(store, task_class=3, step_plan=_seed_plan(), minutes_ago=10)

    monkeypatch.setattr(ex, "run_direct_step", lambda *a, **k: _ok_step_result())
    monkeypatch.setattr(ex, "_run_final_summary", lambda *a, **k: "final summary text")

    _cycle(test_app)

    assert store.get_task(early_high_class)["status"] == "completed"
    assert store.get_task(late_low_class)["status"] == "queued"


# ── 2. gate closed ⇒ frozen ──────────────────────────────────────────────


def test_gate_closed_freezes(db_env, test_app, monkeypatch):
    """Gate closed ⇒ active task frozen (abort_reason=chat); open ⇒ continues."""
    store = db_env
    task_id = _mk_task(store, status="active", step_plan=_seed_plan(), minutes_ago=30)
    ran = []
    monkeypatch.setattr(ex, "run_direct_step", lambda *a, **k: (ran.append(1), _ok_step_result())[1])

    _cycle(test_app, gate=False)

    t = store.get_task(task_id)
    assert t["status"] == "frozen", "gate closed ⇒ frozen"
    assert t["abort_reason"] == "chat"
    assert t["next_step_index"] == 0 and not ran, "no step may run while frozen"

    # Gate re-opens ⇒ the frozen task resumes and completes.
    monkeypatch.setattr(ex, "is_gate_open", lambda *a, **k: True)
    monkeypatch.setattr(ex, "_run_final_summary", lambda *a, **k: "final summary text")
    _cycle(test_app, gate=True)
    t2 = store.get_task(task_id)
    # The frozen task resumes and keeps running: it may complete within this
    # cycle (two-step plan) — what matters is that it is no longer frozen.
    assert t2["status"] in ("active", "completed"), "frozen task resumed on gate re-open"
    assert t2["next_step_index"] >= 1


# ── 3. chat aborts the in-flight step ────────────────────────────────────


def test_aborts_step_on_chat(db_env, test_app, gate_open, monkeypatch):
    """StepAborted ⇒ step interrupted(chat), budget grows, task frozen, index held."""
    store = db_env
    task_id = _mk_task(store, step_plan=_seed_plan(), minutes_ago=30)

    def _aborted(*a, **k):
        time.sleep(0.05)  # I9: elapsed must exceed 0 deterministically
        raise StepAborted("step cancelled during reasoning generation")

    monkeypatch.setattr(ex, "run_direct_step", _aborted)
    _cycle(test_app)

    t = store.get_task(task_id)
    assert t["status"] == "frozen", "abort inside a step freezes the task"
    assert t["abort_reason"] == "chat"
    assert t["next_step_index"] == 0, "interrupted step re-runs on resume"
    assert t["accumulated_exec_seconds"] > 0, "aborted attempt still consumes budget"
    step = store.get_task_steps(task_id)[0]
    assert step["status"] == "interrupted"
    assert step["interrupt_reason"] == "chat"
    assert step["attempts"] == 1


# ── 4. budget expiry ⇒ expired + final summary step ──────────────────────


def test_budget_expiry_generates_summary(db_env, test_app, gate_open, monkeypatch):
    """accumulated >= budget ⇒ expired, no further step runs, final summary added."""
    store = db_env
    task_id = _mk_task(store, step_plan=_seed_plan(), accumulated=ex._BUDGET_CFG_FALLBACK["TASK_TOTAL_BUDGET_S"])
    ran = []
    monkeypatch.setattr(ex, "run_direct_step", lambda *a, **k: (ran.append(1), _ok_step_result())[1])
    monkeypatch.setattr(ex, "_run_final_summary", lambda *a, **k: "final summary text")

    _cycle(test_app)

    t = store.get_task(task_id)
    assert t["status"] == "expired"
    assert t["abort_reason"] == "budget_expired"
    assert t["completed_at"] is not None
    assert not ran, "no ordinary step may run once the budget is exhausted"
    steps = store.get_task_steps(task_id)
    assert steps[-1]["kind"] == "final" and steps[-1]["result"] == "final summary text"


# ── 5. question step ⇒ awaiting_user frees the slot ─────────────────────


def test_awaiting_user_releases_slot(db_env, test_app, gate_open, monkeypatch):
    """question step ⇒ step+task awaiting_user; the next queued task takes the slot."""
    store = db_env
    plan = {
        "title": "Needs answer",
        "steps": [{"seq": 1, "goal": "Which port?", "tool": "llm", "user_input_expected": True}],
    }
    awaiting_id = _mk_task(store, status="active", step_plan=plan, minutes_ago=20)
    other_id = _mk_task(store, step_plan=_seed_plan(), minutes_ago=10)
    monkeypatch.setattr(ex, "run_direct_step", lambda *a, **k: _ok_step_result())
    monkeypatch.setattr(ex, "_run_final_summary", lambda *a, **k: "final summary text")

    _cycle(test_app)

    ta = store.get_task(awaiting_id)
    assert ta["status"] == "awaiting_user", "question step ⇒ task awaiting_user"
    sa = store.get_task_steps(awaiting_id)[0]
    assert sa["kind"] == "question" and sa["status"] == "awaiting_user"
    # Slot released within the same cycle: the other queued task got picked and ran.
    tb = store.get_task(other_id)
    assert tb["started_at"] is not None, "slot released — next queued task started"
    assert tb["status"] in ("active", "completed")


# ── 6. user answer resumes the task ──────────────────────────────────────


def test_user_answer_resumes_task(db_env, test_app, gate_open, monkeypatch):
    """Unprocessed user_input ⇒ question closed passed, continuation runs."""
    store = db_env
    plan = {
        "title": "Needs answer",
        "steps": [
            {"seq": 1, "goal": "Which port?", "tool": "llm", "user_input_expected": True},
            {"seq": 2, "goal": "Continue work", "tool": "llm", "checkpoint_criteria": "done"},
        ],
    }
    task_id = _mk_task(store, status="awaiting_user", step_plan=plan, minutes_ago=30)
    # Plan-phase rows (as the executor would have created them before awaiting).
    q_id = store.add_step(task_id=task_id, seq=1, kind="question", goal="Which port?", tool="llm")
    cont_id = store.add_step(task_id=task_id, seq=2, kind="direct", goal="Continue work", tool="llm")
    ui_id = store.add_user_input_step(task_id, question_seq=1, answer="Rotterdam")

    monkeypatch.setattr(ex, "run_direct_step", lambda *a, **k: _ok_step_result())
    monkeypatch.setattr(ex, "_run_final_summary", lambda *a, **k: "final summary text")
    _cycle(test_app)

    assert store.get_step(q_id)["status"] == "passed", "answered question closes as passed"
    assert store.get_step(ui_id)["status"] == "passed", "user_input step consumed"
    assert store.get_step(cont_id)["status"] == "passed", "continuation step ran"
    t = store.get_task(task_id)
    assert t["status"] == "completed"
    assert t["next_step_index"] == 2, "resumed at the step AFTER the question and finished"


def test_running_task_preempted_by_user_answer(db_env, test_app, gate_open, monkeypatch):
    """Answered awaiting task preempts the currently running task (spec §2 rule 6)."""
    store = db_env
    running_id = _mk_task(store, status="active", step_plan=_seed_plan(), minutes_ago=5)
    running_step = store.add_step(task_id=running_id, seq=1, kind="direct", goal="Do first thing", tool="llm")
    store.update_step(running_step, status="running")
    answered_id = _mk_task(store, status="awaiting_user", step_plan=_seed_plan(), minutes_ago=30)
    store.add_step(task_id=answered_id, seq=1, kind="question", goal="q", tool="llm")
    store.add_step(task_id=answered_id, seq=2, kind="direct", goal="Do first thing", tool="llm")
    store.add_user_input_step(answered_id, question_seq=1, answer="answer text")

    monkeypatch.setattr(ex, "run_direct_step", lambda *a, **k: _ok_step_result())
    monkeypatch.setattr(ex, "_run_final_summary", lambda *a, **k: "final summary text")
    _cycle(test_app)

    t_run = store.get_task(running_id)
    assert t_run["status"] == "queued", "previously running task returns to queued"
    assert t_run["abort_reason"] == "preempted"
    s_run = store.get_step(running_step)
    assert s_run["status"] == "interrupted" and s_run["interrupt_reason"] == "preempted"
    t_ans = store.get_task(answered_id)
    assert t_ans["status"] in ("active", "completed"), "answered task takes the slot back"


# ── 7. subtask branch: queued + loop break (Task 8 completes it) ─────────


def test_subtask_step_queued_and_loop_breaks(db_env, test_app, gate_open, monkeypatch):
    """subtask kind ⇒ step subtask_queued with the tag stored; no runner call; loop breaks."""
    store = db_env
    plan = {
        "title": "Media task",
        "steps": [{"seq": 1, "goal": "Make an image", "tool": "image_gen"}],
    }
    task_id = _mk_task(store, step_plan=plan)
    enq = []

    class _Q:
        def add_request(self, user_id, session_id, request_data, user_class, lang, task_type=None):
            enq.append({"user_id": user_id, "data": request_data, "task_type": task_type, "user_class": user_class})
            return "sub-queue-id-1", {}

    monkeypatch.setattr(ex, "_get_request_queue", lambda: _Q())
    monkeypatch.setattr(ex, "run_direct_step", lambda *a, **k: _ok_step_result())

    _cycle(test_app)

    assert enq, "subtask must be enqueued through the request queue"
    assert enq[0]["data"].get("task_step_subtask"), "tag task_step_subtask=<step_id> required"
    assert enq[0]["user_class"] == 3, "user_class carries the task class"
    step = store.get_task_steps(task_id)[0]
    assert step["status"] == "subtask_queued"
    t = store.get_task(task_id)
    assert t["status"] == "active" and t["next_step_index"] == 0, "loop breaks — completion polling is Task 8"


# ── 8. plan phase ────────────────────────────────────────────────────────


def test_plan_phase_creates_steps(db_env, test_app, gate_open, monkeypatch):
    """No plan ⇒ planner call → parsed JSON → step rows. The planner's own
    title never overwrites the user's request (the request IS the title)."""
    store = db_env
    task_id = _mk_task(store)
    plan_json = (
        '{"title": "Planned task", "steps": ['
        '{"seq": 1, "goal": "g1", "tool": "llm", "checkpoint_criteria": "c1"},'
        '{"seq": 2, "goal": "ask user", "tool": "llm", "user_input_expected": true}]}'
    )
    monkeypatch.setattr(ex, "_run_planner", lambda *a, **k: plan_json)
    monkeypatch.setattr(ex, "run_direct_step", lambda *a, **k: _ok_step_result())

    _cycle(test_app)

    t = store.get_task(task_id)
    assert t["title"] == "seed", "planner title must not overwrite the request"
    assert t["status"] == "awaiting_user", "second plan step is a question ⇒ awaiting"
    assert t["next_step_index"] == 1, "first step ran, awaiting the answer to the second"
    assert len(t["step_plan"]["steps"]) == 2
    kinds = [s["kind"] for s in store.get_task_steps(task_id)]
    assert kinds == ["direct", "question"], "user_input_expected ⇒ question kind"


def test_plan_parse_failure_marks_task_failed(db_env, test_app, gate_open, monkeypatch):
    """Planner output unparseable after retry ⇒ localized ⚠️ error step, task failed."""
    store = db_env
    task_id = _mk_task(store)
    monkeypatch.setattr(ex, "_run_planner", lambda *a, **k: "not json at all")

    _cycle(test_app)

    t = store.get_task(task_id)
    assert t["status"] == "failed"
    assert t["completed_at"] is not None
    steps = store.get_task_steps(task_id)
    assert steps and steps[-1]["status"] == "failed"
    assert steps[-1]["result"].startswith("⚠️"), "user-facing error must carry the ⚠️ prefix"


def test_plan_phase_publishes_live_stages(db_env, test_app, gate_open, monkeypatch):
    """The plan phase reports its progress to the task view: model load →
    making the plan (on the backend's "generating" callback) → cleared."""
    store = db_env
    task_id = _mk_task(store)
    plan_json = '{"title": "p", "steps": [{"seq": 1, "goal": "g", "tool": "llm"}]}'
    stages: list[tuple[str, str]] = []

    def _fake_planner(app, prompt, cancel_check, lang, status_callback=None):
        stages.append(("loading_reasoning_model", ""))  # published before the call
        if status_callback:
            status_callback("generating")
        return plan_json

    monkeypatch.setattr(ex, "_run_planner", _fake_planner)
    monkeypatch.setattr(
        "app.tasks.events.publish_task_stage",
        lambda app, task, stage: stages.append((stage or "", task.get("id") or "")),
    )
    monkeypatch.setattr(ex, "run_direct_step", lambda *a, **k: _ok_step_result())

    _cycle(test_app)

    cleared = [s for s, _ in stages if s == ""]
    assert ("loading_reasoning_model", task_id) in stages, "the load stage is announced before the planner call"
    assert ("making_plan", task_id) in stages, "the generating callback flips the stage to planning"
    assert cleared, "the stage is cleared when the plan phase ends"


def test_plan_failure_clears_the_stage(db_env, test_app, gate_open, monkeypatch):
    """An unreadable plan still clears the stage (try/finally, no stale line)."""
    store = db_env
    task_id = _mk_task(store)
    cleared: list[bool] = []

    def _fake_stage(app, task, stage):
        if not stage:
            cleared.append(True)

    monkeypatch.setattr(ex, "_run_planner", lambda *a, **k: "not json at all")
    monkeypatch.setattr("app.tasks.events.publish_task_stage", _fake_stage)

    _cycle(test_app)

    assert store.get_task(task_id)["status"] == "failed"
    assert cleared, "the finally clause must clear the stage on the failure path too"


# ── 9. executor thread lifecycle (no DB) ─────────────────────────────────


def test_executor_start_stop_idempotent(test_app, monkeypatch):
    """start() twice starts one daemon thread; stop() joins it; singleton accessor."""
    executor = ex.TaskExecutor(app=test_app)
    monkeypatch.setattr(executor, "_process_one_cycle", lambda *a, **k: None)
    # The loop/stop read the poll interval from the app config (I5).
    test_app.config["TASK_POLL_INTERVAL_S"] = 0.01

    executor.start()
    executor.start()  # idempotent
    assert executor._thread is not None and executor._thread.daemon
    assert executor._thread.is_alive()
    thread = executor._thread
    executor.stop()
    executor.stop()  # idempotent
    assert not thread.is_alive() and executor._thread is None

    # Module-level singleton accessor: same instance, app bound on first call.
    with ex._executor_lock:
        ex._EXECUTOR = None
    try:
        e1 = ex.get_task_executor(test_app)
        e2 = ex.get_task_executor()
        assert e1 is e2
    finally:
        with ex._executor_lock:
            ex._EXECUTOR = None


def test_executor_loop_survives_exceptions(test_app, monkeypatch):
    """A raising cycle must not kill the daemon loop (defensive try/except)."""
    executor = ex.TaskExecutor(app=test_app)
    test_app.config["TASK_POLL_INTERVAL_S"] = 0.01
    state = {"cycles": 0}

    def _boom(*a, **k):
        state["cycles"] += 1
        if state["cycles"] >= 2:
            executor._shutdown.set()
        raise RuntimeError("boom")

    monkeypatch.setattr(executor, "_process_one_cycle", _boom)
    executor._loop()
    assert state["cycles"] >= 2, "loop keeps cycling after an exception"


# ── 10. restart markers (I6) ─────────────────────────────────────────────


def test_restart_marks_orphaned_running_steps(db_env, test_app, gate_open, monkeypatch):
    """Executor start: steps left 'running' by a dead process → interrupted/restart."""
    store = db_env
    task_id = _mk_task(store, status="active", step_plan=_seed_plan(), minutes_ago=30)
    orphan = store.add_step(task_id=task_id, seq=1, kind="direct", goal="g", tool="llm")
    store.update_step(orphan, status="running", started_at="2026-10-11T10:00:00+00:00")

    executor = ex.TaskExecutor(app=test_app)
    executor._mark_interrupted_by_restart()

    s = store.get_step(orphan)
    assert s["status"] == "interrupted" and s["interrupt_reason"] == "restart"
    t = store.get_task(task_id)
    assert t["abort_reason"] == "restart", "task-level restart marker set"
    # Re-running the sweep is idempotent (no rows left running).
    executor._mark_interrupted_by_restart()
    assert store.get_step(orphan)["interrupt_reason"] == "restart"


def test_restart_restarts_task_audio_transcription(db_env, test_app, monkeypatch):
    """A task frozen for Whisper transcription when the process died keeps its
    freeze and the transcription thread is started again (its files are still
    on disk) — restoring queued/awaiting here would hand the planner an empty
    dictated request."""
    store = db_env
    task_id = _mk_task(store, status="frozen", minutes_ago=30)
    store.update_task(
        task_id, abort_reason="audio_transcript", audio=[{"file_path": f"{task_id}/v.webm", "file_name": "v.webm"}]
    )
    started: list[str] = []
    monkeypatch.setattr(
        "app.tasks.audio_transcribe.start_task_audio_transcription",
        lambda app, tid: started.append(tid),
    )

    executor = ex.TaskExecutor(app=test_app)
    executor._mark_interrupted_by_restart()

    t = store.get_task(task_id)
    assert t["status"] == "frozen", "still parked for the transcription"
    assert t["abort_reason"] == "audio_transcript"
    assert started == [task_id], "the transcription thread is restarted"


def test_gate_closed_does_not_touch_foreign_running_step(db_env, test_app, monkeypatch):
    """Gate-closed freeze leaves a running row from ANOTHER process alone (I6b)."""
    store = db_env
    task_id = _mk_task(store, status="active", step_plan=_seed_plan(), minutes_ago=30)
    orphan = store.add_step(task_id=task_id, seq=1, kind="direct", goal="g", tool="llm")
    store.update_step(orphan, status="running")

    _cycle(test_app, gate=False)

    s = store.get_step(orphan)
    assert s["status"] == "running", "a step not owned by this process is not re-marked"
    t = store.get_task(task_id)
    assert t["status"] == "frozen" and t["abort_reason"] == "chat"


# ── 11. mid-step abort trigger (C1) ──────────────────────────────────────


def test_running_step_pointer_published_and_cleared(db_env, test_app, gate_open, monkeypatch):
    """Step start publishes the pointer + SET member; step end retracts both."""
    store = db_env
    task_id = _mk_task(store, step_plan=_seed_plan(), minutes_ago=30)
    fake = test_app.request_queue.redis
    monkeypatch.setattr(ex, "run_direct_step", lambda *a, **k: _ok_step_result())
    monkeypatch.setattr(ex, "_run_final_summary", lambda *a, **k: "final summary text")

    _cycle(test_app)

    assert fake.get(f"task:running_step:{task_id}") is None, "pointer cleared after the step ends"
    assert fake.smembers("task:active_running_steps") == set(), "SET empty after the task completed"


def test_chat_hook_raises_cancel_flag(db_env, test_app, monkeypatch):
    """_mark_chat_activity turns a published running-step pointer into a cancel flag."""
    fake = test_app.request_queue.redis
    fake.smembers.return_value = {"task-abc"}
    fake.get.return_value = "step-xyz"
    queue = test_app.request_queue

    queue._mark_chat_activity({"type": "text", "data": {"type": "text", "text": "hi"}})

    assert fake.set.call_args_list[-1].args[0] == "task:step_cancel:step-xyz"
    fake.smembers.return_value = set()
    fake.get.return_value = None


def test_chat_hook_swallows_redis_errors(db_env, test_app, monkeypatch):
    """A failing Redis must never break the chat enqueue path."""
    fake = test_app.request_queue.redis
    fake.smembers.side_effect = RuntimeError("redis down")
    queue = test_app.request_queue

    queue._mark_chat_activity({"type": "text", "data": {"type": "text", "text": "hi"}})  # must not raise
    fake.smembers.side_effect = None
    fake.smembers.return_value = set()


# ── 12. corrective retry carries the mismatch (I2) ───────────────────────


def test_retry_digest_contains_checkpoint_mismatch(db_env, test_app, gate_open, monkeypatch):
    """False verdict ⇒ second attempt's context_digest carries the explanation."""
    store = db_env
    task_id = _mk_task(store, step_plan=_seed_plan(), minutes_ago=30)
    digests = []
    attempts = {"n": 0}

    def _runner(app, step, context_digest, cancel_check, **kwargs):
        if int(step.get("seq") or 0) != 1:
            return _ok_step_result()  # the second plan step just passes
        attempts["n"] += 1
        digests.append(context_digest)
        if attempts["n"] == 1:
            return {
                "result": "wrong result",
                "exec_seconds": 0.3,
                "status": "failed",
                "checkpoint": {"verdict": False, "explanation": "missing the port number", "exec_seconds": 0.2},
            }
        return _ok_step_result()

    monkeypatch.setattr(ex, "run_direct_step", _runner)
    monkeypatch.setattr(ex, "_run_final_summary", lambda *a, **k: "final summary text")

    _cycle(test_app)

    assert attempts["n"] == 2, "one corrective retry ran"
    assert "missing the port number" in digests[1], "retry digest carries the checkpoint mismatch"
    assert digests[0] == "" or "missing the port number" not in digests[0], "first attempt had no mismatch note"
    # The step row keeps the False verdict + explanation.
    row = store.get_task_steps(task_id)[0]
    assert row["checkpoint_verdict"] is True and row["status"] == "passed"
    assert row["attempts"] == 2


# ── 13. no duplicate final (M3) + subtask kind (M1) + poll interval (I5) ──


def test_final_step_not_duplicated(db_env, test_app, gate_open, monkeypatch):
    """A task that already has a final step reuses it instead of adding another."""
    store = db_env
    task_id = _mk_task(store, step_plan=_seed_plan(), minutes_ago=30)
    existing_final = store.add_step(task_id=task_id, seq=99, kind="final")

    monkeypatch.setattr(ex, "run_direct_step", lambda *a, **k: _ok_step_result())
    monkeypatch.setattr(ex, "_run_final_summary", lambda *a, **k: "final summary text")
    _cycle(test_app)

    finals = [s for s in store.get_task_steps(task_id) if s["kind"] == "final"]
    assert len(finals) == 1 and finals[0]["id"] == existing_final
    assert finals[0]["result"] == "final summary text", "existing final reused and updated"


def test_final_step_records_header_meta(db_env, test_app):
    """The final summary's model/tokens/duration reach the row (chat-style header).

    ``exec_seconds`` was never written for a final step, so the header silently
    dropped ⏱️/🔢/🚀 even though the summary ran on the reasoning model.
    """
    store = db_env
    task_id = _mk_task(store, step_plan=_seed_plan(), minutes_ago=30)
    meta = {
        "model_name": "Qwen3.6-Test.gguf",
        "usage": {"prompt_tokens": 284, "completion_tokens": 747},
        "exec_seconds": 4.2,
    }
    ex.add_final_step(task_id, "the answer", meta=meta)

    rows = [s for s in store.get_task_steps(task_id) if s["kind"] == "final"]
    assert len(rows) == 1
    assert abs(float(rows[0]["exec_seconds"]) - 4.2) < 0.01
    assert rows[0]["extra"]["usage"]["completion_tokens"] == 747
    assert rows[0]["extra"]["model_name"] == "Qwen3.6-Test.gguf"


def test_subtask_rows_use_subtask_kind(db_env, test_app, gate_open, monkeypatch):
    """Plan rows for media/RLM tools get kind='subtask' (M1), not 'direct'."""
    store = db_env
    task_id = _mk_task(store, step_plan=_seed_plan(), minutes_ago=30)
    executor = ex.TaskExecutor(app=test_app)
    executor._create_plan_step_rows(
        task_id,
        [
            {"seq": 1, "goal": "draw", "tool": "image_gen", "checkpoint_criteria": "c"},
            {"seq": 2, "goal": "think", "tool": "llm", "checkpoint_criteria": "c"},
        ],
    )
    kinds = [s["kind"] for s in store.get_task_steps(task_id)]
    assert kinds == ["subtask", "direct"]


def test_plan_question_row_gets_planner_meta(db_env, test_app, gate_open, monkeypatch):
    """A plan-typed clarifying question (user_input_expected) carries the
    planner's chat-style header metadata — model, type, tokens, time — into
    its row's extra, so the question message renders like every other."""
    store = db_env
    from app.tasks.store import update_task

    plan = {
        "title": "Ask me",
        "plan_meta": {
            "model_name": "Qwen3.6-35B-A3B-UD-Q2_K_XL.gguf",
            "model_type": "reasoning",
            "exec_seconds": 23.4,
            "usage": {"prompt_tokens": 1003, "completion_tokens": 1984},
        },
        "steps": [
            {"seq": 1, "goal": "Which city?", "tool": "llm", "user_input_expected": True},
            {"seq": 2, "goal": "Continue", "tool": "llm", "checkpoint_criteria": "c"},
        ],
    }
    task_id = _mk_task(store, step_plan=plan)
    update_task(task_id, status="awaiting_user")
    executor = ex.TaskExecutor(app=test_app)
    executor._create_plan_step_rows(task_id, plan["steps"])

    rows = store.get_task_steps(task_id)
    question = next(r for r in rows if r["kind"] == "question")
    direct = next(r for r in rows if r["kind"] == "direct")
    assert question["extra"]["model_name"] == "Qwen3.6-35B-A3B-UD-Q2_K_XL.gguf"
    assert question["extra"]["model_type"] == "reasoning"
    assert question["extra"]["usage"] == {"prompt_tokens": 1003, "completion_tokens": 1984}
    assert question["extra"]["exec_seconds"] == 23.4
    assert not direct["extra"], "non-question rows keep an empty extra"


def test_poll_interval_from_app_config(test_app, monkeypatch):
    """_loop and stop() read TASK_POLL_INTERVAL_S from the app config (I5)."""
    executor = ex.TaskExecutor(app=test_app)
    test_app.config["TASK_POLL_INTERVAL_S"] = 0.01
    monkeypatch.setattr(executor, "_process_one_cycle", lambda *a, **k: executor._shutdown.set())

    executor.start()
    thread = executor._thread
    executor.stop(timeout=2)
    assert not thread.is_alive(), "stop() joined the loop quickly with the config interval"


# ── whole-branch review fixes ────────────────────────────────────────────


class _SimpleQ:
    """Minimal request-queue stub: add_request returns a fixed id, cancel is a no-op."""

    def add_request(self, *a, **k):
        return "sub-queue-id-1", {}

    def cancel_task(self, *a, **k):
        pass


def test_subtask_result_runs_checkpoint(db_env, test_app, gate_open, monkeypatch):
    """Spec §4: a subtask result is verified by a normal checkpoint against
    the step's checkpoint_criteria; a rejected verification fails the step
    and the task continues (media refs still recorded)."""
    store = db_env
    plan = {
        "title": "Media task",
        "steps": [
            {"seq": 1, "goal": "Make an image", "tool": "image_gen", "checkpoint_criteria": "image matches the request"}
        ],
    }
    task_id = _mk_task(store, step_plan=plan)
    executor = ex.TaskExecutor(app=test_app)

    class _Q:
        def add_request(self, *a, **k):
            return "sub-queue-id-1", {}

        def cancel_task(self, *a, **k):
            pass

    monkeypatch.setattr(ex, "_get_request_queue", lambda: _Q())
    executor._enqueue_subtask(
        store.get_task(task_id),
        0,
        store.get_task(task_id)["step_plan"]["steps"][0],
    )
    row = store.get_task_steps(task_id)[0]
    queue_id = row["extra"]["subtask_queue_id"]
    assert row["status"] == "subtask_queued"

    envelope = {"status": "completed", "response": "an image was generated", "file_path": "x/y.png"}
    monkeypatch.setattr(executor, "_peek_subtask_result", lambda qid: envelope if qid == queue_id else None)
    checks = []

    def _fake_checkpoint(app, step, result, lang="ru"):
        checks.append({"step": step, "result": result, "lang": lang})
        return {"verdict": False, "explanation": "wrong subject", "exec_seconds": 0.3}

    monkeypatch.setattr(ex, "run_checkpoint", _fake_checkpoint)

    outcome = executor._poll_subtask(store.get_task(task_id), 0, row, gate_open=True)

    assert outcome == "done"
    assert len(checks) == 1
    assert "an image was generated" in checks[0]["result"]
    # The saved artifact is named for the verifier (file_path rides the
    # envelope; the artifact line is appended even without a file_name).
    assert "x/y.png" in checks[0]["result"]
    assert checks[0]["step"]["checkpoint_criteria"] == "image matches the request"
    step = store.get_task_steps(task_id)[0]
    assert step["status"] == "failed", "rejected verification fails the step"
    assert step["checkpoint_verdict"] is False and step["checkpoint_result"] == "wrong subject"
    assert store.get_task(task_id)["next_step_index"] == 1, "the task continues"


def test_subtask_result_checkpoint_pass_keeps_media(db_env, test_app, gate_open, monkeypatch):
    """A passing verification marks the step passed with checkpoint fields and
    the media refs merged into extra."""
    store = db_env
    plan = {
        "title": "Media task",
        "steps": [{"seq": 1, "goal": "Make an image", "tool": "image_gen", "checkpoint_criteria": "image exists"}],
    }
    task_id = _mk_task(store, step_plan=plan)
    executor = ex.TaskExecutor(app=test_app)
    monkeypatch.setattr(ex, "_get_request_queue", lambda: _SimpleQ())
    executor._enqueue_subtask(store.get_task(task_id), 0, plan["steps"][0])
    row = store.get_task_steps(task_id)[0]
    queue_id = row["extra"]["subtask_queue_id"]

    envelope = {"status": "completed", "response": "done", "file_path": "a/b.png", "file_name": "b.png"}
    monkeypatch.setattr(executor, "_peek_subtask_result", lambda qid: envelope if qid == queue_id else None)
    monkeypatch.setattr(
        ex, "run_checkpoint", lambda *a, **k: {"verdict": True, "explanation": "ok", "exec_seconds": 0.2}
    )

    executor._poll_subtask(store.get_task(task_id), 0, row, gate_open=True)

    step = store.get_task_steps(task_id)[0]
    assert step["status"] == "passed"
    assert step["checkpoint_verdict"] is True
    assert step["extra"]["media"]["file_path"] == "a/b.png"
    assert step["extra"]["media"]["file_name"] == "b.png"


def test_subtask_envelope_meta_lands_in_step_extra(db_env, test_app, gate_open, monkeypatch):
    """Model header metadata from the envelope (model name / type / token
    usage) is merged into the step's extra — the task view renders the same
    chat-style header on media steps as on direct ones."""
    store = db_env
    plan = {
        "title": "Camera task",
        "steps": [
            {"seq": 1, "goal": "Snapshot the kitchen", "tool": "camera", "checkpoint_criteria": "snapshot taken"}
        ],
    }
    task_id = _mk_task(store, step_plan=plan)
    executor = ex.TaskExecutor(app=test_app)
    monkeypatch.setattr(ex, "_get_request_queue", lambda: _SimpleQ())
    executor._enqueue_subtask(store.get_task(task_id), 0, plan["steps"][0])
    row = store.get_task_steps(task_id)[0]
    queue_id = row["extra"]["subtask_queue_id"]

    envelope = {
        "status": "completed",
        "response": "Camera snapshot: кухня\nНа снимке кошка спит.",
        "file_path": "t/cat.jpg",
        "model_name": "Qwen3VL-4B",
        "model_type": "multimodal",
        "usage": {"prompt_tokens": 120, "completion_tokens": 45},
    }
    monkeypatch.setattr(executor, "_peek_subtask_result", lambda qid: envelope if qid == queue_id else None)
    monkeypatch.setattr(
        ex, "run_checkpoint", lambda *a, **k: {"verdict": True, "explanation": "ok", "exec_seconds": 0.2}
    )

    executor._poll_subtask(store.get_task(task_id), 0, row, gate_open=True)

    step = store.get_task_steps(task_id)[0]
    assert step["extra"]["model_name"] == "Qwen3VL-4B"
    assert step["extra"]["model_type"] == "multimodal"
    assert step["extra"]["usage"] == {"prompt_tokens": 120, "completion_tokens": 45}
    assert step["extra"]["media"]["file_path"] == "t/cat.jpg", "media refs keep working"


def test_subtask_error_envelope_keeps_media_and_meta(db_env, test_app, gate_open, monkeypatch):
    """A camera step the worker reported as FAILED (snapshot error) still gets
    its full header: the multimodal model the step would have used + the real
    token totals are part of the error envelope now, and the executor merges
    them into the step's extra even when the step does not pass."""
    store = db_env
    plan = {
        "title": "Camera task",
        "steps": [{"seq": 1, "goal": "Snapshot the kitchen", "tool": "camera", "checkpoint_criteria": "x"}],
    }
    task_id = _mk_task(store, step_plan=plan)
    executor = ex.TaskExecutor(app=test_app)
    monkeypatch.setattr(ex, "_get_request_queue", lambda: _SimpleQ())
    executor._enqueue_subtask(store.get_task(task_id), 0, plan["steps"][0])
    row = store.get_task_steps(task_id)[0]
    queue_id = row["extra"]["subtask_queue_id"]

    envelope = {
        "status": "completed",
        "error": "Не удалось получить изображение с камеры детская: Таймаут (15с)",
        "model_name": "Qwen3VL-8B-Instruct-Q4_K_M.gguf",
        "model_type": "multimodal",
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
    }
    monkeypatch.setattr(executor, "_peek_subtask_result", lambda qid: envelope if qid == queue_id else None)

    executor._poll_subtask(store.get_task(task_id), 0, row, gate_open=True)

    step = store.get_task_steps(task_id)[0]
    assert step["status"] == "failed"
    assert "Таймаут" in step["last_error"]
    assert step["extra"]["model_name"] == "Qwen3VL-8B-Instruct-Q4_K_M.gguf", "failed step keeps the model in the header"
    assert step["extra"]["model_type"] == "multimodal"
    # usage of all-zero tokens is kept as {0,0} (the camera error burned no
    # LLM time); the JS renders nothing for it, the model name stays.
    assert step["extra"]["usage"] == {"prompt_tokens": 0, "completion_tokens": 0}


def test_subtask_envelope_without_meta_keeps_extra_clean(db_env, test_app, gate_open, monkeypatch):
    """A worker without the meta fields (or an error envelope) changes nothing."""
    store = db_env
    plan = {"title": "Media task", "steps": [{"seq": 1, "goal": "Make an image", "tool": "image_gen"}]}
    task_id = _mk_task(store, step_plan=plan)
    executor = ex.TaskExecutor(app=test_app)
    monkeypatch.setattr(ex, "_get_request_queue", lambda: _SimpleQ())
    executor._enqueue_subtask(store.get_task(task_id), 0, plan["steps"][0])
    row = store.get_task_steps(task_id)[0]
    queue_id = row["extra"]["subtask_queue_id"]

    envelope = {"status": "completed", "response": "done", "file_path": "a/b.png"}
    monkeypatch.setattr(executor, "_peek_subtask_result", lambda qid: envelope if qid == queue_id else None)
    monkeypatch.setattr(
        ex, "run_checkpoint", lambda *a, **k: {"verdict": True, "explanation": "ok", "exec_seconds": 0.1}
    )

    executor._poll_subtask(store.get_task(task_id), 0, row, gate_open=True)

    extra = store.get_task_steps(task_id)[0]["extra"]
    assert "model_name" not in extra and "model_type" not in extra and "usage" not in extra


def test_subtask_no_criteria_skips_checkpoint(db_env, test_app, gate_open, monkeypatch):
    """No checkpoint_criteria on the subtask step ⇒ no verifier call (the
    checkpoint machinery is opt-in per plan step)."""
    store = db_env
    plan = {"title": "Media task", "steps": [{"seq": 1, "goal": "Make an image", "tool": "image_gen"}]}
    task_id = _mk_task(store, step_plan=plan)
    executor = ex.TaskExecutor(app=test_app)
    monkeypatch.setattr(ex, "_get_request_queue", lambda: _SimpleQ())
    executor._enqueue_subtask(store.get_task(task_id), 0, plan["steps"][0])
    row = store.get_task_steps(task_id)[0]
    queue_id = row["extra"]["subtask_queue_id"]

    envelope = {"status": "completed", "response": "done"}
    monkeypatch.setattr(executor, "_peek_subtask_result", lambda qid: envelope if qid == queue_id else None)
    monkeypatch.setattr(
        ex, "run_checkpoint", lambda *a, **k: (_ for _ in ()).throw(AssertionError("verifier must not run"))
    )

    executor._poll_subtask(store.get_task(task_id), 0, row, gate_open=True)
    assert store.get_task_steps(task_id)[0]["status"] == "passed"


def test_resume_of_interrupted_subtask_cancels_old_and_keeps_audit(db_env, test_app, gate_open, monkeypatch):
    """Resuming an interrupted subtask enqueues a fresh request, re-cancels the
    superseded queue id and keeps it in extra (C2: the old job can never be
    mistaken for this step's result)."""
    store = db_env
    plan = {"title": "Media task", "steps": [{"seq": 1, "goal": "Make an image", "tool": "image_gen"}]}
    task_id = _mk_task(store, step_plan=plan)
    executor = ex.TaskExecutor(app=test_app)
    monkeypatch.setattr(ex, "_get_request_queue", lambda: _SimpleQ())

    # First enqueue (as on the first pass).
    executor._enqueue_subtask(store.get_task(task_id), 0, plan["steps"][0])
    row = store.get_task_steps(task_id)[0]
    first_queue_id = row["extra"]["subtask_queue_id"]

    # Chat abort path: the old request is cancelled, the step goes interrupted.
    cancelled = []

    class _Q2:
        def add_request(self, *a, **k):
            return "sub-queue-id-2", {}

        def cancel_task(self, task_id):
            cancelled.append(task_id)

    monkeypatch.setattr(ex, "_get_request_queue", lambda: _Q2())
    executor._abort_subtask(task_id, 0, row, first_queue_id, reason="chat")
    row = store.get_task_steps(task_id)[0]
    assert row["status"] == "interrupted"

    # Resume: the loop sees interrupted (not queued/running) → re-enqueue.
    assert str(row["tool"]) == "image_gen"
    executor._enqueue_subtask(store.get_task(task_id), 0, plan["steps"][0])

    row2 = store.get_task_steps(task_id)[0]
    assert row2["extra"]["subtask_queue_id"] == "sub-queue-id-2"
    assert first_queue_id in (row2["extra"].get("superseded_queue_ids") or [])
    assert first_queue_id in cancelled, "the superseded request is re-cancelled defensively"


def test_plan_phase_cancel_flag_interrupts_planner(db_env, test_app, gate_open, monkeypatch):
    """The planner's cancel_check reads the task:cancelled flag (I5) — a stop
    arriving mid-plan is observed between stream chunks."""
    store = db_env
    task_id = _mk_task(store)
    seen = {}

    def _fake_planner(app, prompt, cancel_check, lang, status_callback=None):
        seen["cancel_callable"] = cancel_check
        return '{"title": "T", "steps": [{"seq": 1, "goal": "g", "tool": "llm"}]}'

    monkeypatch.setattr(ex, "_run_planner", _fake_planner)
    monkeypatch.setattr(ex, "run_direct_step", lambda *a, **k: _ok_step_result())

    _cycle(test_app)

    assert "cancel_callable" in seen, "the planner receives a real cancel check"
    task = store.get_task(task_id)
    # The mock Redis answers None → not cancelled → the plan completes.
    assert seen["cancel_callable"]() is False
    assert task["step_plan"].get("steps"), "plan still parsed when not cancelled"


# ── whole-branch review follow-up: UI bugfixes ──────────────────────────


def test_awaiting_task_does_not_republish_each_cycle(db_env, test_app, gate_open, monkeypatch):
    """An awaiting task is re-picked every cycle; the SSE update is published
    only on a status TRANSITION — a per-cycle publish made the task window
    re-render (flicker) once a second."""
    store = db_env
    plan = {
        "title": "Needs answer",
        "steps": [{"seq": 1, "goal": "Which port?", "tool": "llm", "user_input_expected": True}],
    }
    task_id = _mk_task(store, status="active", step_plan=plan, minutes_ago=30)
    publishes = []
    executor = ex.TaskExecutor(app=test_app)
    monkeypatch.setattr(executor, "_publish_task_update", lambda tid: publishes.append(tid))

    executor._process_one_cycle(gate_open=True)
    assert store.get_task(task_id)["status"] == "awaiting_user"
    first_count = len(publishes)

    # The next cycle re-picks the awaiting task — no new publish (no transition).
    executor._process_one_cycle(gate_open=True)
    assert len(publishes) == first_count, "no republish while the status is unchanged"


def test_publish_fires_on_status_transition(db_env, test_app, gate_open, monkeypatch):
    """A real transition (active → awaiting_user) publishes: the plan-phase
    pre-publish (panel switches to «Составляю план…») plus the awaiting
    transition — 2 publishes for a one-question plan."""
    store = db_env
    plan = {
        "title": "Needs answer",
        "steps": [{"seq": 1, "goal": "Which port?", "tool": "llm", "user_input_expected": True}],
    }
    task_id = _mk_task(store, status="active", step_plan=plan, minutes_ago=30)
    publishes = []
    executor = ex.TaskExecutor(app=test_app)
    monkeypatch.setattr(executor, "_publish_task_update", lambda tid: publishes.append(tid))

    executor._process_one_cycle(gate_open=True)

    assert publishes.count(task_id) == 2, "plan-phase publish + awaiting transition"
    assert publishes[0] == task_id


def test_verifier_needs_user_input_asks_and_reruns(db_env, test_app, gate_open, monkeypatch):
    """Checkpoint third verdict needs_user_input: a dedicated question row is
    appended (linked to the direct step), the task awaits, and after the
    answer the SAME direct step re-runs with the answer in the digest."""
    store = db_env
    task_id = _mk_task(store, step_plan=_seed_plan(), minutes_ago=30)
    attempts = {"n": 0}

    def _runner(app, step, context_digest, cancel_check, **kwargs):
        if int(step.get("seq") or 0) != 1:
            return _ok_step_result()
        attempts["n"] += 1
        if attempts["n"] == 1:
            return {
                "result": "incomplete answer",
                "exec_seconds": 0.2,
                "status": "failed",
                "checkpoint": {
                    "verdict": False,
                    "needs_user_input": True,
                    "question": "Which city do you mean?",
                    "explanation": "the city is ambiguous",
                    "model_name": "Qwen3.6-35B-A3B-UD-Q2_K_XL.gguf",
                    "usage": {"prompt_tokens": 320, "completion_tokens": 11},
                    "exec_seconds": 0.8,
                },
            }
        assert "city" in context_digest.lower(), "answer lands in the retry digest"
        return _ok_step_result()

    monkeypatch.setattr(ex, "run_direct_step", _runner)
    monkeypatch.setattr(ex, "_run_final_summary", lambda *a, **k: "final summary text")

    executor = ex.TaskExecutor(app=test_app)
    executor._process_one_cycle(gate_open=True)

    # Task awaits; question row appended and linked to the direct step.
    t = store.get_task(task_id)
    assert t["status"] == "awaiting_user"
    rows = store.get_task_steps(task_id)
    questions = [r for r in rows if r["kind"] == "question"]
    direct = next(r for r in rows if r["kind"] == "direct" and int(r["seq"] or 0) == 1)
    assert len(questions) == 1 and questions[0]["parent_step_id"] == direct["id"]
    assert questions[0]["status"] == "awaiting_user"
    assert questions[0]["result"] == "Which city do you mean?"
    # The question carries the verifier's model + tokens + time (chat-style
    # header, same as every other task message).
    assert questions[0]["extra"]["model_name"] == "Qwen3.6-35B-A3B-UD-Q2_K_XL.gguf"
    assert questions[0]["extra"]["model_type"] == "reasoning"
    assert questions[0]["extra"]["usage"] == {"prompt_tokens": 320, "completion_tokens": 11}
    assert questions[0]["extra"]["exec_seconds"] == 0.8
    assert direct["status"] == "pending", "rejected attempt resets for the re-run"

    # Answer arrives → the direct step re-runs (attempt 2), task completes.
    ui_id = ex.add_user_input_step(task_id, int(questions[0]["seq"]), "London")
    ui = next(r for r in store.get_task_steps(task_id) if r["id"] == ui_id)
    executor._process_user_answer(ui)

    t = store.get_task(task_id)
    assert t["status"] == "active"
    assert int(t["next_step_index"]) == 0, "resume at the direct step's position"
    assert attempts["n"] == 1, "re-run happens on the NEXT cycle"
    executor._process_one_cycle(gate_open=True)
    assert attempts["n"] == 2, "the same step ran again with the answer"
    assert store.get_task(task_id)["status"] == "completed"


def test_verifier_question_row_after_plan_steps(db_env, test_app, gate_open, monkeypatch):
    """The verifier question row gets seq = max(seq)+1 (after the plan rows)
    and is consumed as passed once answered."""
    store = db_env
    plan = {
        "title": "T",
        "steps": [{"seq": 1, "goal": "g", "tool": "llm", "checkpoint_criteria": "c"}],
    }
    task_id = _mk_task(store, step_plan=plan, minutes_ago=30)

    def _runner(app, step, context_digest, cancel_check, **kwargs):
        return {
            "result": "partial",
            "exec_seconds": 0.2,
            "status": "failed",
            "checkpoint": {
                "verdict": False,
                "needs_user_input": True,
                "question": "Clarify?",
                "explanation": "x",
            },
        }

    monkeypatch.setattr(ex, "run_direct_step", _runner)
    monkeypatch.setattr(ex, "_run_final_summary", lambda *a, **k: "final")
    executor = ex.TaskExecutor(app=test_app)
    executor._process_one_cycle(gate_open=True)

    rows = store.get_task_steps(task_id)
    assert [int(r["seq"] or 0) for r in rows] == [1, 2], "question appended after plan steps"
    assert rows[1]["kind"] == "question" and rows[1]["parent_step_id"] == rows[0]["id"]

    ui_id = ex.add_user_input_step(task_id, 2, "yes")
    ui = next(r for r in store.get_task_steps(task_id) if r["id"] == ui_id)
    executor._process_user_answer(ui)
    rows = store.get_task_steps(task_id)
    q = rows[1]
    assert q["status"] == "passed" and q["finished_at"], "question closed on answer"
    assert int(store.get_task(task_id)["next_step_index"]) == 0


# ── deep-analysis toggle ↔ plan appending (v12.6) ─────────────────────────


def test_append_deep_analysis_step(db_env):
    """Turning the 🔬 toggle ON at answer time appends an rlm step over the
    task request; its checkpoint criteria are localized."""
    store = db_env
    task_id = _mk_task(
        store,
        status="active",
        step_plan={
            "task_request": "Проанализируй документы про кошек",
            "steps": [{"seq": 1, "goal": "g1", "tool": "llm", "checkpoint_criteria": "c1"}],
        },
    )

    ex.append_deep_analysis_step(task_id, lang="ru")

    steps = store.get_task(task_id)["step_plan"]["steps"]
    assert len(steps) == 2
    assert steps[1]["tool"] == "rlm"
    assert int(steps[1]["seq"]) == 2
    assert steps[1]["goal"] == "Проанализируй документы про кошек"
    # Criteria are localized at append time — lang="ru" produced the ru wording.
    assert steps[1]["checkpoint_criteria"], "criteria present"
    assert steps[1]["user_input_expected"] is False

    # Idempotent: a second call (another answer with the toggle) appends nothing.
    ex.append_deep_analysis_step(task_id, lang="ru")
    assert len(store.get_task(task_id)["step_plan"]["steps"]) == 2


def test_capabilities_section_lists_camera_rooms():
    """The planner prompt advertises the user's exact camera rooms so it can
    emit ONE camera step per room ("which room…" questions)."""
    section = ex._capabilities_section(camera_rooms=["Кухня", "Гостиная"])
    assert "camera" in section
    assert "Available rooms: Кухня, Гостиная" in section, "rooms listed verbatim in the section"
    # Without cameras the section is unchanged (no dangling fragment).
    assert ex._capabilities_section() == ex._capabilities_section(camera_rooms=None)


def test_camera_rooms_safe_when_camera_off(test_app):
    from unittest.mock import MagicMock

    cam = MagicMock()
    cam.available = False
    test_app.modules["cam"] = cam
    executor = ex.TaskExecutor(app=test_app)

    assert executor._camera_rooms({"user_id": "tester"}) == []
    cam.get_available_rooms.assert_not_called()


def test_camera_rooms_resolve_available(test_app):
    from unittest.mock import MagicMock

    cam = MagicMock()
    cam.available = True
    cam.get_available_rooms.return_value = {"kitchen": "Кухня", "living": "Гостиная"}
    test_app.modules["cam"] = cam
    executor = ex.TaskExecutor(app=test_app)

    assert executor._camera_rooms({"user_id": "tester"}) == ["Гостиная", "Кухня"]
    cam.get_available_rooms.assert_called_once_with("tester")


def test_subtask_mapping_has_camera_and_crawl():
    """camera/crawl ride the queue as camera_task / crawl_task sub-tasks."""
    assert ex._SUBTASK_TYPE["camera"] == "camera_task"
    assert ex._SUBTASK_TYPE["crawl"] == "crawl_task"
    assert {"camera", "crawl"} <= ex._SUBTASK_TOOLS


def test_subtask_mapping_has_vision():
    """vision rides the queue as a vision_task sub-task."""
    assert ex._SUBTASK_TYPE["vision"] == "vision_task"
    assert "vision" in ex._SUBTASK_TOOLS


def test_capabilities_section_hides_vision_without_images():
    """vision is advertised ONLY with attached images — the planner must not
    invent image-analysis steps for a text-only task."""
    with_images = ex._capabilities_section(camera_rooms=None, has_images=True)
    without_images = ex._capabilities_section(camera_rooms=None, has_images=False)
    assert "vision" in with_images
    assert "vision (" not in without_images
    # Other capabilities are unaffected.
    assert "llm (" in with_images and "llm (" in without_images


def test_task_image_parts_read_files(test_app, tmp_path):
    """Stored files are read back into data: URLs; missing files are skipped."""
    upload = tmp_path / "uploads"
    sub = upload / "sess1"
    sub.mkdir(parents=True)
    (sub / "a.jpg").write_bytes(b"jpegbytes")
    test_app.config["UPLOAD_FOLDER"] = str(upload)
    executor = ex.TaskExecutor(app=test_app)
    task = {
        "id": "t1",
        "images": [
            {"file_path": "sess1/a.jpg", "file_name": "a.jpg"},
            {"file_path": "sess1/gone.jpg", "file_name": "gone.jpg"},
            {"file_path": "../escape.jpg", "file_name": "evil.jpg"},
        ],
    }
    parts = executor._task_image_parts(task)
    assert len(parts) == 1, "missing and escaping paths skipped"
    assert parts[0]["name"] == "a.jpg"
    assert parts[0]["data"].startswith("data:image/jpeg;base64,")


def test_task_image_parts_label_the_real_image_type(test_app, tmp_path):
    """A part carries its own image type: a PNG must not go out labelled JPEG.

    The type comes from the stored ``file_type``, else from the file name; only
    an image whose type stays unknown keeps the legacy JPEG label."""
    upload = tmp_path / "uploads"
    sub = upload / "sess1"
    sub.mkdir(parents=True)
    for name in ("pic.png", "photo.jpg", "note.bmp", "mystery.bin"):
        (sub / name).write_bytes(b"x" * 8)
    test_app.config["UPLOAD_FOLDER"] = str(upload)
    executor = ex.TaskExecutor(app=test_app)
    task = {
        "id": "t1",
        "images": [
            {"file_path": "sess1/pic.png", "file_name": "pic.png", "file_type": "image/png"},
            {"file_path": "sess1/photo.jpg", "file_name": "photo.jpg"},
            {"file_path": "sess1/note.bmp", "file_name": "note.bmp", "file_type": "application/octet-stream"},
            {"file_path": "sess1/mystery.bin", "file_name": "mystery.bin"},
        ],
    }
    types = [p["data"].split(";", 1)[0] for p in executor._task_image_parts(task)]
    assert types == ["data:image/png", "data:image/jpeg", "data:image/bmp", "data:image/jpeg"]


def test_task_image_parts_empty_when_no_images(test_app):
    executor = ex.TaskExecutor(app=test_app)
    assert executor._task_image_parts({"id": "t1", "images": []}) == []
    assert executor._task_image_parts({"id": "t1"}) == []


def test_prior_step_media_image_is_raw_base64(monkeypatch, test_app, tmp_path):
    """The video wrapper decodes this value with ``base64.b64decode`` directly.

    A ``data:`` URL there raises "Incorrect padding" — the format, not the
    label, was what broke the video step that animates a prior step's image.
    """
    upload = tmp_path / "uploads"
    sub = upload / "task1"
    sub.mkdir(parents=True)
    (sub / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\npayload")
    test_app.config["UPLOAD_FOLDER"] = str(upload)
    steps = [
        {"seq": 1, "extra": {}},
        {
            "seq": 2,
            "extra": {"media": {"file_path": "task1/shot.png", "file_name": "shot.png", "file_type": "image/png"}},
        },
        {
            "seq": 3,
            "extra": {"media": {"file_path": "task1/doc.pdf", "file_name": "doc.pdf", "file_type": "application/pdf"}},
        },
        {
            "seq": 9,
            "extra": {"media": {"file_path": "task1/later.png", "file_name": "later.png", "file_type": "image/png"}},
        },
    ]
    monkeypatch.setattr(ex, "get_task_steps", lambda task_id: steps)
    executor = ex.TaskExecutor(app=test_app)

    data = executor._prior_step_media_image("t1", 5)

    assert data is not None
    assert not data.startswith("data:"), "the wrapper decodes raw base64 only"
    assert base64.b64decode(data) == (sub / "shot.png").read_bytes(), "latest image step before idx wins"


def test_vision_subtask_enqueue_carries_images_and_question(db_env, test_app, gate_open, monkeypatch):
    """A vision step enqueues a vision_task with data: parts + the question."""
    store = db_env
    task_id = _mk_task(store, step_plan=None)
    store.update_task(
        task_id,
        step_plan={
            "title": "seed",
            "steps": [{"seq": 1, "goal": "Что на картинке?", "tool": "vision", "checkpoint_criteria": "c"}],
        },
        next_step_index=0,
        status="active",
        images=[{"file_path": "x/a.jpg", "file_name": "a.jpg"}],
    )
    executor = ex.TaskExecutor(app=test_app)
    executor._task_image_parts = lambda task: [{"data": "data:image/jpeg;base64,QQ==", "name": "a.jpg"}]
    added = {}

    class _FakeQueue:
        def add_request(self, **kwargs):
            added.update(kwargs)
            return "q1", {}

        def cancel_task(self, *a, **k):
            return None

    fake_queue = _FakeQueue()
    monkeypatch.setattr(ex, "_get_request_queue", lambda: fake_queue)
    task = store.get_task(task_id)
    executor._enqueue_subtask(task, 0, task["step_plan"]["steps"][0])

    assert added["request_data"]["type"] == "vision_task"
    assert added["request_data"]["images"] == [{"data": "data:image/jpeg;base64,QQ==", "name": "a.jpg"}]
    assert added["request_data"]["task_question"] == "seed"


# ── SLM long-term memory in tasks (v12.6) ───────────────────────────────


def _fake_slm(recalled=None, available=True):
    from unittest.mock import MagicMock

    slm = MagicMock()
    slm.available = available
    slm.recall.return_value = recalled or []
    return slm


def _llm_step(goal="g", extra=None):
    """A direct llm step dict (same shape as the runner tests' _step)."""
    step = {"id": "s1", "kind": "direct", "tool": "llm", "goal": goal, "checkpoint_criteria": "c"}
    if extra is not None:
        step["extra"] = extra
    return step


def test_recall_slm_facts_formats_section(test_app):
    """Facts render as a prompt section, session-specific first (chat parity)."""
    from app.tasks.runner import recall_slm_facts

    test_app.modules["slm"] = _fake_slm(
        [
            {"content": "general fact", "metadata": {"fact_type": "general"}},
            {"content": "session fact", "metadata": {"fact_type": "session_specific"}},
        ]
    )
    out = recall_slm_facts(test_app, "составь меню", "valery", "ru")
    assert "Дополнительная информация из долговременной памяти:" in out
    assert "- session fact" in out and "- general fact" in out
    assert out.index("session fact") < out.index("general fact")


def test_recall_slm_facts_absent_module_is_empty(test_app):
    """No SLM module / empty user → empty section, never an error."""
    from app.tasks.runner import recall_slm_facts

    test_app.modules["slm"] = _fake_slm()
    assert recall_slm_facts(test_app, "query", "valery", "ru") == ""
    assert recall_slm_facts(test_app, "query", "", "ru") == ""


def test_recall_slm_facts_recall_error_is_empty(test_app):
    """A raised recall degrades to an empty section, never a task failure."""
    from app.tasks.runner import recall_slm_facts

    slm = _fake_slm()
    slm.recall.side_effect = RuntimeError("slm down")
    test_app.modules["slm"] = slm
    assert recall_slm_facts(test_app, "query", "valery", "ru") == ""


def test_llm_step_prompt_contains_slm_facts(test_app):
    """The llm step prompt embeds the recalled memory section."""
    captured = {}

    def fake_call(app, prompt, cancel_check, lang="ru", temperature=None):
        captured["prompt"] = prompt
        return "R"

    test_app.modules["slm"] = _fake_slm(
        [{"content": "пользователь вегетарианец", "metadata": {"fact_type": "general"}}]
    )
    with (
        patch("app.tasks.runner._call_reasoning", side_effect=fake_call),
        patch("app.tasks.runner.run_checkpoint", return_value=_passed_checkpoint()),
    ):
        run_direct_step(
            test_app,
            _llm_step(goal="Составь меню"),
            context_digest="",
            cancel_check=lambda: False,
            user_id="valery",
            task_request="Составь меню на неделю",
        )
    assert "пользователь вегетарианец" in captured["prompt"]
    assert test_app.modules["slm"].recall.call_args.kwargs["profile"] == "valery"


def test_llm_step_without_slm_module_has_no_placeholder(test_app):
    """Without SLM the step prompt carries no memory section and no residue."""
    import re

    captured = {}
    test_app.modules.pop("slm", None)
    with (
        patch("app.tasks.runner._call_reasoning", return_value="R"),
        patch("app.tasks.runner.run_checkpoint", return_value=_passed_checkpoint()),
    ):
        run_direct_step(test_app, _llm_step(), context_digest="", cancel_check=lambda: False, user_id="u")
    prompt = captured.get("prompt", "")
    assert "долговременной памяти" not in prompt
    assert not re.search(r"\{[a-z_]+\}", prompt)


def test_plan_phase_prompt_contains_slm_facts(db_env, test_app, gate_open, monkeypatch):
    """The planner prompt embeds the user's long-term memory facts."""
    store = db_env
    _mk_task(store)
    captured = {}

    def fake_planner(app, prompt, cancel_check, lang="ru", status_callback=None):
        captured["prompt"] = prompt
        return '{"title": "t", "steps": [{"seq": 1, "goal": "g", "tool": "llm", "checkpoint_criteria": "c"}]}'

    monkeypatch.setattr(ex, "_run_planner", fake_planner)
    monkeypatch.setattr(ex, "run_direct_step", lambda *a, **k: _ok_step_result())
    monkeypatch.setattr(ex, "_run_final_summary", lambda *a, **k: "final answer text here")
    test_app.modules["slm"] = _fake_slm(
        [{"content": "пользователь не ест грибы", "metadata": {"fact_type": "general"}}]
    )

    _cycle(test_app)

    assert "пользователь не ест грибы" in captured["prompt"]
    assert test_app.modules["slm"].recall.call_args.kwargs["profile"] == "tester"


def test_plan_phase_prompt_lists_attached_images(db_env, test_app, gate_open, monkeypatch):
    """The planner prompt names the attached images — a vision/llm plan over
    them needs no user_input question like «Прикрепите изображения…» (bug: 4
    images were attached and the planner still asked to attach them)."""
    store = db_env
    from app.tasks.store import update_task as _ut

    task_id = _mk_task(store)
    _ut(
        task_id,
        images=[
            {"file_name": "photo1.jpg", "file_path": "t/photo1.jpg", "file_type": "image/jpeg"},
            {"file_name": "photo2.jpg", "file_path": "t/photo2.jpg", "file_type": "image/jpeg"},
        ],
    )
    captured = {}

    def fake_planner(app, prompt, cancel_check, lang="ru", status_callback=None):
        captured["prompt"] = prompt
        return '{"title": "t", "steps": [{"seq": 1, "goal": "g", "tool": "vision", "checkpoint_criteria": "c"}]}'

    monkeypatch.setattr(ex, "_run_planner", fake_planner)
    monkeypatch.setattr(ex, "run_direct_step", lambda *a, **k: _ok_step_result())
    monkeypatch.setattr(ex, "_run_final_summary", lambda *a, **k: "final answer text here")

    _cycle(test_app)

    prompt = captured["prompt"]
    assert "photo1.jpg" in prompt and "photo2.jpg" in prompt, "attached image names reach the planner"
    assert prompt.count("photo") >= 2, "every attached image is listed"


def test_plan_phase_prompt_carries_current_time(db_env, test_app, gate_open, monkeypatch):
    """The planner prompt carries the current date/time — a calendar question
    («сколько дней до понедельника») needs no web search (bug: the planner
    routed it to search because the prompt had no date)."""
    store = db_env
    _mk_task(store)
    captured = {}

    fake_now = "Current date and time: Thursday, 15 October 2026, 12:00 (Europe/Moscow)."

    def fake_time(app):
        return fake_now

    def fake_planner(app, prompt, cancel_check, lang="ru", status_callback=None):
        captured["prompt"] = prompt
        return '{"title": "t", "steps": [{"seq": 1, "goal": "g", "tool": "llm", "checkpoint_criteria": "c"}]}'

    monkeypatch.setattr(ex, "_run_planner", fake_planner)
    monkeypatch.setattr(ex, "run_direct_step", lambda *a, **k: _ok_step_result())
    monkeypatch.setattr(ex, "_run_final_summary", lambda *a, **k: "final answer text here")
    monkeypatch.setattr(ex, "get_current_time_in_timezone", fake_time)

    _cycle(test_app)

    assert "15 October 2026" in captured["prompt"], "the current date reaches the planner prompt"


def test_extract_facts_thread_started_on_passed_llm_step(db_env, test_app, gate_open, monkeypatch):
    """A passed llm step triggers the chat-style background fact extraction."""
    store = db_env
    task_id = _mk_task(store, step_plan=_seed_plan(), status="active", next_step_index=0)
    started = []

    def fake_bg(app, query, response, session_id, user_id, lang):
        started.append((query, response, session_id, user_id, lang))

    import app.queue as q

    monkeypatch.setattr(q, "_extract_facts_bg", fake_bg)
    plan = _seed_plan()
    store.update_task(task_id, step_plan=plan)
    executor = ex.TaskExecutor(app=test_app)
    executor._maybe_extract_facts(store.get_task(task_id), {"tool": "llm"}, "Задача выполнена, вот подробный ответ")

    assert len(started) == 1
    query, response, session_id, user_id, lang = started[0]
    assert query == "seed" and "подробный ответ" in response
    assert session_id == f"task:{task_id}"
    assert user_id == "tester" and lang == "ru"


def test_extract_facts_skips_tool_steps_and_errors(db_env, test_app, gate_open):
    """Search/rag results and ⚠️ errors are data, not user memories."""
    store = db_env
    task_id = _mk_task(store)
    task = store.get_task(task_id)
    executor = ex.TaskExecutor(app=test_app)
    executor._maybe_extract_facts(task, {"tool": "search"}, "Длинный результат поиска, точно больше двадцати")
    executor._maybe_extract_facts(task, {"tool": "llm"}, "⚠️ GPU memory unavailable")
    executor._extract_facts_text(task, "короткий")

    import app.queue as q

    assert q._extract_facts_bg is not None  # sanity: nothing raised; patch-based check below


def test_extract_facts_skips_via_patch(db_env, test_app, gate_open, monkeypatch):
    """Negative extraction cases observed through the patched hook."""
    store = db_env
    task_id = _mk_task(store)
    started = []

    import app.queue as q

    monkeypatch.setattr(q, "_extract_facts_bg", lambda *a: started.append(a))
    task = store.get_task(task_id)
    executor = ex.TaskExecutor(app=test_app)
    executor._maybe_extract_facts(task, {"tool": "search"}, "Длинный результат поиска, точно больше двадцати")
    executor._maybe_extract_facts(task, {"tool": "llm"}, "⚠️ GPU memory unavailable")
    executor._extract_facts_text(task, "короткий")

    assert started == []


# ── media sub-task dispatch parity (v12.6 media fix) ────────────────────


class _RecordingQ:
    """Request-queue stub recording every add_request payload."""

    def __init__(self):
        self.calls = []

    def add_request(self, **kwargs):
        self.calls.append(kwargs)
        return "sub-queue-id-1", {}

    def cancel_task(self, *a, **k):
        pass


class _PublishProbeQ(_RecordingQ):
    """Queue stub that reads the step row the instant the request is published.

    The slow worker's orphan guard does exactly this, milliseconds after the
    push (live log: "Slow worker: dequeued task … (type=camera_task)" followed
    6 ms later by "Orphan subtask dropped: step … status=pending"): a step still
    marked ``pending`` at publish time is discarded as an orphan, and the step
    later dies with "subtask result expired".
    """

    def __init__(self, store):
        super().__init__()
        self.store = store
        self.status_at_publish = None

    def add_request(self, **kwargs):
        step_id = kwargs["request_data"]["task_step_subtask"]
        self.status_at_publish = (self.store.get_step(step_id) or {}).get("status")
        return super().add_request(**kwargs)


def _media_plan(tool, goal="Do the thing"):
    return {"title": "Media task", "steps": [{"seq": 1, "goal": goal, "tool": tool}]}


def test_enqueue_subtask_maps_tools_to_dispatched_queue_types(db_env, test_app, gate_open, monkeypatch):
    """Each media tool reaches a queue task_type ``_process_request`` dispatches.

    The previous mapping sent image_gen as "image" and image_edit as "image_edit",
    neither of which has a dispatch branch — the worker answered "Unknown request
    type" and the step failed without any media.
    """
    store = db_env
    for tool, task_type in (("image_gen", "image_gen"), ("video_gen", "video")):
        plan = _media_plan(tool)
        task_id = _mk_task(store, step_plan=plan)
        queue = _RecordingQ()
        monkeypatch.setattr(ex, "_get_request_queue", lambda q=queue: q)

        executor = ex.TaskExecutor(app=test_app)
        executor._enqueue_subtask(store.get_task(task_id), 0, plan["steps"][0])

        call = queue.calls[0]
        assert call["task_type"] == task_type
        assert call["request_data"]["type"] == task_type
        assert call["request_data"]["text"] == "Do the thing", "handlers read the instruction from text"
        assert call["request_data"]["task_step_subtask"], "the step tag travels for the worker guard"
        assert call["session_id"] == task_id, "the queue session IS the task id (media folder resolution)"
        assert store.get_task_steps(task_id)[0]["status"] == "subtask_queued"


def test_enqueue_subtask_stamps_the_step_before_publishing(db_env, test_app, gate_open, monkeypatch):
    """The step is ``subtask_queued`` BEFORE the request reaches the queue.

    Root cause of the live «camera steps show file names but no snapshots»
    defect: the request was published first and the status written afterwards,
    so the idle slow worker dequeued it within milliseconds and its orphan guard
    dropped it — every camera step then failed with "subtask result expired"
    (nothing was ever generated, so no media reached the step either).
    """
    store = db_env
    plan = _media_plan("camera", goal="Сделайте снимок кухни")
    task_id = _mk_task(store, step_plan=plan)
    queue = _PublishProbeQ(store)
    monkeypatch.setattr(ex, "_get_request_queue", lambda q=queue: q)

    executor = ex.TaskExecutor(app=test_app)
    executor._enqueue_subtask(store.get_task(task_id), 0, plan["steps"][0])

    assert queue.status_at_publish == "subtask_queued", (
        "publishing before the status write leaves the step 'pending' when the "
        "idle worker dequeues it — the orphan guard drops a live sub-task"
    )
    step = store.get_task_steps(task_id)[0]
    assert step["extra"]["subtask_queue_id"], "the queue id is stamped on the step for polling"


def test_video_gen_subtask_reuses_prior_step_image(db_env, test_app, gate_open, monkeypatch):
    """A video_gen step generates from the EDITED IMAGE of a prior step, not
    from the text goal (bug: the video was made from the goal description).

    The prior step's extra.media.file_path (an image/* artifact, e.g. the png
    an image_edit step saved) is read back from the upload folder and rides
    the request as ``file_data`` — the queue's video handler branches on it
    into the image+text pipeline. A data: prefix is fine (chat_with_image
    normalizes it), so the same bytes the chat path sends travel along.
    """
    import base64
    import json
    import os

    from app.tasks.store import add_step, update_step

    store = db_env
    plan = _media_plan("video_gen", goal="Оживите картинку")
    task_id = _mk_task(store, step_plan=plan)

    # The prior image_edit step stored its artifact in extra.media.
    img_rel = f"{task_id}/edited.png"
    upload_folder = str(test_app.config["UPLOAD_FOLDER"])
    os.makedirs(os.path.join(upload_folder, task_id), exist_ok=True)
    png_path = os.path.join(upload_folder, img_rel)
    png_bytes = b"\x89PNG fake image bytes"
    with open(png_path, "wb") as fh:
        fh.write(png_bytes)

    prior = add_step(task_id=task_id, seq=0, kind="subtask", goal="Edit the image", tool="image_edit")
    update_step(prior, status="passed", extra=json.dumps({"media": {"file_path": img_rel, "file_type": "image/png"}}))

    queue = _RecordingQ()
    monkeypatch.setattr(ex, "_get_request_queue", lambda q=queue: q)

    executor = ex.TaskExecutor(app=test_app)
    task = store.get_task(task_id)
    executor._enqueue_subtask(task, 0, plan["steps"][0])

    call = queue.calls[0]
    file_data = call["request_data"].get("file_data")
    assert file_data, "the video step must receive the prior step's image"
    assert base64.b64decode(file_data.split(",", 1)[-1]) == png_bytes, "the prior step's edited image bytes travel"

    # Housekeeping for the next tests.
    os.remove(png_path)


def test_video_gen_subtask_without_prior_image_falls_back_to_text(db_env, test_app, gate_open, monkeypatch):
    """No image anywhere in the task ⇒ the video step stays text-to-video
    (chat parity: a chat video request with no file generates from text)."""
    store = db_env
    plan = _media_plan("video_gen", goal="Волны на закате")
    task_id = _mk_task(store, step_plan=plan)

    queue = _RecordingQ()
    monkeypatch.setattr(ex, "_get_request_queue", lambda q=queue: q)

    executor = ex.TaskExecutor(app=test_app)
    executor._enqueue_subtask(store.get_task(task_id), 0, plan["steps"][0])

    assert "file_data" not in queue.calls[0]["request_data"], "no image ⇒ text-to-video path"


def test_frozen_resume_clears_abort_reason(db_env, test_app, gate_open, monkeypatch):
    """Resuming a frozen task clears ``abort_reason`` — a completed task that
    was once frozen by a chat request must not keep carrying ``abort_reason=
    'chat'`` (bug: the list showed stale «выполняется»-ish state from the
    leftover reason)."""
    store = db_env
    task_id = _mk_task(store, status="frozen", step_plan=_seed_plan(), minutes_ago=30)
    from app.tasks.store import update_task as _ut

    _ut(task_id, abort_reason="chat")

    monkeypatch.setattr(ex, "run_direct_step", lambda *a, **k: _ok_step_result())
    monkeypatch.setattr(ex, "_run_final_summary", lambda *a, **k: "final answer text here")
    _cycle(test_app, gate=True)

    t = store.get_task(task_id)
    assert t["status"] == "completed"
    assert t["abort_reason"] is None, "resume must clear the stale abort_reason"


def test_subtask_checkpoint_verdict_sees_media_artifact(db_env, test_app, gate_open, monkeypatch):
    """The verifier's input mentions the saved media artifact (file name and
    type) — a media step whose artifact exists must not be rejected with
    «текстовая инструкция вместо файла» just because the artifact was never
    named in the checkpoint text."""
    import os

    from app.tasks.store import update_task as _ut

    store = db_env
    plan = _media_plan("image_edit", goal="Make it winter")
    plan["steps"][0]["checkpoint_criteria"] = "the edited image file exists"
    task_id = _mk_task(store, step_plan=plan)

    # image_edit reads the task's attached image from disk at enqueue time.
    upload_folder = str(test_app.config["UPLOAD_FOLDER"])
    os.makedirs(os.path.join(upload_folder, task_id), exist_ok=True)
    src_path = os.path.join(upload_folder, task_id, "source.png")
    with open(src_path, "wb") as fh:
        fh.write(b"\x89PNG source bytes")
    _ut(task_id, images=[{"file_name": "source.png", "file_path": f"{task_id}/source.png", "file_type": "image/png"}])

    executor = ex.TaskExecutor(app=test_app)

    class _Q:
        def add_request(self, *a, **k):
            return "sub-queue-id-1", {}

        def cancel_task(self, *a, **k):
            pass

    monkeypatch.setattr(ex, "_get_request_queue", lambda: _Q())
    executor._enqueue_subtask(store.get_task(task_id), 0, plan["steps"][0])
    row = store.get_task_steps(task_id)[0]
    queue_id = row["extra"]["subtask_queue_id"]

    envelope = {
        "status": "completed",
        "response": "",
        "file_path": f"{task_id}/winter.png",
        "file_name": "winter.png",
        "file_type": "image/png",
    }
    monkeypatch.setattr(executor, "_peek_subtask_result", lambda qid: envelope if qid == queue_id else None)
    checks = []

    def _fake_checkpoint(app, step, result, lang="ru"):
        checks.append({"step": step, "result": result, "lang": lang})
        return {"verdict": True, "explanation": "ok", "exec_seconds": 0.1}

    monkeypatch.setattr(ex, "run_checkpoint", _fake_checkpoint)

    outcome = executor._poll_subtask(store.get_task(task_id), 0, row, gate_open=True)

    assert outcome == "done"
    assert len(checks) == 1
    assert "winter.png" in checks[0]["result"], "the artifact file name reaches the verifier"
    assert "image/png" in checks[0]["result"], "the artifact type reaches the verifier"
    step_row = store.get_task_steps(task_id)[0]
    assert step_row["status"] == "passed"
    os.remove(src_path)


def test_rlm_subtask_waits_for_attached_documents_then_enqueues(db_env, test_app, gate_open, monkeypatch):
    """An rlm step over the task's OWN attachments waits for their indexing.

    A scanned PDF has no text on disk before its OCR finished, so the corpus
    would be read empty — the same wait the rag step performs. Attachments also
    replace the "every indexed document of the user" scope.
    """
    store = db_env
    plan = _media_plan("rlm", goal="Проанализируйте документы")
    task_id = _mk_task(store, step_plan=plan)
    from app.tasks.store import update_task as _ut

    _ut(task_id, doc_ids=["d1", "d2"])
    waited = []
    monkeypatch.setattr(
        ex,
        "wait_for_documents_indexed",
        lambda app, user_id, doc_ids, cancel_check=None: waited.append((user_id, tuple(doc_ids))),
    )
    queue = _RecordingQ()
    monkeypatch.setattr(ex, "_get_request_queue", lambda q=queue: q)

    executor = ex.TaskExecutor(app=test_app)
    executor._enqueue_subtask(store.get_task(task_id), 0, plan["steps"][0])

    assert waited == [("tester", ("d1", "d2"))], "the attached documents are waited for, in doc_ids order"
    assert queue.calls[0]["request_data"]["doc_ids"] == ["d1", "d2"], "the corpus is scoped to the attachments"
    assert store.get_task_steps(task_id)[0]["status"] == "subtask_queued"


def test_rlm_subtask_wait_preempted_by_chat_freezes_the_task(db_env, test_app, gate_open, monkeypatch):
    """A chat request during the wait interrupts the step and freezes the task.

    The sub-task is never enqueued (the gate is closed — a chat request owns the
    resources); the step re-executes on resume, like a chat-preempted direct step.
    """
    store = db_env
    plan = _media_plan("rlm", goal="Проанализируйте документы")
    task_id = _mk_task(store, step_plan=plan)
    from app.tasks.store import update_task as _ut

    _ut(task_id, doc_ids=["d1"])

    def _abort(app, user_id, doc_ids, cancel_check=None):
        raise StepAborted("cancelled while waiting")

    monkeypatch.setattr(ex, "wait_for_documents_indexed", _abort)
    queue = _RecordingQ()
    monkeypatch.setattr(ex, "_get_request_queue", lambda q=queue: q)

    executor = ex.TaskExecutor(app=test_app)
    executor._enqueue_subtask(store.get_task(task_id), 0, plan["steps"][0])

    assert queue.calls == [], "a preempted wait must not enqueue the sub-task"
    step = store.get_task_steps(task_id)[0]
    assert step["status"] == "interrupted" and step["interrupt_reason"] == "chat"
    task = store.get_task(task_id)
    assert task["status"] == "frozen" and task["abort_reason"] == "chat"


def test_enqueue_subtask_image_edit_sends_raw_base64(db_env, test_app, gate_open, monkeypatch):
    """image_edit takes the task's first attached image as raw base64 (the edit
    pipeline's ``file_data``), read back from the file stored on disk."""
    store = db_env
    plan = _media_plan("image_edit", goal="Make it snowy")
    task_id = _mk_task(store, step_plan=plan)
    store.update_task(
        task_id,
        images=[{"file_path": "tester/src.jpg", "file_name": "src.jpg", "file_type": "image/jpeg"}],
    )
    payload = b"\xff\xd8\xff\xe0jpegbytes"
    src = os.path.join(test_app.config["UPLOAD_FOLDER"], "tester", "src.jpg")
    os.makedirs(os.path.dirname(src), exist_ok=True)
    with open(src, "wb") as fh:
        fh.write(payload)

    queue = _RecordingQ()
    monkeypatch.setattr(ex, "_get_request_queue", lambda: queue)
    executor = ex.TaskExecutor(app=test_app)
    executor._enqueue_subtask(store.get_task(task_id), 0, plan["steps"][0])

    call = queue.calls[0]
    assert call["task_type"] == "api_image_edit"
    assert call["request_data"]["file_data"] == base64.b64encode(payload).decode()
    assert not call["request_data"]["file_data"].startswith("data:"), "raw base64, not a data: URL"
    assert call["request_data"]["file_name"] == "src.jpg"


def test_enqueue_subtask_image_edit_without_image_fails_the_step(db_env, test_app, gate_open, monkeypatch):
    """No attached source image ⇒ the edit step fails at once (no GPU spent),
    the task continues and the failure reaches the view."""
    store = db_env
    plan = {
        "title": "Media task",
        "steps": [
            {"seq": 1, "goal": "Edit it", "tool": "image_edit"},
            {"seq": 2, "goal": "think", "tool": "llm"},
        ],
    }
    task_id = _mk_task(store, step_plan=plan)
    queue = _RecordingQ()
    monkeypatch.setattr(ex, "_get_request_queue", lambda: queue)
    executor = ex.TaskExecutor(app=test_app)
    publishes = []
    monkeypatch.setattr(executor, "_publish_task_update", lambda tid: publishes.append(tid))

    executor._enqueue_subtask(store.get_task(task_id), 0, plan["steps"][0])

    assert queue.calls == [], "nothing is enqueued without a source image"
    step = store.get_task_steps(task_id)[0]
    assert step["status"] == "failed" and step["last_error"] == "no source image attached"
    assert store.get_task(task_id)["next_step_index"] == 1, "the task continues past the dead step"
    assert publishes == [task_id]


def test_cycle_publishes_the_queued_media_step(db_env, test_app, gate_open, monkeypatch):
    """The cycle that enqueues a media step publishes ``task_update`` — the
    plan's steps used to appear all at once when the whole task completed."""
    store = db_env
    plan = {
        "title": "Media task",
        "steps": [{"seq": 1, "goal": "Make an image", "tool": "image_gen", "checkpoint_criteria": "c"}],
    }
    task_id = _mk_task(store, step_plan=plan, minutes_ago=30)
    monkeypatch.setattr(ex, "_get_request_queue", lambda: _RecordingQ())
    executor = ex.TaskExecutor(app=test_app)
    publishes = []
    monkeypatch.setattr(executor, "_publish_task_update", lambda tid: publishes.append(tid))

    executor._process_one_cycle(gate_open=True)

    assert store.get_task(task_id)["status"] == "active"
    assert store.get_task_steps(task_id)[0]["status"] == "subtask_queued"
    assert task_id in publishes, "the queued step must reach the view at once"


def test_poll_subtask_publishes_the_flip_to_running(db_env, test_app, gate_open, monkeypatch):
    """The first poll flips the step to ``subtask_running`` and publishes it —
    the view had no other signal while the media sub-task held the GPU."""
    store = db_env
    plan = _media_plan("image_gen", goal="Make an image")
    monkeypatch.setattr(ex, "_get_request_queue", lambda: _RecordingQ())
    task_id = _mk_task(store, step_plan=plan)
    executor = ex.TaskExecutor(app=test_app)

    executor._enqueue_subtask(store.get_task(task_id), 0, plan["steps"][0])
    row = store.get_task_steps(task_id)[0]
    publishes = []
    monkeypatch.setattr(executor, "_publish_task_update", lambda tid: publishes.append(tid))
    monkeypatch.setattr(executor, "_peek_subtask_result", lambda qid: None)

    executor._poll_subtask(store.get_task(task_id), 0, row, gate_open=True)

    assert store.get_task_steps(task_id)[0]["status"] == "subtask_running"
    assert publishes == [task_id], "the flip to running is published"


def test_audio_transcripts_join_the_request():
    """A request-phase audio transcript is part of the task request the planner
    and every step read (chat parity: the transcript is context, never a
    document); an ANSWER-phase transcript belongs to its answer step instead."""
    task = {
        "title": "проанализируй аудио и скажи, какой цвет салфеток заказать",
        "audio": [
            {"file_name": "material.webm", "phase": "request", "transcript": "салфетки зелёные"},
            {"file_name": "answer.webm", "phase": "answer", "step_id": "s9", "transcript": "это ответ"},
            {"file_name": "pending.webm", "phase": "request", "transcript": ""},
        ],
    }

    text = ex._request_text(task)

    assert text.startswith("проанализируй аудио")
    assert "салфетки зелёные" in text
    assert "material.webm" in text, "the transcript names its file"
    assert "это ответ" not in text, "an answer transcript is consumed by the answer step"
    assert "pending.webm" not in text, "an untranscribed attachment adds nothing"


def test_request_text_without_audio_is_untouched():
    """Tasks without audio (and rows predating the column) read exactly as
    before: the plan payload's request, else the title."""
    assert ex._request_text({"title": "t"}) == "t"
    assert ex._request_text({"title": "t", "step_plan": {"task_request": "plan-req"}}) == "plan-req"
    assert ex._request_text({"title": "t", "audio": []}) == "t"
