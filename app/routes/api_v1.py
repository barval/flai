"""OpenAI-compatible public API foundation."""

import io
import json
import mimetypes
import os
import time
import uuid
from base64 import b64decode, b64encode
from collections.abc import Callable
from datetime import datetime
from functools import wraps
from threading import BoundedSemaphore
from typing import Any, cast

import magic
from flask import Blueprint, Response, current_app, g, jsonify, request, send_file, stream_with_context
from flask_babel import gettext as _
from flask_limiter.errors import RateLimitExceeded
from werkzeug.exceptions import MethodNotAllowed, RequestEntityTooLarge
from werkzeug.utils import secure_filename

from app import db, limiter
from app.api_bridge import (
    ApiImageRejectedError,
    ApiSessionNotFoundError,
    ApiTaskError,
    ApiTaskTimeoutError,
    enqueue_chat,
    enqueue_embeddings,
    enqueue_image_edit,
    enqueue_image_generation,
    enqueue_transcription,
    enqueue_video_generation,
    get_api_task_message,
    get_api_task_owner,
    list_api_tasks,
    normalize_chat_messages,
    register_api_task,
    resolve_api_session,
    sanitize_api_task_result,
    serialize_chat_completion,
    serialize_embeddings,
    stream_chat,
    wait_for_result,
)
from app.api_tokens import touch_api_token, verify_api_token
from app.routes.documents import validate_file
from app.utils import (
    check_document_quota,
    check_upload_quota,
    convert_to_supported_format_if_needed,
    resize_image_if_needed,
    save_uploaded_file,
    validate_session_ownership,
)

API_PREFIX = "/v1"
DEFAULT_API_RATE_LIMIT = "60 per minute;1000 per hour"
DEFAULT_MAX_CONCURRENT_WAITS = 64
CORS_ALLOWED_HEADERS = "Authorization, Content-Type"
CORS_MAX_AGE = "600"
CORS_RETRY_AFTER = "1"

bp = Blueprint("api_v1", __name__, url_prefix=API_PREFIX)

API_MODELS: list[dict[str, str]] = [
    {"id": "flai-chat", "object": "model", "owned_by": "flai", "description": "Router-selected chat"},
    {"id": "flai-reasoning", "object": "model", "owned_by": "flai", "description": "Reasoning requests"},
    {"id": "flai-rag", "object": "model", "owned_by": "flai", "description": "Answers grounded in documents"},
    {"id": "flai-search", "object": "model", "owned_by": "flai", "description": "Web search answers"},
    {"id": "flai-multimodal", "object": "model", "owned_by": "flai", "description": "Image understanding"},
    {"id": "flai-image", "object": "model", "owned_by": "flai", "description": "Image generation and editing"},
    {"id": "flai-video", "object": "model", "owned_by": "flai", "description": "Video generation"},
    {"id": "flai-tts", "object": "model", "owned_by": "flai", "description": "Speech synthesis"},
    {"id": "flai-stt", "object": "model", "owned_by": "flai", "description": "Speech transcription"},
    {"id": "flai-embeddings", "object": "model", "owned_by": "flai", "description": "Text embeddings"},
    {"id": "flai-rlm", "object": "model", "owned_by": "flai", "description": "Deep analysis"},
]


def api_error(
    status: int,
    err_type: str,
    code: str,
    message: str,
    param: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[Any, int]:
    """Return the common OpenAI-shaped API error body."""
    localized_message = message if message.startswith("⚠️ ") else f"⚠️ {message}"
    response = jsonify({"error": {"message": localized_message, "type": err_type, "param": param, "code": code}})
    if headers:
        response.headers.extend(headers)
    return response, status


def _unauthorized() -> tuple[Any, int, dict[str, str]]:
    response, status = api_error(
        401,
        "authentication_error",
        "invalid_api_key",
        _("api_error_invalid_api_key"),
    )
    return response, status, {"WWW-Authenticate": "Bearer"}


def api_token_required(view: Callable) -> Callable:
    """Authenticate a bearer token and set request-local API identity."""

    @wraps(view)
    def wrapper(*args: Any, **kwargs: Any):
        if not current_app.config.get("API_ENABLED", True):
            return api_error(503, "service_unavailable", "api_disabled", _("api_error_api_disabled"))

        authorization = request.headers.get("Authorization", "")
        token = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
        token_record = verify_api_token(token)
        if token_record is None:
            return _unauthorized()

        user = token_record["user"]
        g.api_user = {
            "token_id": token_record["token_id"],
            "login": user["login"],
            "name": user.get("name", user["login"]),
            "service_class": user.get("service_class", 2),
            "is_admin": user.get("is_admin", False),
            "language": user.get("language", "ru"),
            "voice_gender": user.get("voice_gender", "male"),
            "theme": user.get("theme", "light"),
            "response_style": user.get("response_style", "neutral"),
        }
        touch_api_token(token_record["token_id"])
        return view(*args, **kwargs)

    return wrapper


def _api_rate_key() -> str:
    """Rate-limit bucket of the current request.

    Keyed by the key owner, not by IP, so several clients behind one NAT share
    a budget and one noisy client cannot spend another user's quota. Falls back
    to the remote address when the request is not authenticated.
    """
    user = getattr(g, "api_user", None)
    if user:
        return f"api:{user['login']}"
    return f"api:{request.remote_addr or 'unknown'}"


def _api_rate_limit() -> str:
    """The configured budget, read per request so tests and .env can change it."""
    return str(current_app.config.get("API_RATE_LIMIT", DEFAULT_API_RATE_LIMIT))


def _is_api_path(path: str) -> bool:
    """Match `/v1` and its child routes without treating `/v1evil` as API."""
    return path == API_PREFIX or path.startswith(f"{API_PREFIX}/")


def parse_cors_origins(raw: Any) -> list[str]:
    """Split the configured CORS allowlist into exact origins.

    Origins are matched literally, never by prefix: `https://a.example` must
    not admit `https://a.example.evil.example`.
    """
    return [origin.strip() for origin in str(raw or "").split(",") if origin.strip()]


def cors_allowlist() -> list[str]:
    """The allowed origins, re-read per request so .env changes need no cache."""
    return parse_cors_origins(current_app.config.get("API_CORS_ORIGINS", ""))


def wait_slots(app: Any) -> Any:
    """The per-app pool of requests allowed to wait for a queued task.

    Sizing is read from config the first time a request asks for the pool, so
    a test can shrink it; afterwards the real pool is kept because resizing a
    live pool would drop in-flight waiters. `threading` matches the rest of the
    server: gunicorn runs one gevent worker without monkey patching and the GPU
    queue already guards itself with a `threading.Lock`.
    """
    slots = getattr(app, "_api_wait_slots", None)
    if slots is None:
        size = int(app.config.get("API_MAX_CONCURRENT_WAITS", DEFAULT_MAX_CONCURRENT_WAITS))
        slots = BoundedSemaphore(max(1, size))
        app._api_wait_slots = slots
    return slots


def _acquire_wait_slot() -> Any:
    """Take a wait slot, or answer 429 with Retry-After when none is free."""
    slots = wait_slots(current_app)
    if not slots.acquire(blocking=False):
        return api_error(
            429,
            "rate_limit_error",
            "too_many_requests",
            _("api_error_too_many_requests"),
            headers={"Retry-After": CORS_RETRY_AFTER},
        )
    return None


def _release_wait_slot() -> None:
    slots = getattr(current_app, "_api_wait_slots", None)
    if slots is not None:
        slots.release()


@bp.after_request
def add_api_cors_headers(response: Any) -> Any:
    """Answer browser CORS requests for `/v1` only.

    A browser client needs this to read a response at all, but the allowlist is
    opt-in and applies strictly to the API prefix, so it can never be used to
    read the web UI cross-origin. Credentials are allowed because the API is
    Bearer-authenticated and never relies on a cookie.
    """
    if not _is_api_path(request.path):
        return response
    origin = request.headers.get("Origin")
    if not origin:
        return response
    response.headers.add("Vary", "Origin")
    if origin not in cors_allowlist():
        return response
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Headers"] = CORS_ALLOWED_HEADERS
    if request.method == "OPTIONS":
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Max-Age"] = CORS_MAX_AGE
    return response


@bp.app_errorhandler(RateLimitExceeded)
def rate_limited(error: Any) -> Any:
    """Report a spent budget in the OpenAI error envelope.

    Registered app-wide because a blueprint handler only covers its own
    blueprint; the auth blueprint keeps its own HTML 429 page.
    """
    if not _is_api_path(request.path):
        return error.get_response(request.environ)
    current_limit = limiter.current_limit
    headers = {"Retry-After": str(max(1, current_limit.reset_at - int(time.time())))} if current_limit else {}
    return api_error(429, "rate_limit_error", "rate_limit_exceeded", _("api_error_rate_limited"), headers=headers)


@bp.app_errorhandler(405)
def method_not_allowed(error: Any) -> Any:
    """Keep a wrong HTTP method inside the OpenAI error envelope.

    Flask dispatches routing errors (405, unknown path) to app-level handlers
    only, so this is registered app-wide and falls through for every path
    outside the API prefix.
    """
    if not _is_api_path(request.path):
        valid = getattr(error, "valid_methods", None)
        return MethodNotAllowed(valid_methods=valid).get_response(request.environ)
    return api_error(405, "invalid_request_error", "method_not_allowed", _("Method not allowed"))


@bp.app_errorhandler(RequestEntityTooLarge)
def api_payload_too_large(error: Any) -> Any:
    """Return upload-size errors in the API envelope without changing web routes."""
    if not _is_api_path(request.path):
        return error.get_response(request.environ)
    return api_error(413, "invalid_request_error", "request_too_large", _("Uploaded file is too large"))


@bp.route("/models", methods=["GET"])
@api_token_required
def list_models():
    """List supported FLAI capabilities as OpenAI model objects."""
    return jsonify({"object": "list", "data": API_MODELS})


@bp.route("/flai/me", methods=["GET"])
@api_token_required
def api_me():
    """Report the authenticated user's identity and shipped capabilities."""
    return jsonify(
        {
            "login": g.api_user["login"],
            "service_class": g.api_user["service_class"],
            "is_admin": g.api_user["is_admin"],
            "language": g.api_user["language"],
            "response_style": g.api_user["response_style"],
            "rate_limit": current_app.config.get("API_RATE_LIMIT", DEFAULT_API_RATE_LIMIT),
            "max_concurrent_waits": int(
                current_app.config.get("API_MAX_CONCURRENT_WAITS", DEFAULT_MAX_CONCURRENT_WAITS)
            ),
            "capabilities": {
                "chat_completions": True,
                "streaming": True,
                "embeddings": True,
                "audio_speech": bool(current_app.modules.get("tts") and current_app.modules["tts"].available),
                "audio_transcriptions": bool(
                    current_app.modules.get("audio") and current_app.modules["audio"].available
                ),
                "images": bool(
                    current_app.modules.get("image")
                    and current_app.modules["image"].available
                    and current_app.modules.get("multimodal")
                    and current_app.modules["multimodal"].available
                ),
                "videos": bool(
                    current_app.modules.get("video")
                    and current_app.modules["video"].available
                    and current_app.modules.get("multimodal")
                    and current_app.modules["multimodal"].available
                ),
                "documents": True,
                "rlm": bool(current_app.config.get("RLM_ENABLED", True)),
                "tools": False,
                "response_format_json_schema": False,
            },
        }
    )


@bp.route("/flai/tasks", methods=["GET"])
@api_token_required
@limiter.limit(_api_rate_limit, key_func=_api_rate_key)
def api_list_tasks():
    """List recent tasks registered to the authenticated API-key owner."""
    raw_limit = request.args.get("limit", "20")
    try:
        limit = max(1, min(100, int(raw_limit)))
    except ValueError:
        return api_error(400, "invalid_request_error", "invalid_limit", _("'limit' must be an integer"))

    records = list_api_tasks(g.api_user["login"], limit=limit)
    queue = cast(Any, current_app).request_queue
    data = []
    seen_task_ids: set[str] = set()
    for record in records:
        task_id = record["task_id"]
        result = queue.check_result(task_id)
        task = _serialize_api_task(task_id, record, result)
        if task["id"] not in seen_task_ids:
            seen_task_ids.add(task["id"])
            data.append(task)
    return jsonify({"object": "list", "data": data})


@bp.route("/flai/tasks/<task_id>", methods=["GET"])
@api_token_required
@limiter.limit(_api_rate_limit, key_func=_api_rate_key)
def api_get_task(task_id: str):
    """Read status/result only after verifying the registry owner."""
    record = get_api_task_owner(task_id)
    if not record or record.get("login") != g.api_user["login"]:
        return api_error(404, "invalid_request_error", "task_not_found", _("Task not found"))
    queue = cast(Any, current_app).request_queue
    result = queue.check_result(task_id)
    return jsonify(_serialize_api_task(task_id, record, result))


@bp.route("/flai/tasks/<task_id>/cancel", methods=["POST"])
@api_token_required
@limiter.limit(_api_rate_limit, key_func=_api_rate_key)
def api_cancel_task(task_id: str):
    """Cancel an owned active task; queued and terminal tasks are not cancellable."""
    record = get_api_task_owner(task_id)
    if not record or record.get("login") != g.api_user["login"]:
        return api_error(404, "invalid_request_error", "task_not_found", _("Task not found"))

    queue = cast(Any, current_app).request_queue
    result = queue.check_result(task_id)
    if result and result.get("status") in ("completed", "error"):
        return api_error(409, "invalid_request_error", "task_not_cancellable", _("Task is no longer cancellable"))

    status = _task_pending_status(queue, g.api_user["login"], g.api_user["language"], task_id)
    if status == "queued":
        return api_error(409, "invalid_request_error", "task_not_cancellable", _("Queued tasks cannot be cancelled"))
    if not queue.cancel_task(task_id):
        return api_error(409, "invalid_request_error", "task_not_cancellable", _("Task is no longer cancellable"))
    result = queue.check_result(task_id)
    if result and result.get("status") in ("completed", "error"):
        return api_error(409, "invalid_request_error", "task_not_cancellable", _("Task is no longer cancellable"))
    return jsonify({"status": "cancelling", "task_id": task_id})


@bp.route("/flai/tasks/<task_id>/content", methods=["GET"])
@api_token_required
@limiter.limit(_api_rate_limit, key_func=_api_rate_key)
def api_task_content(task_id: str):
    """Download generated media after checking task and message ownership."""
    record = get_api_task_owner(task_id)
    if not record or record.get("login") != g.api_user["login"]:
        return api_error(404, "invalid_request_error", "task_not_found", _("Task not found"))

    queue = cast(Any, current_app).request_queue
    seen = set()
    active_id = task_id
    active_record = record
    result = queue.check_result(active_id)
    while result and result.get("status") == "completed":
        inner = result.get("result") or {}
        child_id = inner.get("request_id") if inner.get("status") == "queued" else None
        if not child_id:
            break
        child_id = str(child_id)
        if child_id in seen:
            return api_error(404, "invalid_request_error", "media_not_found", _("Generated media not found"))
        seen.add(child_id)
        child = get_api_task_owner(child_id)
        if (
            not child
            or child.get("login") != record.get("login")
            or child.get("session_id") != record.get("session_id")
            or child.get("endpoint") != record.get("endpoint")
        ):
            return api_error(404, "invalid_request_error", "media_not_found", _("Generated media not found"))
        active_id = child_id
        active_record = child
        result = queue.check_result(active_id)

    message_id = (
        (result.get("result") or {}).get("message_id") if result and result.get("status") == "completed" else None
    )
    if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
        return api_error(404, "invalid_request_error", "media_not_found", _("Generated media not found"))

    media = get_api_task_message(g.api_user["login"], active_record.get("session_id", ""), message_id)
    if not media:
        return api_error(404, "invalid_request_error", "media_not_found", _("Generated media not found"))

    upload_root = os.path.realpath(current_app.config["UPLOAD_FOLDER"])
    target = os.path.realpath(os.path.join(upload_root, media.get("file_path") or ""))
    try:
        inside_root = os.path.commonpath((upload_root, target)) == upload_root
    except ValueError:
        inside_root = False
    if not inside_root or not os.path.isfile(target):
        current_app.logger.warning("Generated API task media failed upload-root containment")
        return api_error(404, "invalid_request_error", "media_not_found", _("Generated media not found"))

    filename = secure_filename(os.path.basename(media.get("file_name") or target)) or os.path.basename(target)
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return send_file(target, mimetype=media_type, as_attachment=True, download_name=filename)


def _serialize_api_task(task_id: str, record: dict[str, str], result: dict[str, Any] | None) -> dict[str, Any]:
    """Build a task status object without serializing raw queue/file data."""
    queue = cast(Any, current_app).request_queue
    original_task_id = task_id
    parent_task_id = None
    nested = (result or {}).get("result") or {}
    child_id = nested.get("request_id") if nested.get("status") == "queued" else None
    while child_id:
        parent_task_id = task_id
        task_id = str(child_id)
        child_record = get_api_task_owner(task_id)
        if child_record is None:
            registered = register_api_task(
                g.api_user,
                task_id,
                record.get("session_id", ""),
                record.get("endpoint", ""),
                only_if_absent=True,
            )
            child_record = get_api_task_owner(task_id) if registered else None
        if (
            child_record is None
            or child_record.get("login") != g.api_user["login"]
            or child_record.get("session_id", "") != record.get("session_id", "")
            or child_record.get("endpoint", "") != record.get("endpoint", "")
        ):
            return {
                "id": original_task_id,
                "object": "flai.task",
                "status": "error",
                "endpoint": record.get("endpoint"),
                "created_at": float(record.get("created_at", 0)),
                "error": f"⚠️ {_('Task %s failed') % original_task_id}",
            }
        record = child_record
        result = queue.check_result(task_id)
        nested = (result or {}).get("result") or {}
        child_id = nested.get("request_id") if nested.get("status") == "queued" else None

    if parent_task_id and (not result or result.get("status") not in ("completed", "error")):
        return {
            "id": task_id,
            "object": "flai.task",
            "status": _task_pending_status(queue, g.api_user["login"], g.api_user["language"], task_id),
            "endpoint": record.get("endpoint"),
            "created_at": float(record.get("created_at", 0)),
            "position": None,
            "parent_task_id": parent_task_id,
        }

    if result and result.get("status") in ("completed", "error"):
        inner_result = result.get("result") or {}
        safe_result = sanitize_api_task_result(inner_result)
        prompt_tokens = safe_result.pop("prompt_tokens", None)
        completion_tokens = safe_result.pop("completion_tokens", None)
        if prompt_tokens is not None or completion_tokens is not None:
            safe_result["usage"] = {
                key: value
                for key, value in {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                }.items()
                if value is not None
            }
        task_status = result["status"]
        if task_status == "completed" and inner_result.get("is_error") and "response" not in inner_result:
            task_status = "error"
        response: dict[str, Any] = {
            "id": task_id,
            "object": "flai.task",
            "status": task_status,
            "endpoint": record.get("endpoint"),
            "created_at": float(record.get("created_at", 0)),
            "result": safe_result,
        }
        if task_status == "error":
            error = result.get("error") or inner_result.get("error")
            response["error"] = (
                error if isinstance(error, str) and error.startswith("⚠️ ") else f"⚠️ {_('Task %s failed') % task_id}"
            )
        elif isinstance(inner_result.get("message_id"), int) and record.get("endpoint") in (
            "/v1/images/generations",
            "/v1/images/edits",
            "/v1/videos",
        ):
            response["content_url"] = f"/v1/flai/tasks/{task_id}/content"
            if record.get("endpoint") == "/v1/videos":
                response["url"] = response["content_url"]
        return response

    pending = _task_pending_status(queue, g.api_user["login"], g.api_user["language"], task_id)
    response = {
        "id": task_id,
        "object": "flai.task",
        "status": pending,
        "endpoint": record.get("endpoint"),
        "created_at": float(record.get("created_at", 0)),
        "position": None,
    }
    return response


def _task_pending_status(queue: Any, login: str, lang: str, task_id: str) -> str:
    """Distinguish processing from queued without inventing a queue position."""
    status = queue.get_user_requests_status(login, lang=lang)
    processing = status.get("processing") or {}
    if processing.get("id") == task_id:
        return "processing"
    if any(item.get("id") == task_id for item in status.get("queued", [])):
        processing_keys = (queue.processing_key, queue.slow_processing_key)
        for key in processing_keys:
            if queue.redis.hexists(key, task_id):
                return "processing"
    return "queued"


def _async_task_response(task_id: str, status_url: str, output_format: str | None = None) -> tuple[Any, int]:
    """Return the common 202 body for explicit asynchronous operations."""
    body = {"id": task_id, "object": "flai.task", "status": "queued", "poll_url": status_url}
    if output_format is not None:
        body["output_format"] = output_format
    return jsonify(body), 202


def _resolve_media_session(payload: dict[str, Any]) -> str | tuple[Any, int]:
    """Resolve an API-owned session for an explicit image/video operation."""
    raw_metadata = payload.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    requested_session_id = metadata.get("session_id")
    if requested_session_id is not None and not isinstance(requested_session_id, str):
        return api_error(
            400, "invalid_request_error", "invalid_session_id", _("'metadata.session_id' must be a string")
        )
    client_user = payload.get("user")
    if client_user is not None and not isinstance(client_user, str):
        return api_error(400, "invalid_request_error", "invalid_user", _("'user' must be a string"))
    try:
        return resolve_api_session(g.api_user, requested_session_id, client_user)
    except ApiSessionNotFoundError:
        return api_error(404, "invalid_request_error", "session_not_found", _("Chat session not found"))


@bp.route("/images/generations", methods=["POST"])
@api_token_required
@limiter.limit(_api_rate_limit, key_func=_api_rate_key)
def image_generations():
    """Queue native image generation; clients retrieve the image from task content."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return api_error(400, "invalid_request_error", "invalid_body", _("Request body must be a JSON object"))
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return api_error(400, "invalid_request_error", "invalid_input", _("'prompt' must be a non-empty string"))
    if payload.get("response_format", "url") != "url":
        return api_error(
            400,
            "invalid_request_error",
            "invalid_response_format",
            _("Only response_format 'url' is supported for asynchronous image generation"),
        )

    image_module = current_app.modules.get("image")
    if image_module is None:
        return api_error(503, "service_unavailable", "image_unavailable", _("Image generation module unavailable"))
    image_module.check_availability()
    if not image_module.available:
        return api_error(503, "service_unavailable", "image_unavailable", _("Image generation module unavailable"))

    session_id = _resolve_media_session(payload)
    if not isinstance(session_id, str):
        return session_id
    task_id, _queue_info = enqueue_image_generation(
        g.api_user,
        session_id,
        prompt.strip(),
        g.api_user.get("response_style", "neutral"),
    )
    register_api_task(g.api_user, task_id, session_id, "/v1/images/generations")
    response, status = _async_task_response(task_id, f"/v1/flai/tasks/{task_id}", output_format="url")
    response.headers["Location"] = f"/v1/flai/tasks/{task_id}"
    return response, status


@bp.route("/images/edits", methods=["POST"])
@api_token_required
@limiter.limit(_api_rate_limit, key_func=_api_rate_key)
def image_edits():
    """Validate an uploaded source image and queue a native API image edit."""
    prompt = request.form.get("prompt", "")
    if not prompt.strip():
        return api_error(400, "invalid_request_error", "invalid_input", _("'prompt' must be a non-empty string"))
    if request.form.get("response_format", "url") != "url":
        return api_error(
            400,
            "invalid_request_error",
            "invalid_response_format",
            _("Only response_format 'url' is supported for asynchronous image edits"),
        )
    if "image" not in request.files or not request.files["image"].filename:
        return api_error(400, "invalid_request_error", "invalid_request", _("Missing 'image' upload"))

    upload = request.files["image"]
    source_bytes = upload.read()
    if not source_bytes:
        return api_error(400, "invalid_request_error", "invalid_input", _("Uploaded file is empty"))
    detected_type = magic.from_buffer(source_bytes[:2048], mime=True) if source_bytes else None
    if not detected_type or not detected_type.startswith("image/"):
        return api_error(400, "invalid_request_error", "invalid_input", _("Unsupported image format"))
    quota_error = check_upload_quota(g.api_user["login"], len(source_bytes))
    if quota_error:
        return api_error(413, "invalid_request_error", "upload_quota_exceeded", quota_error)

    image_module = current_app.modules.get("image")
    multimodal = current_app.modules.get("multimodal")
    if image_module is None or multimodal is None:
        return api_error(503, "service_unavailable", "image_unavailable", _("Image editing module unavailable"))
    image_module.check_availability()
    if not image_module.available or not multimodal.available:
        return api_error(503, "service_unavailable", "image_unavailable", _("Image editing module unavailable"))

    filename = secure_filename(upload.filename or "image.png") or "image.png"
    file_type = detected_type
    file_data = b64encode(source_bytes).decode("ascii")
    file_data, file_type, filename, _converted = convert_to_supported_format_if_needed(file_data, file_type, filename)
    file_data, file_type, filename, _resized, _original_size, _new_size = resize_image_if_needed(
        file_data,
        file_type,
        filename,
        current_app.config.get("MAX_IMAGE_SIZE", 1536),
    )
    image_size = int(len(file_data) * 3 / 4)
    valid, validation_error = multimodal.validate_image(
        file_data, file_type, filename, image_size, lang=g.api_user["language"]
    )
    if not valid:
        return api_error(
            400, "invalid_request_error", "invalid_input", validation_error or _("Unsupported image format")
        )

    form_payload = {
        "metadata": request.form.get("metadata"),
        "user": request.form.get("user"),
    }
    if form_payload["metadata"]:
        try:
            form_payload["metadata"] = json.loads(form_payload["metadata"])
        except (TypeError, json.JSONDecodeError):
            return api_error(400, "invalid_request_error", "invalid_metadata", _("'metadata' must be a JSON object"))
    if form_payload["user"] is None:
        form_payload.pop("user")
    session_id = _resolve_media_session(form_payload)
    if not isinstance(session_id, str):
        return session_id
    task_id, _queue_info = enqueue_image_edit(
        g.api_user,
        session_id,
        prompt.strip(),
        file_data,
        file_type,
        filename,
        g.api_user.get("response_style", "neutral"),
    )
    register_api_task(g.api_user, task_id, session_id, "/v1/images/edits")
    response, status = _async_task_response(task_id, f"/v1/flai/tasks/{task_id}")
    response.headers["Location"] = f"/v1/flai/tasks/{task_id}"
    return response, status


@bp.route("/videos", methods=["POST"])
@api_token_required
@limiter.limit(_api_rate_limit, key_func=_api_rate_key)
def video_generations():
    """Queue text-to-video generation with options supported by VideoModule."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return api_error(400, "invalid_request_error", "invalid_body", _("Request body must be a JSON object"))
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return api_error(400, "invalid_request_error", "invalid_input", _("'prompt' must be a non-empty string"))
    if payload.get("model") is not None and not isinstance(payload.get("model"), str):
        return api_error(400, "invalid_request_error", "invalid_model", _("'model' must be a string"))
    option_ranges = {
        "width": (256, 1024),
        "height": (256, 1024),
        "num_frames": (9, 240),
        "frame_rate": (6, 24),
        "seed": (-1, 2**31 - 1),
    }
    options: dict[str, int] = {}
    for key, value in payload.items():
        if key in ("prompt", "model", "user", "metadata"):
            continue
        bounds = option_ranges.get(key)
        if bounds is None or isinstance(value, bool) or not isinstance(value, int):
            return api_error(
                400, "invalid_request_error", "invalid_video_options", _("Unsupported video generation options")
            )
        if not bounds[0] <= value <= bounds[1]:
            return api_error(
                400, "invalid_request_error", "invalid_video_options", _("Video generation option is out of range")
            )
        if key in ("width", "height") and value % 32:
            return api_error(
                400, "invalid_request_error", "invalid_video_options", _("Video dimensions must be multiples of 32")
            )
        if key == "frame_rate" and value not in (6, 12, 16, 24):
            return api_error(400, "invalid_request_error", "invalid_video_options", _("Unsupported video frame rate"))
        options[key] = value

    width = options.get("width", 768)
    height = options.get("height", 512)
    num_frames = options.get("num_frames", 240)
    frame_rate = options.get("frame_rate", 24)
    if width * height * num_frames > 768 * 512 * 240 or num_frames > frame_rate * 10:
        return api_error(
            400,
            "invalid_request_error",
            "invalid_video_options",
            _("Video output exceeds the supported size or duration"),
        )

    video_module = current_app.modules.get("video")
    multimodal = current_app.modules.get("multimodal")
    if video_module is None or multimodal is None:
        return api_error(503, "service_unavailable", "video_unavailable", _("Video generation module unavailable"))
    video_module.check_availability()
    if not video_module.available or not multimodal.available:
        return api_error(503, "service_unavailable", "video_unavailable", _("Video generation module unavailable"))

    session_id = _resolve_media_session(payload)
    if not isinstance(session_id, str):
        return session_id
    task_id, _queue_info = enqueue_video_generation(g.api_user, session_id, prompt.strip(), options)
    register_api_task(g.api_user, task_id, session_id, "/v1/videos")
    response, status = _async_task_response(task_id, f"/v1/videos/{task_id}")
    response.headers["Location"] = f"/v1/videos/{task_id}"
    return response, status


@bp.route("/videos/<task_id>", methods=["GET"])
@api_token_required
@limiter.limit(_api_rate_limit, key_func=_api_rate_key)
def get_video_task(task_id: str):
    """Return a video-shaped view of an owner-scoped task."""
    record = get_api_task_owner(task_id)
    if not record or record.get("login") != g.api_user["login"] or record.get("endpoint") != "/v1/videos":
        return api_error(404, "invalid_request_error", "task_not_found", _("Task not found"))
    task = _serialize_api_task(task_id, record, cast(Any, current_app).request_queue.check_result(task_id))
    response = dict(task)
    response["object"] = "video"
    response["task_status_url"] = f"/v1/flai/tasks/{response['id']}"
    if response.get("content_url"):
        response["url"] = response["content_url"]
    return jsonify(response)


@bp.route("/chat/completions", methods=["POST"])
@api_token_required
@limiter.limit(_api_rate_limit, key_func=_api_rate_key)
def chat_completions():
    """Answer a chat request through the normal router and GPU queue.

    The request is persisted and queued exactly like a web message, so the LLM
    router — not this route — decides which subsystem handles the prompt. The
    ``model`` field is accepted and ignored: FLAI exposes capabilities, not
    interchangeable weights.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return api_error(400, "invalid_request_error", "invalid_body", _("Request body must be a JSON object"))

    try:
        text, images = normalize_chat_messages(payload.get("messages"))
    except ApiImageRejectedError as exc:
        return api_error(400, "invalid_request_error", "invalid_request", exc.localized(_))

    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    client_user = payload.get("user")
    if client_user is not None and not isinstance(client_user, str):
        return api_error(400, "invalid_request_error", "invalid_user", _("'user' must be a string"))
    requested_session_id = metadata.get("session_id")
    if requested_session_id is not None and not isinstance(requested_session_id, str):
        return api_error(
            400, "invalid_request_error", "invalid_session_id", _("'metadata.session_id' must be a string")
        )

    stream = payload.get("stream", False)
    if not isinstance(stream, bool):
        return api_error(400, "invalid_request_error", "invalid_stream", _("'stream' must be a boolean"))
    stream_options = payload.get("stream_options") if isinstance(payload.get("stream_options"), dict) else {}
    include_usage = bool(stream_options.get("include_usage", False))

    try:
        session_id = resolve_api_session(g.api_user, requested_session_id, client_user)
    except ApiSessionNotFoundError:
        return api_error(404, "invalid_request_error", "session_not_found", _("Chat session not found"))

    timeout_s = current_app.config.get("API_SYNC_MAX_WAIT", 600)
    if stream:
        task_id, _queue_info = enqueue_chat(g.api_user, session_id, text, images)
        register_api_task(g.api_user, task_id, session_id, "/v1/chat/completions")
        generator = stream_chat(
            g.api_user["login"],
            task_id,
            session_id,
            include_usage=include_usage,
            timeout_s=timeout_s,
            translate=_,
        )
        return Response(
            stream_with_context(generator),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    shed = _acquire_wait_slot()
    if shed is not None:
        return shed
    try:
        task_id, _queue_info = enqueue_chat(g.api_user, session_id, text, images)
        register_api_task(g.api_user, task_id, session_id, "/v1/chat/completions")
        result = wait_for_result(g.api_user["login"], task_id, timeout_s)
    except ApiTaskTimeoutError:
        message = _("Task %s is still running and was not cancelled") % task_id
        return api_error(408, "server_error", "task_timeout", message)
    except ApiTaskError as exc:
        return api_error(500, "server_error", "task_failed", exc.localized(_))
    finally:
        _release_wait_slot()

    return jsonify(serialize_chat_completion(result, task_id, session_id))


def _collect_embedding_input(payload: dict[str, Any]) -> list[str] | None:
    """Normalize the OpenAI ``input`` field into a list of non-empty strings."""
    raw = payload.get("input")
    if isinstance(raw, str):
        texts = [raw]
    elif isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        texts = raw
    else:
        return None
    if not texts or not all(text.strip() for text in texts):
        return None
    return texts


@bp.route("/embeddings", methods=["POST"])
@api_token_required
@limiter.limit(_api_rate_limit, key_func=_api_rate_key)
def embeddings():
    """Embed one string or a batch of strings with the RAG embedding model.

    ``model``, ``dimensions`` and ``user`` are accepted and ignored: FLAI has a
    single embedding model. The request runs on the fast worker but still holds
    the GPU lock, so it queues like any other model call.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return api_error(400, "invalid_request_error", "invalid_body", _("Request body must be a JSON object"))

    texts = _collect_embedding_input(payload)
    if texts is None:
        return api_error(
            400,
            "invalid_request_error",
            "invalid_input",
            _("'input' must be a non-empty string or an array of non-empty strings"),
        )

    encoding_format = payload.get("encoding_format", "float")
    if encoding_format not in ("float", "base64"):
        return api_error(
            400,
            "invalid_request_error",
            "invalid_encoding_format",
            _("'encoding_format' must be 'float' or 'base64'"),
        )

    timeout_s = current_app.config.get("API_SYNC_MAX_WAIT", 600)
    shed = _acquire_wait_slot()
    if shed is not None:
        return shed
    try:
        task_id = enqueue_embeddings(g.api_user, texts)
        register_api_task(g.api_user, task_id, "api", "/v1/embeddings")
        result = wait_for_result(g.api_user["login"], task_id, timeout_s)
    except ApiTaskTimeoutError:
        message = _("Task %s is still running and was not cancelled") % task_id
        return api_error(408, "server_error", "task_timeout", message)
    except ApiTaskError as exc:
        return api_error(502, "server_error", "task_failed", exc.localized(_))
    finally:
        _release_wait_slot()

    try:
        body = serialize_embeddings(result, encoding_format)
    except ApiTaskError as exc:
        return api_error(502, "server_error", "task_failed", exc.localized(_))
    return jsonify(body)


@bp.route("/audio/speech", methods=["POST"])
@api_token_required
@limiter.limit(_api_rate_limit, key_func=_api_rate_key)
def audio_speech():
    """Synthesize speech from text via the configured TTS service.

    ``model`` and ``speed`` are accepted and ignored. ``voice`` is passed
    through to the Kokoro backend and ignored by Piper. ``language`` and
    ``gender`` override the key owner's defaults for this call.
    ``response_format`` defaults to the configured backend's native format
    (``wav`` for Kokoro, ``mp3`` for Piper). Other formats are rejected rather
    than returning mislabeled audio.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return api_error(400, "invalid_request_error", "invalid_body", _("Request body must be a JSON object"))

    text = payload.get("input")
    if not isinstance(text, str) or not text.strip():
        return api_error(400, "invalid_request_error", "invalid_input", _("'input' must be a non-empty string"))

    voice = payload.get("voice")
    if voice is not None and not isinstance(voice, str):
        return api_error(400, "invalid_request_error", "invalid_request", _("'voice' must be a string"))

    language = payload.get("language", g.api_user.get("language", "ru"))
    if language not in ("ru", "en"):
        return api_error(400, "invalid_request_error", "invalid_language", _("Invalid language"))

    gender = payload.get("gender", g.api_user.get("voice_gender", "male"))
    if gender not in ("male", "female"):
        return api_error(400, "invalid_request_error", "invalid_voice_gender", _("Invalid voice gender"))

    tts_module = current_app.modules.get("tts")
    if tts_module is None or not tts_module.available:
        return api_error(503, "service_unavailable", "tts_unavailable", _("TTS service unavailable"))

    backend_formats = {"kokoro": ("wav", {"audio/wav", "audio/x-wav", "audio/wave"}), "piper": ("mp3", {"audio/mpeg"})}
    backend_format = backend_formats.get(tts_module.backend)
    if backend_format is None:
        return api_error(503, "service_unavailable", "tts_unavailable", _("TTS service unavailable"))
    native_format, accepted_mimes = backend_format

    response_format = payload.get("response_format", native_format)
    if response_format != native_format:
        return api_error(
            400,
            "invalid_request_error",
            "invalid_response_format",
            _("The configured TTS backend produces {native_format}; requested: {requested_format}").format(
                native_format=native_format, requested_format=response_format
            ),
        )

    try:
        audio_bytes, mime_type = tts_module.synthesize(text, language, gender, voice=voice)
    except Exception:
        current_app.logger.exception("API speech synthesis failed")
        return api_error(500, "server_error", "synthesis_failed", _("TTS synthesis failed"))
    if audio_bytes is None or not mime_type:
        return api_error(500, "server_error", "synthesis_failed", _("TTS synthesis failed"))

    mime = mime_type.split(";", 1)[0].strip().lower()
    if mime not in accepted_mimes:
        return api_error(500, "server_error", "synthesis_failed", _("TTS synthesis failed"))
    ext = native_format

    return Response(
        audio_bytes,
        mimetype=mime_type,
        headers={"Content-Disposition": f'attachment; filename="speech.{ext}"'},
    )


@bp.route("/audio/transcriptions", methods=["POST"])
@api_token_required
@limiter.limit(_api_rate_limit, key_func=_api_rate_key)
def audio_transcriptions():
    """Transcribe an uploaded audio file via the Whisper service.

    The request is multipart/form-data with a required ``file`` field.
    ``model`` is accepted and ignored. ``response_format`` must be ``json`` or
    ``text`` — FLAI's Whisper wrapper returns plain text with no timestamps,
    so ``srt``, ``vtt`` and ``verbose_json`` are rejected. An optional
    ``language`` form field overrides the key owner's language.
    """
    if "file" not in request.files:
        return api_error(400, "invalid_request_error", "invalid_request", _("Missing 'file' field"))

    f = request.files["file"]
    if f.filename == "":
        return api_error(400, "invalid_request_error", "invalid_request", _("No file selected"))

    response_format = request.form.get("response_format", "json")
    if response_format not in ("json", "text"):
        return api_error(
            400,
            "invalid_request_error",
            "invalid_response_format",
            _("'response_format' must be 'json' or 'text'; timestamps are not available"),
        )

    audio_module = current_app.modules.get("audio")
    if audio_module is None or not audio_module.available:
        return api_error(503, "service_unavailable", "audio_unavailable", _("Audio service unavailable"))
    if not audio_module.is_audio_file(f.mimetype, f.filename):
        return api_error(400, "invalid_request_error", "invalid_input", _("Unsupported audio format"))

    file_bytes = f.read()
    if not file_bytes:
        return api_error(400, "invalid_request_error", "invalid_input", _("Uploaded file is empty"))

    file_data = b64encode(file_bytes).decode("ascii")
    language = request.form.get("language", g.api_user.get("language", "ru"))
    if language not in ("ru", "en"):
        return api_error(400, "invalid_request_error", "invalid_language", _("Invalid language"))
    timeout_s = current_app.config.get("API_SYNC_MAX_WAIT", 600)
    shed = _acquire_wait_slot()
    if shed is not None:
        return shed
    try:
        task_id = enqueue_transcription(g.api_user, file_data, f.mimetype, f.filename, language)
        register_api_task(g.api_user, task_id, "api", "/v1/audio/transcriptions")
        result = wait_for_result(g.api_user["login"], task_id, timeout_s)
    except ApiTaskTimeoutError:
        message = _("Task %s is still running and was not cancelled") % task_id
        return api_error(408, "server_error", "task_timeout", message)
    except ApiTaskError as exc:
        return api_error(502, "server_error", "task_failed", exc.localized(_))
    finally:
        _release_wait_slot()

    text = result.get("text")
    if not text:
        return api_error(502, "server_error", "task_failed", _("Transcription produced no text"))

    if response_format == "text":
        return Response(text, mimetype="text/plain; charset=utf-8")
    return jsonify({"text": text})


# ---------------------------------------------------------------------------
# Files (OpenAI-compatible) and FLAI document aliases
# ---------------------------------------------------------------------------


def _serialize_api_file(doc: dict[str, Any]) -> dict[str, Any]:
    created_at = doc.get("uploaded_at")
    if created_at is not None and hasattr(created_at, "timestamp"):
        created_at = created_at.timestamp()
    else:
        try:
            created_at = datetime.strptime(str(created_at)[:19], "%Y-%m-%d %H:%M:%S").timestamp()
        except (TypeError, ValueError):
            created_at = 0.0
    return {
        "id": doc["id"],
        "object": "file",
        "filename": doc["filename"],
        "bytes": doc.get("file_size") or 0,
        "created_at": created_at,
        "purpose": "assistants",
        "index_status": doc.get("index_status"),
    }


def _owned_document_or_404(file_id: str) -> tuple[dict[str, Any] | None, tuple[Any, int] | None]:
    doc = db.get_document(file_id, g.api_user["login"])
    if not doc:
        return None, api_error(404, "invalid_request_error", "file_not_found", _("Document not found"))
    return doc, None


def _document_disk_path(doc: dict[str, Any]) -> tuple[str | None, tuple[Any, int] | None]:
    """Resolve a stored document path under DOCUMENTS_FOLDER with containment."""
    documents_folder = current_app.config["DOCUMENTS_FOLDER"]
    file_path = os.path.join(documents_folder, doc["file_path"])
    real_file_path = os.path.realpath(file_path)
    real_documents_folder = os.path.realpath(documents_folder)
    if not real_file_path.startswith(real_documents_folder + os.sep) and real_file_path != real_documents_folder:
        current_app.logger.warning("API document path traversal attempt blocked: %s", doc["file_path"])
        return None, api_error(404, "invalid_request_error", "file_not_found", _("Document not found"))
    if not os.path.isfile(real_file_path):
        return None, api_error(404, "invalid_request_error", "file_not_found", _("Document not found"))
    return real_file_path, None


def _api_list_documents():
    docs = db.get_user_documents(g.api_user["login"])
    return jsonify({"object": "list", "data": [_serialize_api_file(doc) for doc in docs]})


def _api_file_metadata(file_id: str):
    doc, err = _owned_document_or_404(file_id)
    if err is not None or doc is None:
        return err
    return jsonify(_serialize_api_file(doc))


def _api_file_content(file_id: str):
    doc, err = _owned_document_or_404(file_id)
    if err is not None or doc is None:
        return err
    file_path, err = _document_disk_path(doc)
    if err is not None or file_path is None:
        return err
    mimetype, _ = mimetypes.guess_type(file_path)
    return send_file(
        file_path,
        mimetype=mimetype or "application/octet-stream",
        as_attachment=True,
        download_name=doc["filename"],
    )


def _api_file_delete(file_id: str):
    doc, err = _owned_document_or_404(file_id)
    if err is not None or doc is None:
        return err
    login = g.api_user["login"]
    rag = cast(Any, current_app).modules.get("rag")
    if rag and rag.available:
        try:
            rag.delete_document(doc["id"], login)
        except Exception:
            current_app.logger.exception("Failed to delete document from index")
    file_path, err = _document_disk_path(doc)
    if err is not None or file_path is None:
        return err
    try:
        os.remove(file_path)
    except OSError:
        current_app.logger.exception("Failed to delete document file %s", file_path)
    db.delete_document(doc["id"], login)
    return jsonify({"id": doc["id"], "object": "file", "deleted": True})


def _api_upload_document():
    if "file" not in request.files:
        return api_error(400, "invalid_request_error", "invalid_request", _("Missing 'file' field"))
    upload = request.files["file"]
    if upload.filename == "":
        return api_error(400, "invalid_request_error", "invalid_request", _("No file selected"))

    file_content = upload.read()
    is_valid, validation_error = validate_file(io.BytesIO(file_content), upload.filename)
    if not is_valid:
        return api_error(400, "invalid_request_error", "invalid_input", validation_error or _("Unsupported file type"))

    max_size_mb = current_app.config["MAX_DOCUMENT_SIZE_MB"]
    if len(file_content) > max_size_mb * 1024 * 1024:
        return api_error(413, "invalid_request_error", "request_too_large", _("Uploaded file is too large"))

    quota_error = check_document_quota(g.api_user["login"])
    if quota_error:
        return api_error(413, "invalid_request_error", "document_quota_exceeded", quota_error)

    login = g.api_user["login"]
    doc_id = str(uuid.uuid4())
    filename = upload.filename
    is_image = False

    image_mime = magic.from_buffer(file_content[:2048], mime=True) if file_content[:2048] else None
    if image_mime and image_mime.startswith("image/"):
        is_image = True
        max_image_size = current_app.config.get("MAX_IMAGE_SIZE", 1536)
        file_b64 = b64encode(file_content).decode("ascii")
        file_b64, _ftype, _img_orig_name, _resized, _od, _nd = resize_image_if_needed(
            file_b64, image_mime, filename, max_image_size
        )
        file_b64, _ftype, img_new_name, _converted = convert_to_supported_format_if_needed(
            file_b64, _ftype, _ftype and filename or filename
        )
        file_content = b64decode(file_b64)
        filename = img_new_name

    documents_folder = current_app.config["DOCUMENTS_FOLDER"]
    user_folder = os.path.join(documents_folder, login)
    os.makedirs(user_folder, exist_ok=True)
    safe_ext = os.path.splitext(filename)[1].lower()
    safe_filename = f"{doc_id}{safe_ext}"
    file_path = os.path.join(user_folder, safe_filename)

    real_file_path = os.path.realpath(file_path)
    real_user_folder = os.path.realpath(user_folder)
    if not real_file_path.startswith(real_user_folder + os.sep):
        return api_error(400, "invalid_request_error", "invalid_file_path", _("Invalid file path"))

    with open(real_file_path, "wb") as handle:
        handle.write(file_content)

    relative_path = os.path.join(login, safe_filename)
    db.save_document(login, doc_id, filename, len(file_content), safe_ext, relative_path)
    db.update_document_index_status(doc_id, db.INDEX_STATUS_PENDING)

    task_type = "describe_document_image" if is_image else "index_document"
    current_app.request_queue.add_request(
        user_id=login,
        session_id="",  # Document indexing doesn't belong to a chat session
        request_data={"type": task_type, "doc_id": doc_id, "file_path": real_file_path},
        user_class=g.api_user.get("service_class", 2),
        lang=g.api_user.get("language", "ru"),
    )

    doc = db.get_document(doc_id, login)
    if doc is None:  # pragma: no cover - saved above
        doc = {
            "id": doc_id,
            "filename": filename,
            "file_size": len(file_content),
            "uploaded_at": None,
            "index_status": db.INDEX_STATUS_PENDING,
        }
    return jsonify(_serialize_api_file(doc))


@bp.route("/files", methods=["GET", "POST"])
@bp.route("/flai/documents", methods=["GET", "POST"])
@api_token_required
@limiter.limit(_api_rate_limit, key_func=_api_rate_key)
def api_files_collection():
    """List the caller's documents or upload a new one."""
    if request.method == "POST":
        return _api_upload_document()
    return _api_list_documents()


@bp.route("/files/<file_id>", methods=["GET", "DELETE"])
@bp.route("/flai/documents/<file_id>", methods=["GET", "DELETE"])
@api_token_required
@limiter.limit(_api_rate_limit, key_func=_api_rate_key)
def api_files_item(file_id: str):
    """Fetch one owner-owned document record or delete it."""
    if request.method == "DELETE":
        return _api_file_delete(file_id)
    return _api_file_metadata(file_id)


@bp.route("/files/<file_id>/content", methods=["GET"])
@bp.route("/flai/documents/<file_id>/content", methods=["GET"])
@api_token_required
@limiter.limit(_api_rate_limit, key_func=_api_rate_key)
def api_files_content(file_id: str):
    """Download an owner-owned document after a realpath containment check."""
    return _api_file_content(file_id)


# ---------------------------------------------------------------------------
# RLM deep analysis
# ---------------------------------------------------------------------------


@bp.route("/flai/rlm", methods=["POST"])
@api_token_required
@limiter.limit(_api_rate_limit, key_func=_api_rate_key)
def api_rlm_analysis():
    """Run a deep-analysis (RLM) task over caller-owned documents."""
    if not current_app.config.get("RLM_ENABLED", True):
        return api_error(403, "invalid_request_error", "rlm_disabled", _("Deep analysis is disabled"))

    login = g.api_user["login"]
    if request.content_type and "multipart/form-data" in request.content_type:
        session_id = request.form.get("session_id")
        try:
            doc_ids = json.loads(request.form.get("doc_ids") or "[]")
        except (ValueError, TypeError):
            doc_ids = []
        question = (request.form.get("question") or "").strip()
        file_data = file_type = file_name = None
        if "file" in request.files:
            upload = request.files["file"]
            if upload and upload.filename:
                file_data = b64encode(upload.read()).decode("utf-8")
                file_type = upload.content_type or "application/octet-stream"
                file_name = upload.filename
    else:
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return api_error(400, "invalid_request_error", "invalid_request", _("Request body must be a JSON object"))
        session_id = payload.get("session_id")
        doc_ids = payload.get("doc_ids") or []
        question = payload.get("question")
        if question is not None and not isinstance(question, str):
            return api_error(400, "invalid_request_error", "invalid_request", _("'question' must be a string"))
        question = (question or "").strip()
        file_data = file_type = file_name = None

    if not session_id or not isinstance(session_id, str) or not question:
        return api_error(
            400,
            "invalid_request_error",
            "invalid_request",
            _("Select at least one document and enter a question"),
        )
    if not isinstance(doc_ids, list) or any(not isinstance(doc_id, str) for doc_id in doc_ids):
        return api_error(400, "invalid_request_error", "invalid_request", _("'doc_ids' must be a list of strings"))
    if not doc_ids and not file_data:
        return api_error(
            400,
            "invalid_request_error",
            "invalid_request",
            _("Select at least one document and enter a question"),
        )

    if not validate_session_ownership(session_id, login):
        return api_error(404, "invalid_request_error", "session_not_found", _("Session not found"))

    owned = {doc["id"] for doc in (db.get_user_documents(login) or [])}
    owned_doc_ids = [doc_id for doc_id in doc_ids if doc_id in owned]
    if len(owned_doc_ids) != len(doc_ids):
        return api_error(404, "invalid_request_error", "document_not_found", _("Document not found"))

    file_path = None
    if file_data:
        if file_size := len(b64decode(file_data)):
            quota_error = check_upload_quota(login, file_size)
            if quota_error:
                return api_error(413, "invalid_request_error", "upload_quota_exceeded", quota_error)
        file_data, file_type, file_name, _resized, _orig_dims, _new_dims = resize_image_if_needed(
            file_data, file_type, file_name, current_app.config.get("MAX_IMAGE_SIZE", 1536)
        )
        file_path = save_uploaded_file(
            file_data=file_data,
            filename=file_name,
            session_id=session_id,
            upload_folder=current_app.config["UPLOAD_FOLDER"],
            user_id=login,
        )

    user_content: list[dict[str, str]] = []
    if question:
        user_content.append({"type": "text", "text": question})
    if file_data:
        user_content.append({"type": "image", "file_data": file_data, "file_type": file_type, "file_name": file_name})
    user_content_json = json.dumps(user_content, ensure_ascii=False)
    user_message_id = db.save_message(session_id, "user", user_content_json, file_data, file_type, file_name, file_path)

    db.update_session_visit(login, session_id)

    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM messages WHERE session_id = %s AND role = 'user'", (session_id,))
        if cursor.fetchone()["cnt"] == 1:
            db.update_session_title(session_id, question, file_name)

    task_id, info = current_app.request_queue.add_rlm_task(
        login,
        session_id,
        owned_doc_ids,
        question,
        user_class=g.api_user["service_class"],
        lang=g.api_user["language"],
        image_data=file_data,
        image_type=file_type,
        image_name=file_name,
    )
    register_api_task(g.api_user, task_id, session_id, "/v1/flai/rlm")

    return jsonify(
        {
            "task_id": task_id,
            "position": info["position"],
            "user_message_id": user_message_id,
        }
    ), 202


# ---------------------------------------------------------------------------
# Sessions and history
# ---------------------------------------------------------------------------


def _serialize_api_session(session: dict[str, Any]) -> dict[str, Any]:
    created_at = session.get("created_at")
    if created_at is not None and hasattr(created_at, "isoformat"):
        created_at = created_at.isoformat(sep=" ")
    return {"id": session["id"], "title": session.get("title") or "", "created_at": created_at}


@bp.route("/flai/sessions", methods=["GET", "POST"])
@api_token_required
@limiter.limit(_api_rate_limit, key_func=_api_rate_key)
def api_sessions_collection():
    """List or create sessions owned by the API-key owner."""
    login = g.api_user["login"]
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return api_error(400, "invalid_request_error", "invalid_request", _("Request body must be a JSON object"))
        title = payload.get("title")
        if title is not None and not isinstance(title, str):
            return api_error(400, "invalid_request_error", "invalid_request", _("'title' must be a string"))
        if title:
            title = title.strip()[:200]
        else:
            from flask_babel import force_locale

            with force_locale(g.api_user["language"]):
                title = _("New session")
        session_id = db.create_session(login, title=title, lang=g.api_user["language"])
        created_at = ""
        for session in db.get_user_sessions(login):
            if session["id"] == session_id:
                created_at = _serialize_api_session(session)["created_at"] or ""
                break
        return jsonify({"id": session_id, "title": title, "created_at": created_at})
    sessions = db.get_user_sessions(login)
    return jsonify({"object": "list", "data": [_serialize_api_session(s) for s in sessions]})


@bp.route("/flai/sessions/<session_id>/messages", methods=["GET"])
@api_token_required
@limiter.limit(_api_rate_limit, key_func=_api_rate_key)
def api_session_messages(session_id: str):
    """Read owner-scoped message history with pagination."""
    if not validate_session_ownership(session_id, g.api_user["login"]):
        return api_error(404, "invalid_request_error", "session_not_found", _("Session not found"))
    try:
        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        return api_error(400, "invalid_request_error", "invalid_request", _("'limit' and 'offset' must be integers"))
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    since = request.args.get("since")
    messages = db.get_session_messages(session_id, since=since, limit=limit, offset=offset)
    return jsonify({"messages": messages, "limit": limit, "offset": offset, "has_more": len(messages) >= limit})
