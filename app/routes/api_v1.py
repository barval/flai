"""OpenAI-compatible public API foundation."""

import time
from base64 import b64encode
from collections.abc import Callable
from functools import wraps
from threading import BoundedSemaphore
from typing import Any

from flask import Blueprint, Response, current_app, g, jsonify, request, stream_with_context
from flask_babel import gettext as _
from flask_limiter.errors import RateLimitExceeded
from werkzeug.exceptions import MethodNotAllowed, RequestEntityTooLarge

from app import limiter
from app.api_bridge import (
    ApiImageRejectedError,
    ApiSessionNotFoundError,
    ApiTaskError,
    ApiTaskTimeoutError,
    enqueue_chat,
    enqueue_embeddings,
    enqueue_transcription,
    normalize_chat_messages,
    resolve_api_session,
    serialize_chat_completion,
    serialize_embeddings,
    stream_chat,
    wait_for_result,
)
from app.api_tokens import touch_api_token, verify_api_token

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
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
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
                "images": False,
                "videos": False,
                "documents": False,
                "rlm": False,
                "tools": False,
                "response_format_json_schema": False,
            },
        }
    )


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
