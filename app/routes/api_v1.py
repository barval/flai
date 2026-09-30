"""OpenAI-compatible public API foundation."""

from collections.abc import Callable
from functools import wraps
from typing import Any

from flask import Blueprint, current_app, g, jsonify, request
from flask_babel import gettext as _

from app.api_tokens import touch_api_token, verify_api_token

bp = Blueprint("api_v1", __name__, url_prefix="/v1")

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
                "chat_completions": False,
                "streaming": False,
                "embeddings": False,
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
