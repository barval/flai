"""Per-user Tavily API key storage.

The key must be presented to Tavily on every search, so it is stored as-is
(never hashed) and is never returned to the browser — callers only receive a
mask. Deleting the user removes the key with the row.
"""

import hashlib
import json
import logging
import random
import time
from contextlib import suppress

import requests
from requests.exceptions import RequestException

from app.database import get_db

logger = logging.getLogger(__name__)

KEY_PREFIX = "tvly-"
KEY_MAX_LENGTH = 128
MASK_TAIL_CHARS = 4

# Tavily's ``/usage`` endpoint sits behind a WAF that throttles per client:
# valid keys get an HTML 403 (and occasionally an explicit 429) when requests
# come too fast. A browser-like UA plus a few backed-off retries makes the
# lookup reliable for the low-frequency callers (admin page, profile popup); a
# throttling 403/429 is never treated as proof of an invalid key (only a 401
# JSON is).
REQUEST_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
USAGE_MAX_ATTEMPTS = 5
USAGE_RETRY_DELAY = 0.8  # base of the exponential backoff, in seconds
USAGE_RETRY_JITTER = 0.4  # random spread so parallel callers do not sync up
USAGE_RETRY_MAX_DELAY = 8.0
USAGE_TOTAL_TIMEOUT = 25  # wall-clock cap on the retry loop, in seconds

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

    url = f"{api_url.rstrip('/')}{USAGE_PATH}"
    headers = {"Authorization": f"Bearer {api_key}", "User-Agent": REQUEST_USER_AGENT}

    deadline = time.monotonic() + USAGE_TOTAL_TIMEOUT
    response = None
    for attempt in range(USAGE_MAX_ATTEMPTS):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
        except RequestException as e:
            logger.warning(f"Tavily usage request failed: {e}")
            response = None
        else:
            if response.status_code == 200 or response.status_code not in (401, 403, 429):
                break
        # 403/429 are the WAF/rate limiter, not a key verdict. The edge refuses
        # a share of the requests to this path even without a key, so the only
        # way through is a patient, spread-out retry: an exponential backoff
        # with jitter, honoring Retry-After when the limiter sends one.
        if attempt + 1 >= USAGE_MAX_ATTEMPTS or time.monotonic() >= deadline:
            break
        delay = min(USAGE_RETRY_DELAY * (2 ** attempt), USAGE_RETRY_MAX_DELAY)
        retry_after = response.headers.get("Retry-After") if response is not None else None
        if retry_after and str(retry_after).strip().isdigit():
            delay = min(float(retry_after), USAGE_RETRY_MAX_DELAY + 2)
        time.sleep(delay + random.uniform(0, USAGE_RETRY_JITTER))

    if response is None:
        return result
    if response.status_code == 401:
        result["status"] = STATUS_INVALID
        return result
    if response.status_code != 200:
        # Transient throttling or server error: report as unavailable, never
        # as an invalid key, so a valid key is not mislabeled.
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


def _usage_cache_key(login: str, api_key: str) -> str:
    """Cache key that changes with the key, so a replaced key never reads stale quota."""
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]
    return f"tavily_usage:{login}:{digest}"


def cached_tavily_usage(
    login: str,
    api_key: str,
    api_url: str,
    timeout: int,
    ok_ttl: int,
    fail_ttl: int,
) -> dict:
    """Quota lookup with a short-lived cache in Redis.

    A lookup that answered is reused for ``ok_ttl`` seconds, so the column stays
    current while the panel is open. A lookup that could not answer (edge block,
    timeout) is remembered only for ``fail_ttl`` seconds: long enough that page
    reloads stop hammering an endpoint that already refused us — retrying is
    exactly what keeps the block alive — and short enough that the column
    recovers by itself once the endpoint answers again.
    """
    if fail_ttl <= 0:
        fail_ttl = 0
    cache_key = _usage_cache_key(login, api_key)

    client = None
    try:
        from app.api_bridge import get_redis_client

        client = get_redis_client()
    except Exception as exc:  # noqa: BLE001 — the cache is best effort
        logger.debug(f"Tavily usage cache unavailable: {exc}")

    if client is not None:
        try:
            cached = client.get(cache_key)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"Tavily usage cache read failed: {exc}")
            cached = None
        if cached:
            try:
                return json.loads(cached)
            except (TypeError, ValueError):
                logger.warning("Ignoring malformed Tavily usage cache entry")

    usage = fetch_tavily_usage(api_key, api_url, timeout)

    if client is not None:
        answered = usage.get("status") in (STATUS_OK, STATUS_EXHAUSTED, STATUS_INVALID)
        ttl = ok_ttl if answered else fail_ttl
        if ttl > 0:
            try:
                client.set(cache_key, json.dumps(usage), ex=int(ttl))
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"Tavily usage cache write failed: {exc}")
        with suppress(Exception):
            client.close()
    return usage
