# app/routes/rlm.py
"""RLM deep-analysis routes."""

import base64
import json
import mimetypes

from flask import Blueprint, current_app, jsonify, request, session
from flask_babel import gettext as _

from app.db import get_db, get_user_documents, save_message, update_session_title, update_session_visit
from app.utils import check_upload_quota, resize_image_if_needed, save_uploaded_file, validate_session_ownership

bp = Blueprint("rlm", __name__, url_prefix="/api/rlm")


@bp.route("/analyze", methods=["POST"])
def analyze():
    if "login" not in session:
        return jsonify({"error": "⚠️ " + _("Not authorized")}), 401
    user_id = session["login"]

    if not current_app.config.get("RLM_ENABLED", True):
        return jsonify({"error": "⚠️ " + _("Deep analysis is disabled")}), 403

    is_json = request.content_type and "application/json" in request.content_type
    if is_json:
        data = request.get_json(silent=True) or {}
        session_id = data.get("session_id")
        doc_ids = data.get("doc_ids") or []
        question = (data.get("question") or "").strip()
        file_data = file_type = file_name = None
    else:
        session_id = request.form.get("session_id")
        try:
            doc_ids = json.loads(request.form.get("doc_ids") or "[]")
            if not isinstance(doc_ids, list):
                doc_ids = []
        except (json.JSONDecodeError, TypeError):
            doc_ids = []
        question = (request.form.get("text") or request.form.get("question") or "").strip()
        file_data = file_type = file_name = None
        if "file" in request.files:
            file = request.files["file"]
            if file and file.filename:
                file_bytes = file.read()
                file_data = base64.b64encode(file_bytes).decode("utf-8")
                file_type = file.content_type or mimetypes.guess_type(file.filename)[0] or "application/octet-stream"
                file_name = file.filename

    if not session_id or not question:
        return jsonify({"error": "⚠️ " + _("Select at least one document and enter a question")}), 400

    if not doc_ids and not file_data:
        return jsonify({"error": "⚠️ " + _("Select at least one document and enter a question")}), 400

    if not validate_session_ownership(session_id, user_id):
        current_app.logger.warning(f"User {user_id} attempted to use session {session_id} for RLM analysis")
        return jsonify({"error": "⚠️ " + _("Session not found")}), 403

    owned = {d["id"] for d in (get_user_documents(user_id) or [])}
    if not set(doc_ids).issubset(owned):
        current_app.logger.warning(f"User {user_id} attempted to analyze foreign documents in RLM: {doc_ids}")
        return jsonify({"error": "⚠️ " + _("Document not found")}), 403

    # Attached image: enforce quota, downscale for the multimodal model, and
    # persist it alongside the question so it survives page reload.
    file_path = None
    resize_notice = None
    resize_notice_id = None
    if file_data:
        if file_size := len(base64.b64decode(file_data)):
            quota_error = check_upload_quota(user_id, file_size)
            if quota_error:
                return jsonify({"error": quota_error}), 413
        lang_for_notice = session.get("language", "ru")
        new_file_data, new_file_type, new_file_name, resized, _orig_dims, _new_dims = resize_image_if_needed(
            file_data, file_type, file_name, current_app.config.get("MAX_IMAGE_SIZE", 1536)
        )
        if resized:
            from flask_babel import force_locale

            with force_locale(lang_for_notice):
                resolution_msg = _("Maximum resolution {max_size}px on the longest side").format(
                    max_size=current_app.config.get("MAX_IMAGE_SIZE", 1536)
                )
                reduced_msg = _("The image has been reduced.")
                notice_text = f"{resolution_msg}. {reduced_msg}"
                notice_id = save_message(session_id, "assistant", notice_text, model_name="system", response_time="0")
            resize_notice = notice_text
            resize_notice_id = notice_id
        file_data = new_file_data
        file_type = new_file_type
        file_name = new_file_name
        file_path = save_uploaded_file(
            file_data=file_data,
            filename=file_name,
            session_id=session_id,
            upload_folder=current_app.config["UPLOAD_FOLDER"],
            user_id=user_id,
        )

    user_content = []
    if question:
        user_content.append({"type": "text", "text": question})
    if file_data:
        user_content.append({"type": "image", "file_data": file_data, "file_type": file_type, "file_name": file_name})
    user_content_json = json.dumps(user_content, ensure_ascii=False)
    user_message_id = save_message(session_id, "user", user_content_json, file_data, file_type, file_name, file_path)

    update_session_visit(user_id, session_id)

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as cnt FROM messages WHERE session_id = %s AND role = 'user'", (session_id,))
        user_message_count = c.fetchone()["cnt"]
        if user_message_count == 1:
            update_session_title(session_id, question, file_name)

    task_id, info = current_app.request_queue.add_rlm_task(
        user_id,
        session_id,
        doc_ids,
        question,
        lang=session.get("language", "ru"),
        image_data=file_data,
        image_type=file_type,
        image_name=file_name,
    )

    return jsonify(
        {
            "task_id": task_id,
            "position": info["position"],
            "user_message_id": user_message_id,
            "resize_notice": resize_notice,
            "resize_notice_id": resize_notice_id,
        }
    ), 202
