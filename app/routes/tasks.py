# app/routes/tasks.py
"""Internal API for the Tasks (Задачи) subsystem (v12.6, spec §5).

Owner-scoped like sessions: a task belongs to the login stored in its
``user_id``; the admin flag widens access. The conversation is rendered from
``task_steps`` (the user request, the plan, each step with its «Шаг N из M:»
header, clarifying questions/answers and the final answer) — there is no
parallel messages table.
"""

from __future__ import annotations

import base64 as _b64
import json
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
from app.tasks.store import create_task, delete_task, get_task, get_tasks_by_user, update_step, update_task

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


def _save_task_images(uploads: list[Any], task_id: str) -> tuple[list[dict[str, str]] | None, str | None, int | None]:
    """Save attached images of a task request/answer; return (parts, error, status).

    Chat parity: up to ``MAX_CHAT_IMAGES`` images, quota-checked, downscaled
    (single → ``MAX_IMAGE_SIZE``, several → ``MAX_IMAGE_SIZE_MULTI``), stored
    under the user's upload folder via ``save_uploaded_file``. Only
    ``{file_path, file_name, file_type}`` dicts persist — bytes never enter
    the DB. The files land in the folder named after the TASK id (like the
    camera/vision sub-task media, executor ``_enqueue_subtask``), so
    ``/api/files`` resolves ownership through the tasks table — a random
    folder id was rejected with 403 and the task view showed no image.
    """
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
            session_id=task_id,
            upload_folder=current_app.config.get("UPLOAD_FOLDER", "data/uploads"),
            user_id=user_id,
        )
        if not rel_path:
            with force_locale(lang):
                return None, _("Failed to save file"), 500
        images.append({"file_path": rel_path, "file_name": part_name, "file_type": part_type})
    return images, None, None


def _save_task_documents(uploads: list[Any], task_id: str) -> tuple[list[str] | None, str | None, int | None]:
    """Save documents attached to a task request/answer; return (doc_ids, error, status).

    Chat/Documents parity (a document attached with the paperclip used to be
    dropped silently): every upload is validated and stored as a FULL user
    document — ``DOCUMENTS_FOLDER/<login>/<doc_id><ext>`` plus a ``documents``
    row — and its indexing is enqueued, exactly like a Documents-panel upload.
    Only the ids leave here: the task stores them in ``doc_ids``, which scopes
    every rag/rlm step and renders the attachment line in the conversation.
    """
    import os
    import uuid
    from io import BytesIO

    from app import db
    from app.routes.documents import validate_file
    from app.utils import check_document_quota

    documents_folder = current_app.config["DOCUMENTS_FOLDER"]
    user_id = session["login"]
    lang = session.get("language", "ru")
    max_size_mb = current_app.config["MAX_DOCUMENT_SIZE_MB"]
    doc_ids: list[str] = []
    for upload in uploads:
        file_content = upload.read()
        filename = upload.filename or "document"
        is_valid, error_message = validate_file(BytesIO(file_content), filename)
        if not is_valid:
            return None, error_message, 400
        if len(file_content) > max_size_mb * 1024 * 1024:
            with force_locale(lang):
                return None, _("Maximum file size {max_size} MB").format(max_size=max_size_mb), 400
        quota_error = check_document_quota(user_id)
        if quota_error:
            return None, quota_error, 413

        doc_id = str(uuid.uuid4())
        user_folder = os.path.join(documents_folder, user_id)
        os.makedirs(user_folder, exist_ok=True)
        safe_ext = os.path.splitext(filename)[1].lower()
        safe_filename = f"{doc_id}{safe_ext}"
        file_path = os.path.join(user_folder, safe_filename)
        # Double-check the path does not escape DOCUMENTS_FOLDER.
        if not os.path.realpath(file_path).startswith(os.path.realpath(user_folder) + os.sep):
            with force_locale(lang):
                return None, _("Invalid file path"), 400
        with open(file_path, "wb") as f:
            f.write(file_content)

        db.save_document(
            user_id,
            doc_id,
            filename,
            len(file_content),
            file_ext=safe_ext,
            file_path=os.path.join(user_id, safe_filename),
        )
        db.update_document_index_status(doc_id, db.INDEX_STATUS_PENDING)
        current_app.request_queue.add_request(
            user_id=user_id,
            session_id="",  # Document indexing does not belong to a chat session
            request_data={"type": "index_document", "doc_id": doc_id, "file_path": file_path},
            user_class=session.get("user_class", 100),
            lang=lang,
        )
        doc_ids.append(doc_id)
    return doc_ids, None, None


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


def _document_names(task: dict) -> list[str]:
    """File names of the task's attached documents, in ``doc_ids`` order.

    ``doc_ids`` carries ids only, so the names come from the user's document
    list. An id whose document was deleted since is skipped.
    """
    doc_ids = [str(d) for d in (task.get("doc_ids") or []) if d]
    if not doc_ids:
        return []
    from app.db import get_user_documents

    by_id = {
        str(d.get("id")): str(d.get("filename") or "") for d in (get_user_documents(task.get("user_id") or "") or [])
    }
    return [by_id[d] for d in doc_ids if by_id.get(d)]


def _task_summary(
    task: dict,
    token_totals: dict[str, dict[str, int]] | None = None,
    queue_positions: dict[str, int] | None = None,
) -> dict:
    """List-item / status payload (no filesystem paths, no plan bodies).

    ``token_totals`` (optional) maps task_id → summed step usage; provided by
    the list route so list items can show [▲requests ▼answers] like session
    cards (▲ = outgoing prompt tokens, ▼ = incoming completion tokens).
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
        "model_type": (row.get("extra") or {}).get("model_type", ""),
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
        # The attachment names never ride inside the request text: they travel
        # as their own field so the task view renders them as a service line
        # (the plan line's font) with one file name per line.
        attached_images = _("Attached images: {names}").format(names="\n".join(image_names)) if image_names else ""
        doc_names = _document_names(task)
        attached_documents = _("Attached documents: {names}").format(names="\n".join(doc_names)) if doc_names else ""
        # Expose the first task image as extra.media so JS renders it visually.
        req_extra: dict[str, Any] = {}
        task_images = task.get("images") or []
        if task_images and isinstance(task_images, list):
            first_img = task_images[0]
            if isinstance(first_img, dict) and first_img.get("file_path"):
                req_extra["media"] = {
                    k: first_img[k] for k in ("file_path", "file_name", "file_type", "file_size") if k in first_img
                }
                if "file_type" not in req_extra.get("media", {}):
                    req_extra["media"]["file_type"] = "image/jpeg"
        messages.append(
            {
                "id": f"{task['id']}:request",
                "role": "user",
                "kind": "request",
                "content": request_text,
                "attached_images": attached_images,
                "attached_documents": attached_documents,
                "seq": 0,
                "total_steps": total,
                "status": None,
                "tool": "",
                "extra": req_extra,
                "created_at": _iso(task.get("created_at")),
            }
        )
    if total:
        lines = [f"{i + 1}. {s.get('goal', '')}" for i, s in enumerate(plan_steps)]
        # The plan message carries the same chat-style header as a step
        # (model + real tokens + duration of the planner call), stored with
        # the plan itself.
        plan_meta = task.get("step_plan").get("plan_meta") if isinstance(task.get("step_plan"), dict) else None
        plan_extra = plan_meta if isinstance(plan_meta, dict) else {}
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
                "exec_seconds": float(plan_extra.get("exec_seconds") or 0.0),
                "model_name": str(plan_extra.get("model_name") or ""),
                "model_type": str(plan_extra.get("model_type") or ""),
                "usage": plan_extra.get("usage") or {},
                "extra": {},
                "created_at": _iso(task.get("created_at")),
            }
        )
    steps = get_task_steps(task["id"])
    by_id = {r["id"]: r for r in steps}

    def _chrono_key(row: dict) -> tuple[float, int]:
        """Chronological anchor for sorting: a clarifying question and its
        answer belong to the step that asked them (the runtime question points
        at the direct step via ``parent_step_id``, the answer at the question),
        so they render at the parent's plan position — before the re-run step,
        matching the real order — instead of at the tail after the final step.
        """
        seq = int(row.get("seq") or 0)
        kind = row.get("kind")
        parent = by_id.get(row.get("parent_step_id")) if row.get("parent_step_id") else None
        if kind == "user_input":
            if parent is not None and parent.get("kind") == "question":
                grand = by_id.get(parent.get("parent_step_id")) if parent.get("parent_step_id") else None
                anchor = int(grand["seq"]) if grand is not None else int(parent.get("seq") or 0)
                return (anchor, 2)
            return (seq, 2)
        if kind == "question":
            anchor = int(parent["seq"]) if parent is not None else seq
            return (anchor, 1)
        if kind in ("direct", "subtask", "final") and any(
            q.get("kind") == "question" and q.get("parent_step_id") == row["id"] for q in steps
        ):
            # A step that got a clarifying question re-runs after it: place the
            # re-run result after the Q&A pair instead of before it.
            return (seq, 3)
        return (seq, 0)

    for row in sorted(steps, key=_chrono_key):
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
    doc_uploads: list[Any] = []
    if request.content_type and "multipart/form-data" in request.content_type:
        # Vision flow (v12.6): the request may carry attached images (chips in
        # the task view). JSON fields ride the form alongside the files.
        data = {key: val for key, val in request.form.items()}
        uploads = [f for f in request.files.getlist("images") if f and f.filename]
        doc_uploads = [f for f in request.files.getlist("files") if f and f.filename]
    else:
        data = request.get_json(silent=True) or {}

    user_request = str(data.get("request") or "").strip()
    if not user_request:
        return jsonify({"error": "⚠️ " + _("Task request is empty")}), 400

    images = None
    if uploads:
        images, img_error, img_status = _save_task_images(uploads, task_id)
        if img_error:
            return jsonify({"error": "⚠️ " + img_error}), img_status

    # Paperclip documents become full user documents (Documents parity); a
    # failure keeps the task a draft so nothing is queued half-attached.
    new_doc_ids: list[str] = []
    if doc_uploads:
        saved_ids, doc_error, doc_status = _save_task_documents(doc_uploads, task_id)
        if doc_error:
            return jsonify({"error": "⚠️ " + doc_error}), doc_status
        new_doc_ids = saved_ids or []

    update_task(task_id, status="queued", title=user_request[:300], abort_reason=None)
    if "doc_ids" in data or new_doc_ids:
        # Snapshot the Documents-panel selection at submit time (like a chat
        # request): an empty list means "all documents". Documents just saved
        # from paperclip attachments come first and scope the task too — a
        # submit that attached files must never fall back to "all".
        selected = _clean_doc_ids(data.get("doc_ids"))
        update_task(task_id, doc_ids=new_doc_ids + [d for d in selected if d not in new_doc_ids])
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
    doc_uploads: list[Any] = []
    if request.content_type and "multipart/form-data" in request.content_type:
        data = {key: val for key, val in request.form.items()}
        uploads = [f for f in request.files.getlist("images") if f and f.filename]
        doc_uploads = [f for f in request.files.getlist("files") if f and f.filename]
    else:
        data = request.get_json(silent=True) or {}
    answer = str(data.get("answer") or data.get("request") or "").strip()
    if not answer and not uploads:
        # Log what the client actually sent — a cached pre-fix page posts the
        # draft field "request" to /answer, which used to answer «Ответ пуст».
        current_app.logger.warning(
            f"task answer empty: form keys={sorted(request.form.keys() if hasattr(request, 'form') else [])}"
        )
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

    if "doc_ids" in data and not doc_uploads:
        # The answer may carry a fresh Documents-panel selection — it scopes
        # every remaining rag/rlm step of the task. With attached documents the
        # update moves past the de-duplication guard below (a resent form must
        # not save the same documents twice).
        update_task(task_id, doc_ids=_clean_doc_ids(data.get("doc_ids")))
    images: list[dict[str, str]] = []
    ui_media: dict[str, Any] | None = None
    if uploads:
        images, img_error, img_status = _save_task_images(uploads, task_id)
        if img_error:
            return jsonify({"error": "⚠️ " + img_error}), img_status
        if images:
            merged = list(task.get("images") or []) + images
            update_task(task_id, images=merged)
            # First image becomes the conversation-view thumbnail (the JS
            # renderer shows images when extra.media has a file_path).
            first = images[0]
            ui_media = {k: first[k] for k in ("file_path", "file_name", "file_type", "file_size") if k in first}
            if not ui_media.get("file_type"):
                ui_media["file_type"] = "image/jpeg"
    if not answer.strip() and images:
        # Image-only answer: at least let the reasoning model know there IS
        # an image attached (the background description below appends the
        # full text to the step result as soon as the multimodal model has
        # processed it).
        fname = (images[0] or {}).get("file_name", "")
        answer = _("Image: {name}").format(name=fname) if fname else _("[Image attached]")

    # De-duplicate: if a user_input for this question already exists, the
    # browser resent the form (the previous fix needed 2-30 s of GPU image
    # description inside this handler, which read as "nothing happened") —
    # return success without a duplicate row.
    existing_inputs = [
        s
        for s in get_task_steps(task_id)
        if s.get("kind") == "user_input" and s.get("parent_step_id") == question.get("id")
    ]
    if existing_inputs:
        publish_task_update(current_app._get_current_object(), get_task(task_id))
        return jsonify({"ok": True, "dedup": True}), 200

    # Paperclip documents become full user documents (Documents parity) and
    # scope the remaining rag/rlm steps together with the fresh panel
    # selection. Past the de-duplication guard on purpose: a resent form must
    # not create the same documents twice.
    if doc_uploads:
        saved_ids, doc_error, doc_status = _save_task_documents(doc_uploads, task_id)
        if doc_error:
            return jsonify({"error": "⚠️ " + doc_error}), doc_status
        new_doc_ids = saved_ids or []
        selected = _clean_doc_ids(data.get("doc_ids")) if "doc_ids" in data else []
        update_task(task_id, doc_ids=new_doc_ids + [d for d in selected if d not in new_doc_ids])

    ui_step_id = add_user_input_step(task_id, int(question["seq"]), answer)
    # Chat-style header data: the multimodal model that will describe the
    # images (added by the background thread) or nothing without images.
    ui_extra: dict[str, Any] = {}
    if ui_media:
        ui_extra["media"] = ui_media
    if images:
        from app.model_config import get_model_config as _gmc

        mm_config = _gmc("multimodal") or {}
        mm_model_name = str(mm_config.get("model_name") or "")
        if mm_model_name:
            ui_extra["model_name"] = mm_model_name
            ui_extra["model_type"] = "multimodal"
    if ui_extra:
        update_step(ui_step_id, extra=ui_extra)
    if images:
        # Describe the images AFTER answering (app/tasks/answer_images.py):
        # the multimodal call is GPU work (2-30 s) — holding the HTTP response
        # for it made users re-press send and produced duplicate answers.
        from app.tasks.answer_images import describe_answer_images

        describe_answer_images(self_app=current_app._get_current_object(), task=task, step_id=ui_step_id, images=images)
    if "deep_analysis" in data and _truthy_flag(data.get("deep_analysis")):
        # The toggle switched ON while answering: the plan already exists and
        # the planner cannot amend it, so a deep-analysis (rlm) step over the
        # attached documents is appended to the plan for the remaining steps.
        update_task(task_id, deep_analysis=True)
        append_deep_analysis_step(task_id, lang=task.get("lang") or "ru")
    publish_task_update(current_app._get_current_object(), get_task(task_id))
    return jsonify({"ok": True}), 202


def _cancel_inflight_subtasks(task_id: str) -> None:
    """Cancel the media sub-task a running step already put on the queue (v12.6).

    A tagged request whose step is gone is dropped by the worker, but one the
    worker already holds keeps the slow worker and the GPU lock — the orphan
    that left the next task looking hung after this task was stopped or deleted.
    Best effort: a failure here never blocks the stop/delete itself.
    """
    try:
        queue = current_app.request_queue
        for row in get_task_steps(task_id):
            if str(row.get("status") or "") not in ("subtask_queued", "subtask_running"):
                continue
            queue_id = (row.get("extra") or {}).get("subtask_queue_id")
            if queue_id:
                queue.cancel_task(str(queue_id))
    except Exception as e:  # noqa: BLE001 — the cancel is best effort
        current_app.logger.warning(f"in-flight subtask cancel failed ({task_id}): {e}")


@bp.route("/<task_id>/retry", methods=["POST"])
def task_retry(task_id):
    """Re-queue a failed/expired/cancelled task so the planner picks it up fresh."""
    if "login" not in session:
        return jsonify({"error": "⚠️ " + _("Not authorized")}), 401
    task = _owned_task(task_id)
    if task is None:
        return jsonify({"error": "⚠️ " + _("Task not found")}), 404
    if task.get("status") not in ("failed", "expired", "cancelled"):
        return jsonify({"error": "⚠️ " + _("Only failed, expired or cancelled tasks can be restarted")}), 409
    update_task(task_id, status="queued", next_step_index=0, abort_reason=None, completed_at=None)
    t = get_task(task_id)
    publish_task_update(current_app._get_current_object(), t)
    return jsonify({"task": _task_summary(t)}), 202


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
    # A running step's own sub-task must die too, or it holds the GPU queue.
    _cancel_inflight_subtasks(task_id)
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
    # The orphan guard can only stop a sub-task that has not been dequeued yet;
    # an in-flight one is cancelled explicitly (it holds the GPU queue).
    _cancel_inflight_subtasks(task_id)
    delete_task(task_id)
    publish_task_update(
        current_app._get_current_object(),
        {"id": task["id"], "user_id": task["user_id"], "status": "deleted"},
    )
    return jsonify({"status": "ok"})
