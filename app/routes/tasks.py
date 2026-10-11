# app/routes/tasks.py
"""Internal API for the Tasks (Задачи) subsystem (v12.6, spec §5).

Owner-scoped like sessions: a task belongs to the login stored in its
``user_id``; the admin flag widens access. The conversation is rendered from
``task_steps`` (the user request, the plan, each step with its «Шаг N из M:»
header, clarifying questions/answers and the final answer) — there is no
parallel messages table.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, jsonify, request, session
from flask_babel import gettext as _

from app.tasks.events import publish_task_update
from app.tasks.executor import add_user_input_step, get_task_steps
from app.tasks.store import create_task, delete_task, get_task, get_tasks_by_user, update_task

bp = Blueprint("tasks", __name__, url_prefix="/api/tasks")

# User-facing service_class (0/1/2) → low-priority queue task_class (3/4/5).
_SERVICE_TO_TASK_CLASS = {0: 3, 1: 4, 2: 5}
_TERMINAL_STATUSES = {"completed", "failed", "expired", "cancelled"}
# Cancel flags survive until the executor consumes them; a TTL keeps a flag set
# on an already-terminal task from lingering forever.
_CANCEL_FLAG_TTL_S = 3600


def _iso(value: Any) -> str | None:
    """Serialize a DB timestamp for the client (datetime → ISO, None stays None)."""
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _owned_task(task_id: str) -> dict | None:
    """The task when the caller owns it (or is admin), else None (→ 404)."""
    task = get_task(task_id)
    if task is None:
        return None
    if not session.get("is_admin") and task.get("user_id") != session.get("login"):
        return None
    return task


def _task_request(task: dict) -> str:
    """The user's original request (kept in the plan payload at plan phase)."""
    plan = task.get("step_plan") or {}
    if isinstance(plan, dict):
        return str(plan.get("task_request") or task.get("title") or "")
    return str(task.get("title") or "")


def _plan_steps(task: dict) -> list[dict]:
    plan = task.get("step_plan") or {}
    steps = plan.get("steps", []) if isinstance(plan, dict) else (plan or [])
    return [s for s in (steps or []) if isinstance(s, dict)]


def _task_summary(task: dict) -> dict:
    """List-item / status payload (no filesystem paths, no plan bodies)."""
    return {
        "id": task["id"],
        "title": task.get("title") or "",
        "status": task.get("status"),
        "task_class": task.get("task_class"),
        "next_step_index": task.get("next_step_index") or 0,
        "total_steps": len(_plan_steps(task)),
        "created_at": _iso(task.get("created_at")),
        "started_at": _iso(task.get("started_at")),
        "completed_at": _iso(task.get("completed_at")),
        "abort_reason": task.get("abort_reason"),
        # Total accumulated execution time (LLM calls only, waits excluded —
        # the budget's own accounting); rendered as ⏱️ in the view.
        "exec_seconds": float(task.get("accumulated_exec_seconds") or 0.0),
    }


def _step_message(row: dict, total: int) -> dict:
    """One task_steps row as a conversation message."""
    seq = int(row.get("seq") or 0)
    kind = row.get("kind") or "direct"
    body = str(row.get("result") or "")
    header = _("Step {n} of {m}:").format(n=seq, m=total)
    if kind in ("direct", "subtask"):
        content = f"{header}\n{body}" if body else header
    elif kind == "question":
        # The goal IS the verbatim question (prompt rule) — show it as a plain
        # assistant message; the waiting state lives in the banner/live status.
        content = body
    else:  # user_input / final / plan — no step header
        content = body
    return {
        "id": row.get("id"),
        "role": "user" if kind == "user_input" else "assistant",
        "kind": kind,
        "content": content,
        "seq": seq,
        "total_steps": total,
        "status": row.get("status"),
        "tool": row.get("tool") or "",
        "exec_seconds": float(row.get("exec_seconds") or 0.0),
        "model_name": (row.get("extra") or {}).get("model_name", ""),
        "extra": row.get("extra") or {},
        # task_steps has no created_at: a step's timestamp is when it started
        # running (a queued step with no timestamp renders without a date).
        "created_at": _iso(row.get("started_at") or row.get("finished_at")),
    }


def _render_messages(task: dict) -> list[dict]:
    """The full task conversation from ``task_steps`` + the plan payload."""
    plan_steps = _plan_steps(task)
    total = len(plan_steps)
    messages: list[dict] = []
    request_text = _task_request(task)
    if request_text:
        # A draft task has no request yet — the conversation starts empty.
        messages.append(
            {
                "id": f"{task['id']}:request",
                "role": "user",
                "kind": "request",
                "content": request_text,
                "seq": 0,
                "total_steps": total,
                "status": None,
                "tool": "",
                "extra": {},
                "created_at": _iso(task.get("created_at")),
            }
        )
    if total:
        lines = [f"{i + 1}. {s.get('goal', '')}" for i, s in enumerate(plan_steps)]
        messages.append(
            {
                "id": f"{task['id']}:plan",
                "role": "assistant",
                "kind": "plan",
                "content": _("Plan of {n} steps:").format(n=total) + "\n" + "\n".join(lines),
                "seq": 0,
                "total_steps": total,
                "status": None,
                "tool": "",
                "extra": {},
                "created_at": _iso(task.get("created_at")),
            }
        )
    for row in get_task_steps(task["id"]):
        if row.get("status") == "pending" and not str(row.get("result") or "").strip():
            continue  # not materialized yet — nothing to show
        messages.append(_step_message(row, total))
    return messages


@bp.route("", methods=["GET"])
def list_tasks():
    if "login" not in session:
        return jsonify({"error": "⚠️ " + _("Not authorized")}), 401
    tasks = get_tasks_by_user(session["login"])
    return jsonify({"tasks": [_task_summary(t) for t in tasks]})


@bp.route("", methods=["POST"])
def create_task_route():
    if "login" not in session:
        return jsonify({"error": "⚠️ " + _("Not authorized")}), 401
    if not current_app.config.get("TASKS_ENABLED"):
        return jsonify({"error": "⚠️ " + _("Background tasks are disabled")}), 403

    data = request.get_json(silent=True) or {}
    user_request = str(data.get("request") or "").strip()

    # A draft ("+" flow, like a new chat session): no request yet — the user
    # types it into the task view's input, PATCH submits it. The executor
    # never selects a draft task. A request in the body keeps the
    # non-draft creation for API compatibility.
    service_class = session.get("service_class", 2)
    task_class = _SERVICE_TO_TASK_CLASS.get(service_class, 5)
    task_id = create_task(
        user_id=session["login"],
        task_class=task_class,
        title=user_request[:300],
        lang=session.get("language", "ru"),
        response_style=session.get("response_style", "neutral"),
    )
    if not user_request:
        update_task(task_id, status="draft")
    task = get_task(task_id)
    publish_task_update(current_app._get_current_object(), task)
    return jsonify({"task": _task_summary(task)}), 202


@bp.route("/<task_id>/request", methods=["POST"])
def task_request_route(task_id):
    """Submit the draft's request text: draft → queued (the planner picks it up)."""
    if "login" not in session:
        return jsonify({"error": "⚠️ " + _("Not authorized")}), 401
    task = _owned_task(task_id)
    if task is None:
        return jsonify({"error": "⚠️ " + _("Task not found")}), 404
    if task.get("status") != "draft":
        return jsonify({"error": "⚠️ " + _("Task is not a draft")}), 409

    data = request.get_json(silent=True) or {}
    user_request = str(data.get("request") or "").strip()
    if not user_request:
        return jsonify({"error": "⚠️ " + _("Task request is empty")}), 400

    update_task(task_id, status="queued", title=user_request[:300], abort_reason=None)
    task = get_task(task_id)
    publish_task_update(current_app._get_current_object(), task)
    return jsonify({"task": _task_summary(task)}), 202


@bp.route("/<task_id>/messages", methods=["GET"])
def task_messages(task_id):
    if "login" not in session:
        return jsonify({"error": "⚠️ " + _("Not authorized")}), 401
    task = _owned_task(task_id)
    if task is None:
        return jsonify({"error": "⚠️ " + _("Task not found")}), 404
    return jsonify({"task": _task_summary(task), "messages": _render_messages(task)})


@bp.route("/<task_id>/answer", methods=["POST"])
def task_answer(task_id):
    if "login" not in session:
        return jsonify({"error": "⚠️ " + _("Not authorized")}), 401
    task = _owned_task(task_id)
    if task is None:
        return jsonify({"error": "⚠️ " + _("Task not found")}), 404

    data = request.get_json(silent=True) or {}
    answer = str(data.get("answer") or "").strip()
    if not answer:
        return jsonify({"error": "⚠️ " + _("Answer is empty")}), 400

    question = next(
        (
            s
            for s in reversed(get_task_steps(task_id))
            if s.get("kind") == "question" and s.get("status") == "awaiting_user"
        ),
        None,
    )
    if question is None:
        return jsonify({"error": "⚠️ " + _("Task is not awaiting an answer")}), 409

    add_user_input_step(task_id, int(question["seq"]), answer)
    publish_task_update(current_app._get_current_object(), get_task(task_id))
    return jsonify({"ok": True}), 202


@bp.route("/<task_id>/stop", methods=["POST"])
def task_stop(task_id):
    if "login" not in session:
        return jsonify({"error": "⚠️ " + _("Not authorized")}), 401
    task = _owned_task(task_id)
    if task is None:
        return jsonify({"error": "⚠️ " + _("Task not found")}), 404
    if task.get("status") in _TERMINAL_STATUSES:
        return jsonify({"ok": True, "status": task["status"]})

    # Raise the executor's cancel flag; a task with no step in flight is
    # cancelled immediately (the executor never sees a flag for a queued task
    # whose plan phase has not started yet).
    try:
        current_app.request_queue.redis.set(f"task:cancelled:{task_id}", "1", ex=_CANCEL_FLAG_TTL_S)
    except Exception as e:  # noqa: BLE001 — the flag is best effort
        current_app.logger.warning(f"task cancel flag write failed ({task_id}): {e}")
    if task.get("status") in ("queued", "frozen", "awaiting_user"):
        update_task(task_id, status="cancelled")
        task = get_task(task_id) or task

    publish_task_update(current_app._get_current_object(), task)
    return jsonify({"ok": True, "status": task.get("status")})


@bp.route("/<task_id>/delete", methods=["POST"])
def task_delete(task_id):
    if "login" not in session:
        return jsonify({"error": "⚠️ " + _("Not authorized")}), 401
    task = _owned_task(task_id)
    if task is None:
        return jsonify({"error": "⚠️ " + _("Task not found")}), 404
    if task.get("status") not in _TERMINAL_STATUSES:
        # The executor must never resume a deleted task: raise its cancel flag
        # and mark it cancelled before removing the rows.
        try:
            current_app.request_queue.redis.set(f"task:cancelled:{task_id}", "1", ex=_CANCEL_FLAG_TTL_S)
        except Exception as e:  # noqa: BLE001 — best effort
            current_app.logger.warning(f"task cancel flag write failed ({task_id}): {e}")
        update_task(task_id, status="cancelled")
    delete_task(task_id)
    publish_task_update(
        current_app._get_current_object(),
        {"id": task["id"], "user_id": task["user_id"], "status": "deleted"},
    )
    return jsonify({"status": "ok"})
