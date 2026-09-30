"""Session-authenticated key management for the web UI."""

from flask import Blueprint, jsonify, request, session
from flask_babel import gettext as _

from app.api_tokens import create_api_token, list_api_tokens, revoke_api_token

bp = Blueprint("api_keys", __name__, url_prefix="/api-keys")


def _serialize_token(record: dict) -> dict:
    return {
        "id": record["id"],
        "name": record["name"],
        "token_prefix": record["token_prefix"],
        "created_at": record["created_at"].isoformat() if record.get("created_at") else None,
        "last_used_at": record["last_used_at"].isoformat() if record.get("last_used_at") else None,
        "revoked_at": record["revoked_at"].isoformat() if record.get("revoked_at") else None,
    }


@bp.route("/tokens", methods=["GET"])
def get_tokens():
    """List the authenticated user's API keys without secret hashes."""
    login = session.get("login")
    if not login:
        return jsonify({"error": "⚠️ " + _("Not authorized")}), 401
    return jsonify({"tokens": [_serialize_token(row) for row in list_api_tokens(login)]})


@bp.route("/tokens", methods=["POST"])
def create_token():
    """Create an API key and return its plaintext only in this response."""
    login = session.get("login")
    if not login:
        return jsonify({"error": "⚠️ " + _("Not authorized")}), 401
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name", "api")).strip()[:64] or "api"
    plaintext, record = create_api_token(login, name)
    safe_record = _serialize_token({**record, "last_used_at": None, "revoked_at": None})
    return jsonify({**safe_record, "token": plaintext}), 201


@bp.route("/tokens/<int:token_id>/revoke", methods=["POST"])
def revoke_token(token_id: int):
    """Revoke a key owned by the authenticated user."""
    login = session.get("login")
    if not login:
        return jsonify({"error": "⚠️ " + _("Not authorized")}), 401
    if not revoke_api_token(login, token_id):
        return jsonify({"error": "⚠️ " + _("api_key_not_found")}), 404
    return jsonify({"status": "revoked"})
