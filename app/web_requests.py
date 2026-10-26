# app/web_requests.py
"""Accounting for outbound internet requests.

Every call FLAI makes to the outside world is recorded here — a Tavily or
SearXNG search, a page downloaded for text extraction, a Crawl4AI page, an
RLM sub-fetch. The counters feed the Users tab and the Monitoring chart.

Only counts are stored: no query text, no URLs, which keeps the accounting
GDPR-safe and cheap. Recording is best effort — a failed insert never breaks
the request that triggered it.
"""

from __future__ import annotations

import logging

from app.database import get_db

logger = logging.getLogger(__name__)

KIND_SEARCH = "search"
KIND_PAGE = "page"
KIND_CRAWL = "crawl"

KINDS = (KIND_SEARCH, KIND_PAGE, KIND_CRAWL)

PROVIDER_TAVILY = "tavily"
PROVIDER_SEARXNG = "searxng"
PROVIDER_TRAFILATURA = "trafilatura"
PROVIDER_CRAWL4AI = "crawl4ai"
PROVIDER_RLM = "rlm"


def record_request(
    user_id: str | None,
    kind: str,
    provider: str | None = None,
    count: int = 1,
) -> None:
    """Record ``count`` outbound requests for ``user_id``; never raises."""
    if not user_id or count <= 0:
        return
    try:
        with get_db() as conn:
            conn.cursor().execute(
                "INSERT INTO web_requests (user_id, kind, provider, request_count) "
                "VALUES (%s, %s, %s, %s)",
                (user_id, kind, provider, int(count)),
            )
    except Exception as exc:  # noqa: BLE001 — accounting must never break a request
        logger.warning(f"web request accounting failed ({kind}/{provider}): {exc}")
