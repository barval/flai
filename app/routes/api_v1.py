"""OpenAI-compatible public API foundation."""

from collections.abc import Callable
from functools import wraps
from typing import Any

from flask import Blueprint, Response, current_app, g, jsonify, request, stream_with_context
from flask_babel import gettext as _
from flask_limiter.errors import RateLimitExceeded
from werkzeug.exceptions import MethodNotAllowed

from app import limiter
from app.api_bridge import (
    ApiImageRejectedError,
    ApiSessionNotFoundError,
    ApiTaskError,
    ApiTaskTimeoutError,
    enqueue_chat,
    enqueue_embeddings,
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
) -> tuple[Any, int]:
    """Return the common OpenAI-shaped API error body."""
    localized_message = message if message.startswith("⚠️ ") else f"⚠️ {message}"
    return (
        jsonify({"error": {"message": localized_message, "type": err_type, "param": param, "code": code}}),
        status,
    )


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


@bp.app_errorhandler(RateLimitExceeded)
def rate_limited(error: Any) -> Any:
    """Report a spent budget in the OpenAI error envelope.

    Registered app-wide because a blueprint handler only covers its own
    blueprint; the auth blueprint keeps its own HTML 429 page.
    """
    if not request.path.startswith(API_PREFIX):
        return RateLimitExceeded(error.description).get_response(request.environ)
    return api_error(429, "rate_limit_error", "rate_limit_exceeded", _("api_error_rate_limited"))


@bp.app_errorhandler(405)
def method_not_allowed(error: Any) -> Any:
    """Keep a wrong HTTP method inside the OpenAI error envelope.

    Flask dispatches routing errors (405, unknown path) to app-level handlers
    only, so this is registered app-wide and falls through for every path
    outside the API prefix.
    """
    if not request.path.startswith(API_PREFIX):
        valid = getattr(error, "valid_methods", None)
        return MethodNotAllowed(valid_methods=valid).get_response(request.environ)
    return api_error(405, "invalid_request_error", "method_not_allowed", _("Method not allowed"))


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
            "rate_limit": current_app.config.get("API_RATE_LIMIT", "60 per minute;1000 per hour"),
            "capabilities": {
                "chat_completions": True,
                "streaming": True,
                "embeddings": True,
                "audio_speech": False,
                "audio_transcriptions": False,
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

    task_id, _queue_info = enqueue_chat(g.api_user, session_id, text, images)

    timeout_s = current_app.config.get("API_SYNC_MAX_WAIT", 600)
    if stream:
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

    try:
        result = wait_for_result(g.api_user["login"], task_id, timeout_s)
    except ApiTaskTimeoutError:
        message = _("Task %s is still running and was not cancelled") % task_id
        return api_error(408, "server_error", "task_timeout", message)
    except ApiTaskError as exc:
        return api_error(500, "server_error", "task_failed", exc.localized(_))

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

    task_id = enqueue_embeddings(g.api_user, texts)

    timeout_s = current_app.config.get("API_SYNC_MAX_WAIT", 600)
    try:
        result = wait_for_result(g.api_user["login"], task_id, timeout_s)
    except ApiTaskTimeoutError:
        message = _("Task %s is still running and was not cancelled") % task_id
        return api_error(408, "server_error", "task_timeout", message)
    except ApiTaskError as exc:
        return api_error(502, "server_error", "task_failed", exc.localized(_))

    try:
        body = serialize_embeddings(result, encoding_format)
    except ApiTaskError as exc:
        return api_error(502, "server_error", "task_failed", exc.localized(_))
    return jsonify(body)
