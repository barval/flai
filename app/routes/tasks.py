# app/routes/tasks.py
"""Internal API for the Tasks subsystem (v12.6, spec §5).

Owner-scoped like sessions: a task belongs to the login stored in its
``user_id``; the admin flag widens access. The conversation is rendered from
``task_steps`` (the user request, the plan, each step with its "Step N of M:"
header, clarifying questions/answers and the final answer) — there is no
parallel messages table.
"""

from __future__ import annotations

import base64 as _b64
import contextlib
import json
import threading
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

# Visual continuity rebuild (spec §7): one background extraction per task —
# a second concurrent request is rejected with 409; the flag has a safety TTL.
_CONTINUITY_JOB_KEY = "task:continuity:%s"
_CONTINUITY_JOB_TTL_S = 3600

bp = Blueprint("tasks", __name__, url_prefix="/api/tasks")

# User-facing service_class (0/1/2) → low-priority queue task_class (3/4/5).
_SERVICE_TO_TASK_CLASS = {0: 3, 1: 4, 2: 5}
_TERMINAL_STATUSES = {"completed", "failed", "expired", "cancelled"}
# Cancel flags survive until the executor consumes them; a TTL keeps a flag set
# on an already-terminal task from lingering forever.
_CANCEL_FLAG_TTL_S = 3600


def _queued_task_count(user_id: str) -> int:
    """How many non-terminal tasks the user has in the queue."""
    from app.database import get_db

    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT count(*) FROM tasks WHERE user_id = %s AND status IN ('queued','active','frozen')",
            (user_id,),
        )
        return c.fetchone()["count"] or 0


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
                return None, _("max_images_reached").format(max=max_images), 400
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


def _save_task_audio(
    uploads: list[Any], task_id: str, phase: str, step_id: str | None = None
) -> tuple[list[dict[str, Any]] | None, str | None, int | None]:
    """Save audio/voice attachments of a task request/answer; return (entries, error, status).

    Chat parity (v12.6 voice): a recorded voice message or an audio file (a
    video container counts too — chat transcribes its audio track) is stored
    under the user's upload folder and transcribed into text by the
    background thread (``app/tasks/audio_transcribe.py``). Only
    ``{file_path, file_name, file_type, phase, step_id, transcript}`` dicts
    persist — bytes never enter the DB, the transcript never becomes a
    Documents-panel document. The files land in the folder named after the
    TASK id, like images and documents attached to the same task.
    """
    import mimetypes

    from app.utils import check_upload_quota, save_uploaded_file

    user_id = session["login"]
    lang = session.get("language", "ru")
    audio_module = current_app.modules.get("audio")
    # One voice message plus audio files, this many audio items per submit
    # (chat parity): the excess is refused instead of silently dropped.
    max_audio = current_app.config.get("MAX_CHAT_AUDIO", 4)
    if len(uploads) > max_audio:
        with force_locale(lang):
            return None, _("max_audio_reached").format(max=max_audio), 400
    entries: list[dict[str, Any]] = []
    for upload in uploads:
        file_bytes = upload.read()
        part_size = len(file_bytes)
        part_type = upload.content_type or mimetypes.guess_type(upload.filename)[0] or "application/octet-stream"
        part_name = upload.filename
        if not (audio_module and audio_module.is_audio_file(part_type, part_name)):
            with force_locale(lang):
                return None, _("Only audio files can be attached to a task"), 400
        quota_error = check_upload_quota(user_id, part_size)
        if quota_error:
            return None, quota_error, 413
        part_data = _b64.b64encode(file_bytes).decode("utf-8")
        rel_path = save_uploaded_file(
            file_data=part_data,
            filename=part_name,
            session_id=task_id,
            upload_folder=current_app.config.get("UPLOAD_FOLDER", "data/uploads"),
            user_id=user_id,
        )
        if not rel_path:
            with force_locale(lang):
                return None, _("Failed to save file"), 500
        entry: dict[str, Any] = {"file_path": rel_path, "file_name": part_name, "file_type": part_type, "phase": phase}
        if step_id:
            entry["step_id"] = step_id
        entries.append(entry)
    return entries, None, None


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


def _visible_plan_steps(task: dict) -> list[dict]:
    """Plan steps shown to the user: the internal continuity extraction is hidden."""
    return [s for s in _plan_steps(task) if str(s.get("tool") or "") != "continuity"]


def _plan_revisions(task: dict) -> list[dict]:
    """Recorded plan rewrites (see TaskExecutor._apply_replan), oldest first."""
    plan = task.get("step_plan")
    if not isinstance(plan, dict):
        return []
    return [r for r in (plan.get("revisions") or []) if isinstance(r, dict)]


def _plan_update_message(task: dict, rev: dict) -> dict:
    """One «Plan updated (N steps).» service notice.

    Localized at render time (the plan message is too), so a language switch
    re-renders the notice in the new language. The notice carries the review
    call's chat-style header (time, model, real tokens, duration) exactly like
    a step message — the metadata is stored by ``_apply_replan`` under
    ``step_plan.revision_meta`` keyed by the rewrite position.
    """
    total = int(rev.get("total") or 0)
    upto = int(rev.get("upto") or 0)
    plan = task.get("step_plan") if isinstance(task.get("step_plan"), dict) else {}
    meta = ((plan.get("revision_meta") or {}).get(str(upto)) or {})
    if not isinstance(meta, dict):
        meta = {}
    return {
        "id": f"{task['id']}:plan_update:{upto}",
        "role": "assistant",
        "kind": "plan_update",
        "content": _("Plan updated ({n} steps).").format(n=total),
        "seq": upto,
        "total_steps": total,
        "status": "passed",
        "tool": "",
        "exec_seconds": float(meta.get("exec_seconds") or 0.0),
        "model_name": str(meta.get("model_name") or ""),
        "model_type": str(meta.get("model_type") or ""),
        "usage": dict(meta.get("usage") or {}),
        "extra": {},
        "created_at": meta.get("at") or _iso(task.get("updated_at")),
    }


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
        "total_steps": len(_visible_plan_steps(task)),
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


def _transcript_block(entry: dict) -> dict:
    """One audio attachment's transcript as a conversation block.

    The label reuses the chat's "🎤 Transcribed:" wording plus the file name (a
    task may carry several recordings); the client renders it in the regular
    reading font — a long transcript in the italic service line would be
    unreadable.
    """
    name = str(entry.get("file_name") or "")
    label = "🎤 " + _("Transcribed") + (f" ({name}):" if name else ":")
    return {"label": label, "text": str(entry.get("transcript") or "").strip()}


def _step_message(row: dict, total: int, audio_entries: list[dict] | None = None) -> dict:
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
        # A failed OR skipped step with no result body must still show WHY it
        # ended that way (chat parity: errors are visible, never silently
        # blank; a skipped step names the producer step that yielded nothing).
        if not body and row.get("status") in ("failed", "skipped") and row.get("last_error"):
            lines.append(str(row["last_error"]))
        content = "\n".join(lines)
    elif kind == "question":
        # The goal IS the verbatim question (prompt rule) — show it as a plain
        # assistant message; the waiting state lives in the banner/live status.
        content = body
    else:  # user_input / final / plan — no step header
        content = body
    extra = row.get("extra") or {}
    msg = {
        "id": row.get("id"),
        "role": "user" if kind == "user_input" else "assistant",
        "kind": kind,
        "content": content,
        "badge": badge,
        "seq": seq,
        "total_steps": total,
        "status": row.get("status"),
        # Validation state for the step line: how many attempts the step took,
        # why the last one was rejected and what happened per attempt. The task
        # view renders a chip + the reason, so a redrawn picture is explained
        # instead of silently replacing the previous one.
        "attempts": int(row.get("attempts") or 0),
        "attempts_max": int(current_app.config.get("TASK_STEP_MAX_ATTEMPTS") or 3),
        "attempts_log": [e for e in (extra.get("attempts_log") or []) if isinstance(e, dict)],
        "failure_reason": str(row.get("last_error") or row.get("checkpoint_result") or ""),
        "checkpoint_verdict": row.get("checkpoint_verdict"),
        "artifact_note": str(extra.get("artifact_note") or ""),
        "tool": row.get("tool") or "",
        "exec_seconds": float(row.get("exec_seconds") or 0.0),
        "model_name": extra.get("model_name", ""),
        "model_type": extra.get("model_type", ""),
        "usage": extra.get("usage") or {},
        "extra": extra,
        # task_steps has no created_at: a step's timestamp is when it started
        # running (a queued step with no timestamp renders without a date).
        "created_at": _iso(row.get("started_at") or row.get("finished_at")),
    }
    if audio_entries:
        # An answer's audio attachments: the 📎 service line + the transcript
        # blocks the transcription thread appended to this step's result.
        names = [str(e.get("file_name") or "") for e in audio_entries if e.get("file_name")]
        msg["attached_audio"] = (
            _("Attached audio recordings: {names}").format(names="\n" + "\n".join(names)) if names else ""
        )
        msg["audio_transcripts"] = [_transcript_block(e) for e in audio_entries if _transcript_block(e)["text"]]
    return msg


def _render_messages(task: dict) -> list[dict]:
    """The full task conversation from ``task_steps`` + the plan payload."""
    raw_plan_steps = _plan_steps(task)
    plan_steps = _visible_plan_steps(task)
    total = len(plan_steps)
    # Plan positions are 1-based and include the hidden continuity extraction:
    # map them onto the visible numbering so «Step N of M» stays gapless.
    seq_map: dict[int, int] = {}
    visible_pos = 0
    for raw_pos, plan_step in enumerate(raw_plan_steps, start=1):
        if str(plan_step.get("tool") or "") == "continuity":
            continue
        visible_pos += 1
        seq_map[raw_pos] = visible_pos
    messages: list[dict] = []
    request_text = _task_request(task)
    if request_text:
        # A draft task has no request yet — the conversation starts empty.
        image_names = [str(i.get("file_name") or "") for i in (task.get("images") or []) if isinstance(i, dict)]
        image_names = [n for n in image_names if n]
        # The attachment names never ride inside the request text: they travel
        # as their own field so the task view renders them as a service line
        # (the plan line's font) — the label owns its line and the names start
        # on the next one, one file name per line.
        attached_images = (
            _("Attached images: {names}").format(names="\n" + "\n".join(image_names)) if image_names else ""
        )
        doc_names = _document_names(task)
        attached_documents = (
            _("Attached documents: {names}").format(names="\n" + "\n".join(doc_names)) if doc_names else ""
        )
        # Audio attachments (v12.6 voice parity): request-phase entries ride
        # the request message; answer-phase ones ride their user_input step.
        audio_entries = [e for e in (task.get("audio") or []) if isinstance(e, dict)]
        req_audio = [e for e in audio_entries if str(e.get("phase") or "request") != "answer"]
        req_audio_names = [str(e.get("file_name") or "") for e in req_audio if e.get("file_name")]
        attached_audio = (
            _("Attached audio recordings: {names}").format(names="\n" + "\n".join(req_audio_names))
            if req_audio_names
            else ""
        )
        req_transcripts = [_transcript_block(e) for e in req_audio if _transcript_block(e)["text"]]
        answer_audio: dict[str, list[dict]] = {}
        for e in audio_entries:
            sid = str(e.get("step_id") or "")
            if sid and str(e.get("phase") or "") == "answer":
                answer_audio.setdefault(sid, []).append(e)
        # Every task image rides extra.media (JS renders the list in a loop);
        # the FIRST one also stays in extra.media for backward compatibility.
        req_extra: dict[str, Any] = {}
        task_images = task.get("images") or []
        if task_images and isinstance(task_images, list):
            img_media: list[dict[str, Any]] = []
            for img in task_images:
                if not isinstance(img, dict) or not img.get("file_path"):
                    continue
                entry = {k: img[k] for k in ("file_path", "file_name", "file_type", "file_size") if k in img}
                if "file_type" not in entry:
                    entry["file_type"] = "image/jpeg"
                img_media.append(entry)
            if img_media:
                req_extra["media_all"] = img_media
                req_extra["media"] = img_media[0]
        messages.append(
            {
                "id": f"{task['id']}:request",
                "role": "user",
                "kind": "request",
                "content": request_text,
                "attached_images": attached_images,
                "attached_documents": attached_documents,
                "attached_audio": attached_audio,
                "audio_transcripts": req_transcripts,
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

    # A plan rewrite is anchored to the step that triggered it (rank 4 = after
    # that step's own render ranks 0..3), so the notice lands between the
    # executed prefix and the new tail.
    entries: list[tuple[tuple[int, int], dict]] = []
    for row in steps:
        if str(row.get("tool") or "") == "continuity":
            continue  # internal extraction — never a user-visible step
        if row.get("status") == "pending" and not str(row.get("result") or "").strip():
            continue
        raw_seq = int(row.get("seq") or 0)
        display_seq = seq_map.get(raw_seq, raw_seq)
        entries.append(
            (
                _chrono_key(row),
                _step_message(
                    {**row, "seq": display_seq},
                    total,
                    answer_audio.get(str(row["id"])),
                ),
            )
        )
    for rev in _plan_revisions(task):
        entries.append(((int(rev.get("upto") or 0), 4), _plan_update_message(task, rev)))
    for _key, msg in sorted(entries, key=lambda e: e[0]):
        messages.append(msg)
    return messages


@bp.route("", methods=["GET"])
def list_tasks():
    if "login" not in session:
        return jsonify({"error": "⚠️ " + _("Not authorized")}), 401
    tasks = get_tasks_by_user(session["login"])
    totals = _token_totals_for([t["id"] for t in tasks])
    positions = get_queued_task_positions()
    return jsonify({"tasks": [_task_summary(t, totals, positions) for t in tasks]})


@bp.route("/queue-count", methods=["GET"])
def api_task_queue_count():
    if "login" not in session:
        return jsonify({"error": "⚠️ " + _("Not authorized")}), 401
    from app.database import get_db

    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT count(*) FROM tasks WHERE user_id = %s AND status IN ('queued','active','frozen')",
            (session["login"],),
        )
        user_count = c.fetchone()["count"] or 0
        c.execute("SELECT count(*) FROM tasks WHERE status IN ('queued','active','frozen')")
        total = c.fetchone()["count"] or 0
    return jsonify({"queued_tasks": user_count, "total_queued_tasks": total})


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
    # Sync the session language with the DB: the user may have changed their
    # preference in the admin panel (or via a direct DB update) without a page
    # reload that calls /set-language — an old session value would create a
    # task in the wrong language and the planner would then produce an
    # English/Russian plan regardless of the user's actual preference.
    from app.database import get_db

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT language FROM users WHERE login = %s", (session["login"],))
        _row = c.fetchone()
        if _row and _row["language"] != session.get("language"):
            session["language"] = _row["language"]
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
    audio_uploads: list[Any] = []
    if request.content_type and "multipart/form-data" in request.content_type:
        # Vision flow (v12.6): the request may carry attached images (chips in
        # the task view). JSON fields ride the form alongside the files.
        data = {key: val for key, val in request.form.items()}
        uploads = [f for f in request.files.getlist("images") if f and f.filename]
        doc_uploads = [f for f in request.files.getlist("files") if f and f.filename]
        audio_uploads = [f for f in request.files.getlist("audio") if f and f.filename]
    else:
        data = request.get_json(silent=True) or {}

    user_request = str(data.get("request") or "").strip()
    if not user_request and not audio_uploads:
        # A dictated task (audio, no text) is a valid request: the transcript
        # arrives before the planner runs (the task freezes meanwhile).
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

    audio_entries: list[dict[str, Any]] = []
    if audio_uploads:
        saved_audio, audio_error, audio_status = _save_task_audio(audio_uploads, task_id, phase="request")
        if audio_error:
            return jsonify({"error": "⚠️ " + audio_error}), audio_status
        audio_entries = saved_audio or []

    # A task with audio waits for Whisper (the dictated text must exist before
    # the planner runs); everything else goes straight to the queue.
    if audio_entries:
        update_task(task_id, status="frozen", title=user_request[:300], abort_reason="audio_transcript")
    else:
        update_task(task_id, status="queued", title=user_request[:300], abort_reason=None)
    # Sync the task language with the session/DB at submit time: a draft may
    # have been created in another language than the one currently active
    # (the user switched profiles between ➕ and sending). The planner then
    # writes the plan in the wrong language.
    from app.database import get_db as _get_db

    with _get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT language FROM users WHERE login = %s", (session["login"],))
        _row = c.fetchone()
    if _row and _row["language"] != task.get("lang"):
        session["language"] = _row["language"]
        update_task(task_id, lang=_row["language"])
    if "doc_ids" in data or new_doc_ids:
        # Snapshot the Documents-panel selection at submit time (like a chat
        # request): an empty list means "all documents". Documents just saved
        # from paperclip attachments come first and scope the task too — a
        # submit that attached files must never fall back to "all".
        selected = _clean_doc_ids(data.get("doc_ids"))
        update_task(task_id, doc_ids=new_doc_ids + [d for d in selected if d not in new_doc_ids])
    if images:
        update_task(task_id, images=images)
    if audio_entries:
        update_task(task_id, audio=audio_entries)
    if "deep_analysis" in data:
        # The 🔬 toggle state rides the request: the planner is told to include
        # a deep-analysis (rlm) step over the attached documents.
        update_task(task_id, deep_analysis=_truthy_flag(data.get("deep_analysis")))
    if audio_entries:
        # Transcribe AFTER every task field is saved: the thread flips the
        # status back to queued as soon as the transcripts exist, and the
        # planner must then already see the final doc_ids/images/audio.
        from app.tasks.audio_transcribe import start_task_audio_transcription

        start_task_audio_transcription(current_app._get_current_object(), task_id)
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
    audio_uploads: list[Any] = []
    if request.content_type and "multipart/form-data" in request.content_type:
        data = {key: val for key, val in request.form.items()}
        uploads = [f for f in request.files.getlist("images") if f and f.filename]
        doc_uploads = [f for f in request.files.getlist("files") if f and f.filename]
        audio_uploads = [f for f in request.files.getlist("audio") if f and f.filename]
    else:
        data = request.get_json(silent=True) or {}
    answer = str(data.get("answer") or data.get("request") or "").strip()
    if not answer and not uploads and not audio_uploads:
        # A voice-only answer is valid: the transcript is appended to the
        # user_input step by the background thread (the task freezes meanwhile).
        # Log what the client actually sent — a cached pre-fix page posts the
        # draft field "request" to /answer, which used to answer "The answer is empty".
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

    audio_entries: list[dict[str, Any]] = []
    if audio_uploads:
        saved_audio, audio_error, audio_status = _save_task_audio(audio_uploads, task_id, phase="answer")
        if audio_error:
            return jsonify({"error": "⚠️ " + audio_error}), audio_status
        audio_entries = saved_audio or []

    ui_step_id = add_user_input_step(task_id, int(question["seq"]), answer)
    if audio_entries:
        # The step ids are only known after the row exists: link each answer
        # entry to it (the transcript is appended to THIS step by the thread).
        for entry in audio_entries:
            entry["step_id"] = ui_step_id
        merged_audio = list(task.get("audio") or []) + audio_entries
        update_task(task_id, audio=merged_audio)
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

        describe_answer_images(app=current_app._get_current_object(), task=task, step_id=ui_step_id, images=images)
    if audio_entries:
        # Transcribe the answer's audio AFTER the user_input row exists
        # (app/tasks/audio_transcribe.py): Whisper is an HTTP call to the
        # sidecar (no GPU), but the transcripts must be appended to the step
        # before the executor consumes it — the task freezes meanwhile.
        from app.tasks.audio_transcribe import start_task_audio_transcription

        start_task_audio_transcription(current_app._get_current_object(), task_id)
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


@bp.route("/<task_id>/continuity", methods=["POST"])
def task_continuity_rebuild(task_id):
    """Rebuild the task's visual continuity sheet (spec §7).

    Served DIRECTLY: one LLM call in a background thread — the executor loop
    is not involved, so the button works for ACTIVE and COMPLETED tasks with
    no status transition and no step rows. A second concurrent rebuild for
    the same task answers 409.
    """
    if "login" not in session:
        return jsonify({"error": "⚠️ " + _("Not authorized")}), 401
    task = _owned_task(task_id)
    if task is None:
        return jsonify({"error": "⚠️ " + _("Task not found")}), 404
    if task.get("status") not in ("active", "completed", "frozen", "awaiting_user"):
        return jsonify({"error": "⚠️ " + _("The visual continuity sheet can be rebuilt while the task runs or after completion")}), 409
    try:
        redis_client = current_app.request_queue.redis
        if not redis_client.set(_CONTINUITY_JOB_KEY % task_id, "1", ex=_CONTINUITY_JOB_TTL_S, nx=True):
            return jsonify({"error": "⚠️ " + _("The visual continuity sheet is already being rebuilt")}), 409
    except Exception as e:  # noqa: BLE001 — the flag is best effort
        current_app.logger.warning(f"continuity rebuild flag write failed ({task_id}): {e}")

    update_task(task_id, visual_bible={}, visual_bible_attempted=False, visual_bible_stale=False)
    from app.tasks.store import get_task as _get_task

    app = current_app._get_current_object()

    def _rebuild():
        with app.app_context():
            # Imported at call time so tests (and the ruff gate) see no eager
            # executor module dependency in the routes file.
            from app.tasks.executor import TaskExecutor

            task_now = _get_task(task_id) or task
            TaskExecutor(app=app)._extract_visual_bible(task_now, merge=False)
        with contextlib.suppress(Exception):
            app.request_queue.redis.delete(_CONTINUITY_JOB_KEY % task_id)
        publish_task_update(app, _get_task(task_id))

    threading.Thread(target=_rebuild, daemon=True, name=f"task-continuity:{task_id}").start()
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
