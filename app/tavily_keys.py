"""Per-user Tavily API key storage.

The key must be presented to Tavily on every search, so it is stored as-is
(never hashed) and is never returned to the browser — callers only receive a
mask. Deleting the user removes the key with the row.
"""

import logging

import requests
from requests.exceptions import RequestException

from app.database import get_db

logger = logging.getLogger(__name__)

KEY_PREFIX = "tvly-"
KEY_MAX_LENGTH = 128
MASK_TAIL_CHARS = 4

STATUS_NO_KEY = "no_key"
STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"
STATUS_EXHAUSTED = "exhausted"
STATUS_INVALID = "invalid"

USAGE_PATH = "/usage"


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


def fetch_tavily_usage(api_key: str, api_url: str, timeout: int) -> dict:
    """Return the credit quota for a Tavily key.

    Never raises: transport problems become ``unavailable`` so the profile popup
    and the admin table can render a status instead of an error. ``api_key`` is
    only sent in the Authorization header and is never logged.
    """
    result: dict = {
        "status": STATUS_UNAVAILABLE,
        "plan": None,
        "limit": None,
        "used": None,
        "remaining": None,
    }
    if not api_key:
        result["status"] = STATUS_NO_KEY
        return result

    try:
        response = requests.get(
            f"{api_url.rstrip('/')}{USAGE_PATH}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
    except RequestException as e:
        logger.warning(f"Tavily usage request failed: {e}")
        return result

    if response.status_code in (401, 403):
        result["status"] = STATUS_INVALID
        return result
    if response.status_code != 200:
        logger.warning(f"Tavily usage returned HTTP {response.status_code}")
        return result

    try:
        payload = response.json() or {}
    except ValueError:
        logger.warning("Tavily usage returned a non-JSON body")
        return result

    if not isinstance(payload, dict):
        payload = {}
    key_info = payload.get("key")
    if not isinstance(key_info, dict):
        key_info = {}
    account = payload.get("account")
    if not isinstance(account, dict):
        account = {}

    limit = key_info.get("limit")
    if type(limit) is not int:
        limit = account.get("plan_limit")
    used = key_info.get("usage")
    if type(used) is not int:
        used = account.get("plan_usage")
    if type(limit) is not int or type(used) is not int:
        limit = used = None

    remaining = None
    if type(limit) is int and type(used) is int:
        remaining = max(limit - used, 0)
    result.update(
        {
            "status": STATUS_EXHAUSTED if remaining == 0 else STATUS_OK,
            "plan": account.get("current_plan"),
            "limit": limit,
            "used": used,
            "remaining": remaining,
        }
    )
    return result
