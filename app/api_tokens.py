"""Per-user API token lifecycle for the public API."""

import hashlib
import secrets
from typing import Any

from app.database import get_db
from app.userdb import get_user_by_login

TOKEN_PREFIX = "flai-"
TOKEN_SECRET_BYTES = 32
DISPLAY_PREFIX_CHARS = len(TOKEN_PREFIX) + 6


def hash_token(token: str) -> str:
    """Return the SHA-256 digest used to store a high-entropy API token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_api_token(login: str, name: str = "api") -> tuple[str, dict[str, Any]]:
    """Create a token and return its plaintext once alongside safe metadata."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_SECRET_BYTES)
    token_prefix = token[:DISPLAY_PREFIX_CHARS]
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO api_tokens (login, name, token_hash, token_prefix)
            VALUES (%s, %s, %s, %s)
            RETURNING id, created_at
            """,
            (login, name, hash_token(token), token_prefix),
        )
        row = cursor.fetchone()
    record = {
        "id": row["id"] if row else None,
        "login": login,
        "name": name,
        "token_prefix": token_prefix,
        "created_at": row["created_at"] if row else None,
    }
    return token, record


def verify_api_token(token: str) -> dict[str, Any] | None:
    """Resolve an active token to its owner or return None."""
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, login, last_used_at
            FROM api_tokens
            WHERE token_hash = %s AND revoked_at IS NULL
            """,
            (hash_token(token),),
        )
        row = cursor.fetchone()
    if not row:
        return None
    user = get_user_by_login(row["login"])
    if not user or not user.get("is_active", True):
        return None
    return {"token_id": row["id"], "last_used_at": row.get("last_used_at"), "user": user}


def list_api_tokens(login: str) -> list[dict[str, Any]]:
    """List the caller's token metadata without hashes or plaintext secrets."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, name, token_prefix, created_at, last_used_at, revoked_at
            FROM api_tokens
            WHERE login = %s
            ORDER BY created_at DESC, id DESC
            """,
            (login,),
        )
        rows = cursor.fetchall()
    return [dict(row) for row in (rows or [])]


def revoke_api_token(login: str, token_id: int) -> bool:
    """Revoke a token only when it belongs to the specified user."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE api_tokens
            SET revoked_at = CURRENT_TIMESTAMP
            WHERE id = %s AND login = %s AND revoked_at IS NULL
            """,
            (token_id, login),
        )
        return bool(cursor.rowcount and cursor.rowcount > 0)


def touch_api_token(token_id: int) -> None:
    """Update last-used time no more than once per five minutes."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE api_tokens
            SET last_used_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND (last_used_at IS NULL OR last_used_at < CURRENT_TIMESTAMP - INTERVAL '5 minutes')
            """,
            (token_id,),
        )
