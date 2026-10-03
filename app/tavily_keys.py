"""Per-user Tavily API key storage.

The key must be presented to Tavily on every search, so it is stored as-is
(never hashed) and is never returned to the browser — callers only receive a
mask. Deleting the user removes the key with the row.
"""

from app.database import get_db

KEY_PREFIX = "tvly-"
KEY_MAX_LENGTH = 128
MASK_TAIL_CHARS = 4

STATUS_NO_KEY = "no_key"


def get_tavily_key(login: str) -> str | None:
    """Return the stored Tavily key for a user, or None when unset/empty."""
    if not login:
        return None
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT tavily_api_key FROM users WHERE login = %s", (login,))
        row = cursor.fetchone()
    if not row:
        return None
    return row.get("tavily_api_key") or None


def set_tavily_key(login: str, key: str) -> None:
    """Store the Tavily key for a user and stamp the addition time.

    An empty key clears the stored key together with its timestamp, so a user
    without a key never looks like one who added one.
    """
    if not key:
        delete_tavily_key(login)
        return
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE users
            SET tavily_api_key = %s, tavily_key_added_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE login = %s
            """,
            (key, login),
        )


def delete_tavily_key(login: str) -> None:
    """Remove the stored Tavily key for a user."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE users
            SET tavily_api_key = %s, tavily_key_added_at = %s, updated_at = CURRENT_TIMESTAMP
            WHERE login = %s
            """,
            (None, None, login),
        )


def mask_tavily_key(key: str) -> str:
    """Return a display mask that never contains the secret middle of the key."""
    if not key:
        return ""
    if len(key) <= len(KEY_PREFIX) + MASK_TAIL_CHARS:
        return f"{KEY_PREFIX}…"
    return f"{KEY_PREFIX}…{key[-MASK_TAIL_CHARS:]}"


def is_valid_tavily_key_shape(key: str) -> bool:
    """Check the key shape locally before spending a round trip on Tavily."""
    candidate = (key or "").strip()
    if not candidate.startswith(KEY_PREFIX):
        return False
    return len(KEY_PREFIX) < len(candidate) <= KEY_MAX_LENGTH
