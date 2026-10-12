# app/routes/tasks.py
"""Internal API for the Tasks (Задачи) subsystem (v12.6, spec §5).

Owner-scoped like sessions: a task belongs to the login stored in its
``user_id``; the admin flag widens access. The conversation is rendered from
``task_steps`` (the user request, the plan, each step with its «Шаг N из M:»
header, clarifying questions/answers and the final answer) — there is no
parallel messages table.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from flask import Blueprint, current_app, jsonify, request, session
from flask_babel import force_locale
from flask_babel import gettext as _

from app.database import get_db
from app.tasks.events import publish_task_update
from app.tasks.executor import (
    add_user_input_step,
    append_deep_analysis_step,
    get_queued_task_positions,
    get_task_steps,
)
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


def _save_task_images(uploads: list[Any]) -> tuple[list[dict[str, str]] | None, str | None, int | None]:
    """Save attached images of a task request/answer; return (parts, error, status).

    Chat parity: up to ``MAX_CHAT_IMAGES`` images, quota-checked, downscaled
    (single → ``MAX_IMAGE_SIZE``, several → ``MAX_IMAGE_SIZE_MULTI``), stored
    under the user's upload folder via ``save_uploaded_file``. Only
    ``{file_path, file_name}`` dicts persist — bytes never enter the DB.
    """
    import base64 as _b64
    import mimetypes

    from app.utils import check_upload_quota, resize_image_if_needed, save_uploaded_file

    max_images = current_app.config.get("MAX_CHAT_IMAGES", 4)
    max_size = current_app.config.get("MAX_IMAGE_SIZE", 1536)
    if len(uploads) > 1:
        max_size = current_app.config.get("MAX_IMAGE_SIZE_MULTI", 1024)
    user_id = session["login"]
    lang = session.get("language", "ru")
    images: list[dict[str, str]] = []
    for upload in uploads:
        file_bytes = upload.read()
        part_size = len(file_bytes)
        part_type = upload.content_type or mimetypes.guess_type(upload.filename)[0] or "application/octet-stream"
        part_name = upload.filename
        if not part_type.startswith("image/"):
            with force_locale(lang):
                return None, _("Only image files can be attached to a task"), 400
        if len(images) >= max_images:
            with force_locale(lang):
                return None, _("max_images_reached") % max_images, 400
        quota_error = check_upload_quota(user_id, part_size)
        if quota_error:
            return None, quota_error, 413
        part_data = _b64.b64encode(file_bytes).decode("utf-8")
        n_data, _n_type, _n_name, _r, _od, _nd = resize_image_if_needed(part_data, part_type, part_name, max_size)
        rel_path = save_uploaded_file(
            file_data=n_data,
            filename=part_name,
            session_id=str(uuid.uuid4()),
            upload_folder=current_app.config.get("UPLOAD_FOLDER", "data/uploads"),
            user_id=user_id,
        )
        if not rel_path:
            with force_locale(lang):
                return None, _("Failed to save file"), 500
        images.append({"file_path": rel_path, "file_name": part_name})
    return images, None, None


def _truthy_flag(value: Any) -> bool:
    """Client boolean flag: real booleans (JSON) and 'true'/'1' (form) are true."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def _clean_doc_ids(value: Any) -> list[str]:
    """Coerce a client ``doc_ids`` payload into a list of non-empty id strings.

    Accepts a JSON array (fetch JSON body) and a JSON string (multipart form
    field — the browser sends ``JSON.stringify`` output as text).
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return []
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if v]


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


def _task_summary(
    task: dict,
    token_totals: dict[str, dict[str, int]] | None = None,
    queue_positions: dict[str, int] | None = None,
) -> dict:
    """List-item / status payload (no filesystem paths, no plan bodies).

    ``token_totals`` (optional) maps task_id → summed step usage; provided by
    the list route so list items can show [▲out ▼in] like session cards.
    ``queue_positions`` (optional) maps task_id → 1-based queue rank; the list
    route renders it as the ⏳ badge on ``queued`` rows.
    """
    summary = {
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
        "response_style": task.get("response_style") or "neutral",
        # Attached documents (empty = all of the user's documents).
        "doc_ids": [str(d) for d in (task.get("doc_ids") or [])],
        # Attached images (names only — no filesystem paths, no bytes).
        "images": [str(i.get("file_name") or "") for i in (task.get("images") or []) if isinstance(i, dict)],
        # 🔬 Deep-analysis toggle state at submit: the planner must include an
        # rlm step over the attached documents.
        "deep_analysis": bool(task.get("deep_analysis")),
        # Total accumulated execution time (LLM calls only, waits excluded —
        # the budget's own accounting); rendered as ⏱️ in the view.
        "exec_seconds": float(task.get("accumulated_exec_seconds") or 0.0),
    }
    if token_totals is not None:
        summary["token_totals"] = token_totals.get(task["id"]) or {"prompt_tokens": 0, "completion_tokens": 0}
    if queue_positions and task.get("status") == "queued":
        summary["queue_position"] = queue_positions.get(str(task["id"]), 0)
    return summary


def _token_totals_for(task_ids: list[str]) -> dict[str, dict[str, int]]:
    """Per-task summed step usage (prompt/completion tokens) from step extras."""
    if not task_ids:
        return {}
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT task_id,
                   COALESCE(SUM((extra->'usage'->>'prompt_tokens')::bigint), 0) AS p,
                   COALESCE(SUM((extra->'usage'->>'completion_tokens')::bigint), 0) AS c
            FROM task_steps WHERE task_id::text = ANY(%s) GROUP BY task_id
            """,
            (task_ids,),
        )
        rows = c.fetchall()
    return {r["task_id"]: {"prompt_tokens": int(r["p"]), "completion_tokens": int(r["c"])} for r in rows}


def _tool_badge(tool: Any) -> str:
    """Localized «🧮 Calculator:» label for a tool-produced step body (or "").

    Empty for the reasoning-model step (the model answers) and unknown tools.
    The emoji lives in ``TOOL_BADGES``; the name is a gettext msgid rendered
    inside the request locale, so the label follows the user's language.
    """
    from app.tasks.tools import TOOL_BADGES

    badge = TOOL_BADGES.get(str(tool or ""))
    if not badge:
        return ""
    emoji, msgid = badge
    return f"{emoji} {_(msgid)}:"


def _step_message(row: dict, total: int) -> dict:
    """One task_steps row as a conversation message."""
    seq = int(row.get("seq") or 0)
    kind = row.get("kind") or "direct"
    body = str(row.get("result") or "")
    header = _("Step {n} of {m}:").format(n=seq, m=total)
    # A tool-produced step names its tool on its own line above the answer; the
    # client renders it as a second italic service line, same font as the step
    # header. The model step has no badge and answers on its own.
    badge = _tool_badge(row.get("tool")) if kind in ("direct", "subtask") else ""
    if kind in ("direct", "subtask"):
        lines = [header] + ([badge] if badge else []) + ([body] if body else [])
        # A failed step with no result body must still show WHY it failed
        # (chat parity: errors are visible, never silently blank).
        if not body and row.get("status") == "failed" and row.get("last_error"):
            lines.append(str(row["last_error"]))
        content = "\n".join(lines)
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
        "badge": badge,
        "seq": seq,
        "total_steps": total,
        "status": row.get("status"),
        "tool": row.get("tool") or "",
        "exec_seconds": float(row.get("exec_seconds") or 0.0),
        "model_name": (row.get("extra") or {}).get("model_name", ""),
        "usage": (row.get("extra") or {}).get("usage") or {},
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
        image_names = [str(i.get("file_name") or "") for i in (task.get("images") or []) if isinstance(i, dict)]
        image_names = [n for n in image_names if n]
        if image_names:
            request_text = request_text + "\n\n" + _("Attached images: {names}").format(names=", ".join(image_names))
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
    totals = _token_totals_for([t["id"] for t in tasks])
    positions = get_queued_task_positions()
    return jsonify({"tasks": [_task_summary(t, totals, positions) for t in tasks]})


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
        doc_ids=_clean_doc_ids(data.get("doc_ids")),
        deep_analysis=_truthy_flag(data.get("deep_analysis")),
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

    data: dict[str, Any] = {}
    uploads: list[Any] = []
    if request.content_type and "multipart/form-data" in request.content_type:
        # Vision flow (v12.6): the request may carry attached images (chips in
        # the task view). JSON fields ride the form alongside the files.
        data = {key: val for key, val in request.form.items()}
        uploads = [f for f in request.files.getlist("images") if f and f.filename]
    else:
        data = request.get_json(silent=True) or {}

    user_request = str(data.get("request") or "").strip()
    if not user_request:
        return jsonify({"error": "⚠️ " + _("Task request is empty")}), 400

    images = None
    if uploads:
        images, img_error, img_status = _save_task_images(uploads)
        if img_error:
            return jsonify({"error": "⚠️ " + img_error}), img_status

    update_task(task_id, status="queued", title=user_request[:300], abort_reason=None)
    if "doc_ids" in data:
        # Snapshot the Documents-panel selection at submit time (like a chat
        # request): an empty list means "all documents".
        update_task(task_id, doc_ids=_clean_doc_ids(data.get("doc_ids")))
    if images:
        update_task(task_id, images=images)
    if "deep_analysis" in data:
        # The 🔬 toggle state rides the request: the planner is told to include
        # a deep-analysis (rlm) step over the attached documents.
        update_task(task_id, deep_analysis=_truthy_flag(data.get("deep_analysis")))
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

    data: dict[str, Any] = {}
    uploads: list[Any] = []
    if request.content_type and "multipart/form-data" in request.content_type:
        data = {key: val for key, val in request.form.items()}
        uploads = [f for f in request.files.getlist("images") if f and f.filename]
    else:
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
    if "doc_ids" in data:
        # The answer may carry a fresh Documents-panel selection — it scopes
        # every remaining rag/rlm step of the task.
        update_task(task_id, doc_ids=_clean_doc_ids(data.get("doc_ids")))
    if uploads:
        images, img_error, img_status = _save_task_images(uploads)
        if img_error:
            return jsonify({"error": "⚠️ " + img_error}), img_status
        if images:
            merged = list(task.get("images") or []) + images
            update_task(task_id, images=merged)
    if "deep_analysis" in data and _truthy_flag(data.get("deep_analysis")):
        # The toggle switched ON while answering: the plan already exists and
        # the planner cannot amend it, so a deep-analysis (rlm) step over the
        # attached documents is appended to the plan for the remaining steps.
        update_task(task_id, deep_analysis=True)
        append_deep_analysis_step(task_id, lang=task.get("lang") or "ru")
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
