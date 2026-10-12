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

import os
import time
from datetime import UTC, datetime, timedelta

import pytest

import app.tasks.executor as ex
from app.tasks.runner import StepAborted

pytestmark = [pytest.mark.unit]

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
    assert len(checks) == 1 and checks[0]["result"] == "an image was generated"
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

    def _fake_planner(app, prompt, cancel_check, lang):
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
