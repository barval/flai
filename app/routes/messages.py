# app/routes/messages.py
import base64
import json
import mimetypes

from flask import Blueprint, current_app, jsonify, request, session
from flask_babel import force_locale
from flask_babel import gettext as _

from app import db, limiter
from app.database import get_db
from app.utils import (
    resize_image_if_needed,
    save_uploaded_file,
    validate_session_ownership,
)

bp = Blueprint("messages", __name__, url_prefix="/api")


@bp.route("/sessions/<session_id>/messages", methods=["GET"])
def api_get_messages(session_id):
    if "login" not in session:
        return jsonify({"error": _("Not authorized")}), 401

    # Security: Verify session belongs to user
    if not validate_session_ownership(session_id, session["login"]):
        current_app.logger.warning(f"User {session['login']} attempted to access messages in session {session_id}")
        return jsonify({"error": _("Session not found")}), 404

    # Get pagination parameters
    since = request.args.get("since")
    try:
        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
    except (ValueError, TypeError):
        limit = 100
        offset = 0

    # Enforce reasonable limits
    limit = min(limit, 200)  # Max 200 messages at once
    offset = max(offset, 0)  # No negative offset

    messages = db.get_session_messages(session_id, since=since, limit=limit, offset=offset)
    return jsonify({"messages": messages, "limit": limit, "offset": offset, "has_more": len(messages) >= limit})


@bp.route("/send_message", methods=["POST"])
@limiter.limit("15 per minute;60 per hour", key_func=lambda: session.get("login") or request.remote_addr)  # type: ignore[arg-type, return-value]
def send_message():
    current_app.logger.info("=" * 50)
    current_app.logger.info("send_message: START PROCESSING")

    if "login" not in session:
        return jsonify({"error": _("Not authorized")}), 401

    user_id = session["login"]
    user_class = session.get("service_class", 2)
    session_id = session.get("current_session")

    # Read session_id from request body (sent by client for multi-tab safety)
    body_session_id = None
    if request.is_json:
        body_session_id = request.json.get("session_id")
    elif request.content_type and "multipart/form-data" in request.content_type:
        body_session_id = request.form.get("session_id")

    # Validate body_session_id: must be a valid UUID owned by this user
    if body_session_id:
        try:
            import uuid
            uuid.UUID(body_session_id, version=4)
            with db.get_db() as conn:
                c = conn.cursor()
                c.execute(
                    "SELECT id FROM chat_sessions WHERE id = %s AND user_id = %s",
                    (body_session_id, user_id),
                )
                if c.fetchone():
                    session_id = body_session_id
                    session["current_session"] = session_id
        except (ValueError, Exception):
            pass  # Invalid UUID or DB error — fall back to Flask session

    # Verify session exists — fall back to latest if current was deleted (multi-tab race)
    if session_id:
        with db.get_db() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT id FROM chat_sessions WHERE id = %s AND user_id = %s",
                (session_id, user_id),
            )
            if not c.fetchone():
                c.execute(
                    "SELECT id FROM chat_sessions WHERE user_id = %s ORDER BY updated_at DESC LIMIT 1",
                    (user_id,),
                )
                latest = c.fetchone()
                if latest:
                    session_id = latest["id"]
                    session["current_session"] = session_id

    if not session_id:
        session_id = db.create_session(user_id, lang=session.get("language", "ru"))
        session["current_session"] = session_id

    message_text = ""
    file_data = None
    file_type = None
    file_name = None
    voice_record = False
    file_size_bytes = 0
    voice_file_data = None
    voice_file_type = None
    voice_file_name = None

    if request.content_type and "multipart/form-data" in request.content_type:
        message_text = request.form.get("message", "")
        if "file" in request.files:
            file = request.files["file"]
            if file and file.filename:
                file_bytes = file.read()
                file_size_bytes = len(file_bytes)
                file_data = base64.b64encode(file_bytes).decode("utf-8")
                file_type = file.content_type or mimetypes.guess_type(file.filename)[0] or "application/octet-stream"
                file_name = file.filename

                # Check upload quota
                from app.utils import check_upload_quota

                quota_error = check_upload_quota(user_id, file_size_bytes)
                if quota_error:
                    return jsonify({"error": quota_error}), 413
        voice_record = request.form.get("voice_record") == "true"

        # Read voice file when both image + voice are sent together
        voice_file_data = None
        voice_file_type = None
        voice_file_name = None
        if "voice" in request.files:
            vfile = request.files["voice"]
            if vfile and vfile.filename:
                vbytes = vfile.read()
                voice_file_data = base64.b64encode(vbytes).decode("utf-8")
                voice_file_type = vfile.content_type or mimetypes.guess_type(vfile.filename)[0] or "audio/webm"
                voice_file_name = vfile.filename
    else:
        try:
            data = request.get_json()
            if data:
                message_text = data.get("message", "")
        except (json.JSONDecodeError, TypeError):
            # Fallback to form data if JSON parsing fails
            message_text = request.form.get("message", "")

    if not message_text and not file_data:
        return jsonify({"error": _("Empty message")}), 400

    response_style = session.get("response_style", "neutral")

    request_type = "text"
    if file_data and file_type:
        if file_type.startswith("image/"):
            request_type = "audio" if voice_file_data else "image"
        elif current_app.modules["audio"].is_audio_file(file_type, file_name):
            request_type = "audio"
    elif voice_file_data:
        # Voice file is in "voice" field only (no "file" field or "file" is not image)
        request_type = "audio"

    resize_notice = None
    resize_notice_id = None
    file_path = None
    if request_type == "image":
        max_size = current_app.config.get("MAX_IMAGE_SIZE", 1536)
        new_file_data, new_file_type, new_file_name, resized, orig_dims, new_dims = resize_image_if_needed(
            file_data, file_type, file_name, max_size
        )
        if resized:
            lang = session.get("language", "ru")
            with force_locale(lang):
                resolution_msg = _("Maximum resolution {max_size}px on the longest side").format(max_size=max_size)
                reduced_msg = _("The image has been reduced.")
                notice_text = f"{resolution_msg}. {reduced_msg}"
                notice_id = db.save_message(
                    session_id, "assistant", notice_text, model_name="system", response_time="0"
                )
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

    if request_type == "audio":
        limit_mb = current_app.config["MAX_VOICE_SIZE_MB"] if voice_record else current_app.config["MAX_AUDIO_SIZE_MB"]
        # When image + voice, check voice file size (not image)
        audio_size = len(base64.b64decode(voice_file_data)) if voice_file_data else file_size_bytes
        if audio_size > limit_mb * 1024 * 1024:
            return jsonify({"error": _("Maximum file size {max_size} MB").format(max_size=limit_mb)}), 400

    user_content = []
    if message_text:
        user_content.append({"type": "text", "text": message_text})
    if file_data:
        if file_type and file_type.startswith("image/"):
            content_type = "image"
        elif file_type and current_app.modules["audio"].is_audio_file(file_type, file_name):
            content_type = "audio"
        else:
            content_type = "file"
        user_content.append(
            {"type": content_type, "file_data": file_data, "file_type": file_type, "file_name": file_name}
        )

    user_content_json = json.dumps(user_content, ensure_ascii=False)
    user_message_id = db.save_message(session_id, "user", user_content_json, file_data, file_type, file_name, file_path)

    # Mark session as visited when user sends a message (prevents "unread" bug)
    db.update_session_visit(user_id, session_id)

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as cnt FROM messages WHERE session_id = %s", (session_id,))
        message_count = c.fetchone()["cnt"]
        is_first_message = message_count == 1
        if is_first_message:
            db.update_session_title(session_id, message_text, file_name)

    # Audio files are processed asynchronously: queue transcription task
    if request_type == "audio":
        current_app.logger.info("send_message: audio detected, queueing transcription task")

        # Determine which file is audio and which is image
        audio_file_data = voice_file_data or file_data
        audio_file_type = voice_file_type or file_type
        audio_file_name = voice_file_name or file_name
        img_data = file_data if voice_file_data else None
        img_type = file_type if voice_file_data else None
        img_name = file_name if voice_file_data else None

        request_data = {
            "type": "transcribe_audio",
            "file_data": audio_file_data,
            "file_type": audio_file_type,
            "file_name": audio_file_name,
            "voice_record": voice_record,
            "preview": (message_text[:50] + "...") if message_text else (audio_file_name or _("Voice request")),
            "response_style": response_style,
        }
        # Pass image data for combined processing (voice → text + image → multimodal)
        if img_data:
            request_data["image_data"] = img_data
            request_data["image_type"] = img_type
            request_data["image_name"] = img_name
        request_id, position_info = current_app.request_queue.add_request(
            user_id, session_id, request_data, user_class, lang=session.get("language", "ru")
        )
        response_data = {
            "status": "queued",
            "request_id": request_id,
            "position": position_info["position"],
            "estimated_wait": position_info["estimated_seconds"],
            "message": _("Request queued (position {pos})").format(pos=position_info["position"]),
            "user_message_id": user_message_id,
        }
        if resize_notice:
            response_data["resize_notice"] = resize_notice
            response_data["resize_notice_id"] = resize_notice_id
        return jsonify(response_data)

    # For text and image requests, queue the main processing task
    if request_type == "image" and file_data:
        request_data = {
            "type": "image",
            "text": message_text,
            "file_data": file_data,
            "file_type": file_type,
            "file_name": file_name,
            "preview": (message_text[:50] + "...") if message_text else (file_name or _("Image")),
            "response_style": response_style,
            "stream": True,
        }
    else:
        request_data = {
            "type": "text",
            "text": message_text,
            "preview": (message_text[:50] + "...") if message_text else _("Text request"),
            "response_style": response_style,
            "stream": True,
        }

    request_id, position_info = current_app.request_queue.add_request(
        user_id, session_id, request_data, user_class, lang=session.get("language", "ru")
    )

    response_data = {
        "status": "queued",
        "request_id": request_id,
        "position": position_info["position"],
        "estimated_wait": position_info["estimated_seconds"],
        "message": _("Request queued (position {pos})").format(pos=position_info["position"]),
        "user_message_id": user_message_id,
    }
    if resize_notice:
        response_data["resize_notice"] = resize_notice
        response_data["resize_notice_id"] = resize_notice_id
    return jsonify(response_data)


@bp.route("/cancel_task/<task_id>", methods=["POST"])
def cancel_task(task_id):
    """Cancel a running streaming task."""
    if "login" not in session:
        return jsonify({"error": _("Not authorized")}), 401
    user_id = session["login"]
    cancelled = current_app.request_queue.cancel_task(task_id)
    if not cancelled:
        return jsonify({"error": _("Task not found")}), 404
    current_app.logger.info(f"User {user_id} cancelled task {task_id}")
    return jsonify({"status": "ok"})
