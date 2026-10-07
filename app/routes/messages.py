# app/routes/messages.py
import base64
import json
import mimetypes
import re

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
    # Multi-attachment: every "file" part in order (frontend sends the legacy
    # single slot first, then the multi-queue).
    extra_images: list[dict[str, str | None]] = []
    doc_part: dict[str, str | None] | None = None

    if request.content_type and "multipart/form-data" in request.content_type:
        message_text = request.form.get("message", "")
        uploads = [f for f in request.files.getlist("file") if f and f.filename]
        max_chat_images = current_app.config.get("MAX_CHAT_IMAGES", 4)
        for upload in uploads:
            file_bytes = upload.read()
            part_size = len(file_bytes)
            part_data = base64.b64encode(file_bytes).decode("utf-8")
            part_type = upload.content_type or mimetypes.guess_type(upload.filename)[0] or "application/octet-stream"
            part_name = upload.filename

            from app.utils import check_upload_quota

            quota_error = check_upload_quota(user_id, part_size)
            if quota_error:
                return jsonify({"error": quota_error}), 413

            if part_type.startswith("image/"):
                if len(extra_images) >= max_chat_images:
                    lang_hdr = session.get("language", "ru")
                    with force_locale(lang_hdr):
                        error_text = _("max_images_reached").format(max=max_chat_images)
                    return jsonify({"error": error_text}), 400
                extra_images.append({"data": part_data, "type": part_type, "name": part_name, "size": part_size})
            elif current_app.modules["audio"].is_audio_file(part_type, part_name):
                # audio keeps the legacy single-slot semantics
                if file_data is None:
                    file_data = part_data
                    file_type = part_type
                    file_name = part_name
                    file_size_bytes = part_size
            else:
                # document (PDF/DOCX/...) — one per message
                if doc_part is not None:
                    lang_hdr = session.get("language", "ru")
                    with force_locale(lang_hdr):
                        error_text = _("Multiple documents are not supported in one message")
                    return jsonify({"error": error_text}), 400
                doc_part = {"data": part_data, "type": part_type, "name": part_name, "size": part_size}

        # Legacy single slot: first image becomes file_data (keeps display,
        # download and the combined voice+image path working), the rest of the
        # images travel in extra_images.
        if extra_images and file_data is None:
            first = extra_images.pop(0)
            file_data = first["data"]
            file_type = first["type"]
            file_name = first["name"]
            file_size_bytes = first["size"]

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

    if not message_text and not file_data and doc_part is None:
        return jsonify({"error": _("Empty message")}), 400

    response_style = session.get("response_style", "neutral")

    request_type = "text"
    if doc_part is not None:
        # Document attached in chat: index it into RAG first, then answer the
        # question over it (images, if any, provide the visual context).
        request_type = "doc_chat"
    elif file_data and file_type:
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

    # A primary attachment image when the legacy single slot holds image bytes
    # (image-only and voice+image both land here); audio and documents keep
    # their own save logic below.
    is_image = bool(file_data and file_type and file_type.startswith("image/"))
    if is_image:
        max_size = current_app.config.get("MAX_IMAGE_SIZE", 1536)
        if len(extra_images) > 0:
            # several images: keep the context budget, downscale harder
            max_size = current_app.config.get("MAX_IMAGE_SIZE_MULTI", 1024)
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

    # Downscale every extra image with the same cap.
    resized_extra: list[dict[str, str | None]] = []
    max_size = current_app.config.get("MAX_IMAGE_SIZE", 1536)
    if len(extra_images) > 0:
        max_size = current_app.config.get("MAX_IMAGE_SIZE_MULTI", 1024)
    for part in extra_images:
        n_data, n_type, n_name, _r, _od, _nd = resize_image_if_needed(
            part["data"], part["type"], part["name"], max_size
        )
        resized_extra.append({"data": n_data, "type": n_type, "name": n_name})
    extra_images = resized_extra

    if is_image:
        file_path = save_uploaded_file(
            file_data=file_data,
            filename=file_name,
            session_id=session_id,
            upload_folder=current_app.config["UPLOAD_FOLDER"],
            user_id=user_id,
        )

    # Persist EVERY extra image to disk too. Without this, images 2..N
    # existed only as base64 inside the content JSON, and the history
    # loader strips file_data for messages that have a file_path — so the
    # extra thumbnails were unrenderable after a page reload.
    for part in extra_images:
        part["path"] = save_uploaded_file(
            file_data=part["data"],
            filename=part["name"],
            session_id=session_id,
            upload_folder=current_app.config["UPLOAD_FOLDER"],
            user_id=user_id,
        )

    # An uploaded audio file is saved like an image so a session with audio
    # attachments opens without shipping its base64 payload; the saved file
    # becomes the row-level primary attachment.
    if file_data and file_type and current_app.modules["audio"].is_audio_file(file_type, file_name):
        file_path = save_uploaded_file(
            file_data=file_data,
            filename=file_name,
            session_id=session_id,
            upload_folder=current_app.config["UPLOAD_FOLDER"],
            user_id=user_id,
        )

    # The chat-attached document is fully indexed into RAG by the doc_chat
    # worker; the saved copy gives history a plain file_path too. A
    # document-only message makes the document the row-level attachment;
    # with a primary image the document lives only in the content JSON.
    if doc_part is not None:
        doc_part["path"] = save_uploaded_file(
            file_data=doc_part["data"],
            filename=doc_part["name"],
            session_id=session_id,
            upload_folder=current_app.config["UPLOAD_FOLDER"],
            user_id=user_id,
        )
        if not file_path:
            file_path = doc_part["path"]

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
        # The first attachment part carries file_path too (its file was saved
        # above) so the history loader can strip its base64 payload.
        user_content.append(
            {
                "type": content_type,
                "file_type": file_type,
                "file_name": file_name,
                "file_path": file_path if content_type in ("image", "audio") else None,
            }
        )
    for part in extra_images:
        user_content.append(
            {
                "type": "image",
                "file_type": part["type"],
                "file_name": part["name"],
                "file_path": part["path"],
            }
        )
    if doc_part is not None:
        user_content.append(
            {
                "type": "file",
                "file_type": doc_part["type"],
                "file_name": doc_part["name"],
                "file_path": doc_part.get("path"),
            }
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

        # Determine which file is audio and which is image. Two attachment
        # layouts exist: the image in the legacy single slot (file_data) with
        # the voice in "voice", or — the v12.4 multi-attachment flow — the
        # voice in the legacy slot (file_data) with the image in the multi
        # "file" parts (extra_images). Pair them whichever way they came.
        audio_file_data = voice_file_data or file_data
        audio_file_type = voice_file_type or file_type
        audio_file_name = voice_file_name or file_name
        img_data = None
        img_type = None
        img_name = None
        extra_img_parts: list[dict[str, str | None]] = []
        if voice_file_data:
            # Voice in "voice", image (if any) in the legacy slot
            img_data = file_data
            img_type = file_type
            img_name = file_name
        elif file_data and file_type and file_type.startswith("audio/"):
            # Voice in the legacy slot: the image came from the multi queue
            if extra_images:
                first_img = extra_images[0]
                img_data = first_img["data"]
                img_type = first_img["type"]
                img_name = first_img["name"]
                extra_img_parts = extra_images[1:]

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
            all_images = [img_data] + [p["data"] for p in extra_img_parts]
            if len(all_images) > 1:
                request_data["images"] = all_images
        request_id, position_info = current_app.request_queue.add_request(
            user_id, session_id, request_data, user_class, lang=session.get("language", "ru")
        )
        current_app.logger.info(
            f"send_message: queued request_id={request_id} type={request_type} "
            f"queue={position_info.get('queue_type')} pos={position_info.get('position')}"
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
        if extra_images:
            request_data["images"] = [file_data] + [p["data"] for p in extra_images]
    elif request_type == "doc_chat":
        # The document must be visible to the model: index it first (the same
        # pipeline as Documents uploads), then re-queue the question.
        request_data = {
            "type": "doc_chat",
            "text": message_text,
            "doc_file_data": doc_part["data"],
            "doc_file_type": doc_part["type"],
            "doc_file_name": doc_part["name"],
            "preview": (message_text[:50] + "...") if message_text else (doc_part["name"] or _("Text request")),
            "response_style": response_style,
            "stream": True,
            "current_message_id": user_message_id,
        }
        if extra_images:
            request_data["images"] = (
                [file_data] + [p["data"] for p in extra_images] if file_data else [p["data"] for p in extra_images]
            )
    else:
        request_data = {
            "type": "text",
            "text": message_text,
            "current_message_id": user_message_id,
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


def _extract_html_code_block(content: str) -> str | None:
    """Return the first ```html fenced code block from a message, or None."""
    marker = "```html"
    start = content.find(marker)
    if start == -1:
        return None
    start += len(marker)
    end = content.find("```", start)
    if end == -1:
        return None
    return content[start:end].strip() or None


_IMPORT_MAP_RE = re.compile(r'<script[^>]*\btype=["\']importmap["\']', re.IGNORECASE)
_THREE_CDN_VERSION_RE = re.compile(r"https://(?:unpkg\.com|cdn\.jsdelivr\.net/npm)/three@([0-9]+\.[0-9]+\.[0-9]+)/")
_BARE_THREE_IMPORT_RE = re.compile(r"""from\s+['"]three['"]|import\s+['"]three['"]""")
_THREE_BUILD_URL_RE = re.compile(
    r"https://(?:unpkg\.com|cdn\.jsdelivr\.net/npm)/three@[0-9]+\.[0-9]+\.[0-9]+/build/three\.(?:module|min)\.js"
)
_THREE_ADDONS_URL_RE = re.compile(
    r"https://(?:unpkg\.com|cdn\.jsdelivr\.net/npm)/three@[0-9]+\.[0-9]+\.[0-9]+/examples/jsm/"
)


def _ensure_three_importmap(html: str) -> str:
    """Repair generated pages that skipped the Three.js import map.

    The model usually writes the canonical pattern (<script type="importmap">
    mapping ``three`` and ``three/addons/``, then bare specifiers), but it
    sometimes emits absolute CDN URLs instead, e.g.
    ``import { OrbitControls } from 'https://unpkg.com/three@0.160.0/examples/jsm/...'``.
    Three r160+ addon modules import the bare ``three`` specifier internally,
    so without the map the whole module graph fails to link and the preview is
    a blank screen even on a saved file. This injects a map (version taken from
    the first CDN URL, defaulting to 0.160.0) and rewrites the absolute imports
    to the bare aliases. Pages with an existing map are returned untouched.
    """
    if _IMPORT_MAP_RE.search(html):
        return html

    version_match = _THREE_CDN_VERSION_RE.search(html)
    if version_match is None and not _BARE_THREE_IMPORT_RE.search(html):
        return html
    version = version_match.group(1) if version_match else "0.160.0"

    html = _THREE_BUILD_URL_RE.sub("three", html)
    html = _THREE_ADDONS_URL_RE.sub("three/addons/", html)

    imports = {
        "three": f"https://unpkg.com/three@{version}/build/three.module.js",
        "three/addons/": f"https://unpkg.com/three@{version}/examples/jsm/",
    }
    importmap = '<script type="importmap">' + json.dumps({"imports": imports}) + "</script>"
    head = re.search(r"<head[^>]*>", html, re.IGNORECASE)
    html = html[: head.end()] + "\n" + importmap + html[head.end() :] if head else importmap + "\n" + html

    return html


@bp.route("/html-preview/<int:message_id>", methods=["GET"])
def api_html_preview(message_id: int):
    """Serve an assistant message's ```html code block as a standalone page.

    The chat's ▶ button used to open a blob: URL, which inherits the strict
    chat CSP (script-src 'self') and blocks CDN imports — generated pages with
    e.g. Three.js from jsdelivr rendered a blank screen. This endpoint serves
    the HTML with its own relaxed CSP so the preview works while the chat page
    itself stays strictly sandboxed. Pages that skipped the Three.js import map
    are repaired server-side (_ensure_three_importmap) — absolute CDN addon
    imports fail to link without it and the preview would stay blank.
    """
    from flask import Response

    if "login" not in session:
        return jsonify({"error": _("Not authorized")}), 401

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, session_id, content FROM messages WHERE id = %s", (message_id,))
        row = c.fetchone()

    if not row:
        return jsonify({"error": _("Message not found")}), 404

    message = dict(row) if not isinstance(row, dict) else row
    if not validate_session_ownership(message["session_id"], session["login"]):
        current_app.logger.warning(
            f"User {session['login']} attempted to preview message {message_id} in a foreign session"
        )
        return jsonify({"error": _("Message not found")}), 404

    html = _extract_html_code_block(message.get("content") or "")
    if not html:
        return jsonify({"error": _("No HTML code block found in this message")}), 404

    # Relax only what generated pages legitimately need: CDN imports, inline
    # scripts/styles, and eval (legacy UMD bundles like cdnjs three.js r126
    # self-execute via Function() — without 'unsafe-eval' they abort and
    # THREE.Scene never exists). Everything else (frames, other origins) stays
    # locked down. strict-origin-when-cross-origin + frame-ancestors 'none'
    # keep the preview from being embeddable elsewhere.
    csp = (
        "default-src 'none'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://unpkg.com https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        "img-src 'self' data: blob: https:; "
        "font-src 'self' data: https://cdn.jsdelivr.net https://fonts.gstatic.com; "
        "connect-src 'self' https://cdn.jsdelivr.net https://unpkg.com https://cdnjs.cloudflare.com; "
        "media-src 'self' blob: data:; "
        "worker-src 'self' blob:; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; form-action 'self'"
    )
    # Nested code fences (``` inside the html block) would break the page —
    # strip residual fence markers defensively.
    html = re.sub(r"^```(?:html)?\s*", "", html)
    html = re.sub(r"\s*```$", "", html)
    html = _ensure_three_importmap(html)

    return Response(html, mimetype="text/html", headers={"Content-Security-Policy": csp, "X-Own-CSP": "1"})
