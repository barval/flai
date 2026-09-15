# modules/search.py
"""Web search module via SearXNG — self-hosted metasearch engine."""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import pytz
import requests
import trafilatura
from requests.exceptions import Timeout as RequestsTimeout

from app.mixins import TranslationMixin

_RU_MONTHS_GENITIVE = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}

_EN_MONTHS = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}

# Relative date words → day offset. Most specific word first so that
# "позавчера" / "day before yesterday" match before "вчера" / "yesterday".
_RELATIVE_DATE_HINTS: dict[str, list[tuple[re.Pattern[str], int]]] = {
    "ru": [
        (re.compile(r"\bпозавчера\b", re.IGNORECASE), -2),
        (re.compile(r"\bвчера\b", re.IGNORECASE), -1),
        (re.compile(r"\bсегодня\b", re.IGNORECASE), 0),
    ],
    "en": [
        (re.compile(r"\bthe day before yesterday\b", re.IGNORECASE), -2),
        (re.compile(r"\byesterday\b", re.IGNORECASE), -1),
        (re.compile(r"\btoday\b", re.IGNORECASE), 0),
    ],
}


def _format_search_date(d: datetime, lang: str) -> str:
    if lang == "en":
        return f"{_EN_MONTHS[d.month]} {d.day}, {d.year}"
    return f"{d.day} {_RU_MONTHS_GENITIVE[d.month]} {d.year}"


def enhance_query_with_date(query: str, lang: str = "ru", now: datetime | None = None) -> str:
    """Append an absolute date to queries containing relative date words.

    Search engines have no notion of "yesterday" — a query like
    "What IT news happened yesterday?" returns landing pages of news
    sections instead of dated articles. Appending the resolved date
    ("yesterday (September 14, 2026)") anchors the query to a specific day.
    This is query normalization, not routing.

    Args:
        query: Original search query.
        lang: User language ('ru' or 'en') — selects the word list and date format.
        now: Reference datetime for tests; defaults to current UTC time.

    Returns:
        The query with "(<date>)" appended after the relative date word,
        or unchanged if no relative date word is present.
    """
    if not query:
        return query
    if now is None:
        now = datetime.now(pytz.UTC)
    for pattern, offset in _RELATIVE_DATE_HINTS.get(lang or "ru", _RELATIVE_DATE_HINTS["ru"]):
        if not pattern.search(query):
            continue
        target = now + timedelta(days=offset)
        label = _format_search_date(target, lang)
        query = pattern.sub(lambda m, _label=label: f"{m.group(0)} ({_label})", query)
        break
    return query


class SearchModule(TranslationMixin):
    """Interface to SearXNG for web search queries."""

    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.searxng_url = None
        self.timeout = 10
        self.max_results = 7
        self.available = False

        if app:
            self.init_app(app)

    def init_app(self, app):
        """Initialize with Flask app config."""
        self.searxng_url = app.config.get("SEARXNG_URL") or "http://flai-searxng:8080"
        self.searxng_url = self.searxng_url.rstrip("/")
        self.timeout = app.config.get("SEARXNG_TIMEOUT", 10)
        self.max_results = app.config.get("SEARXNG_MAX_RESULTS", 7)
        self.check_availability()

    def check_availability(self) -> bool:
        """Check if SearXNG instance is reachable."""
        if not self.searxng_url:
            self.available = False
            return False
        try:
            resp = requests.get(f"{self.searxng_url}/healthz", timeout=5)
            self.available = resp.status_code == 200
            if self.available:
                self.logger.info(f"SearXNG available at {self.searxng_url}")
            else:
                self.logger.warning(f"SearXNG health check failed: {resp.status_code}")
            return self.available  # type: ignore[no-any-return]
        except Exception as e:
            self.logger.warning(f"SearXNG not available: {e}")
            self.available = False
            return False

    def search(self, query: str, lang: str = "ru", max_results: int | None = None) -> list[dict]:
        """Search the web via SearXNG JSON API.

        Args:
            query: Search query string.
            lang: Language code (e.g. 'ru', 'en').
            max_results: Override default max_results.

        Returns:
            List of dicts with keys: title, url, content.
        """
        if not self.available:
            self.check_availability()
        if not self.available:
            self.logger.warning(f"SearXNG search skipped: not available (query='{query[:120]}')")
            return []

        limit = max_results or self.max_results
        start_time = time.time()

        # Anchor relative date words ("yesterday", "today", ...) to an absolute
        # date in the user's timezone so engines return dated articles, not
        # generic landing pages.
        ref_now: datetime | None = None
        try:
            from flask import current_app

            tz = current_app.config.get("TIMEZONE")
            if tz:
                ref_now = datetime.now(tz)
        except RuntimeError:
            pass
        dated_query = enhance_query_with_date(query, lang, now=ref_now)
        if dated_query != query:
            self.logger.info(f"Search query date-normalized: '{query[:120]}' -> '{dated_query[:160]}'")
            query = dated_query

        try:
            resp = requests.post(
                f"{self.searxng_url}/search",
                data={"q": query, "format": "json", "language": lang},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            raw_results = data.get("results", [])
            elapsed = round(time.time() - start_time, 2)
            self.logger.info(
                f"SearXNG search: '{query[:120]}' → {len(raw_results)} results in {elapsed}s "
                f"(language={lang}, limit={limit})"
            )

            # Diagnose transient engine failures (CAPTCHA / rate limiting) early —
            # these are the top cause of "0 results" and are invisible to callers.
            unresponsive = data.get("unresponsive_engines") or []
            if unresponsive:
                reasons = "; ".join(f"{name}: {reason}" for name, reason, *_ in unresponsive)
                if not raw_results:
                    self.logger.warning(
                        f"SearXNG returned 0 results for '{query[:120]}' — all engines failed: {reasons}"
                    )
                else:
                    self.logger.warning(
                        f"SearXNG partial engine failures for '{query[:120]}': {reasons} "
                        f"({len(raw_results)} results returned)"
                    )

            results: list[dict] = []
            fetch_urls: list[tuple[int, str]] = []

            # Deduplicate a wider pool (limit*3) by URL path and normalized title,
            # then keep results in their original relevance order.
            seen_paths: set[str] = set()
            seen_titles: set[str] = set()
            pool = [r for r in raw_results[: limit * 3] if r.get("url")]
            unique: list[dict] = []
            for r in pool:
                path = (r.get("url") or "").split("?")[0].rstrip("/")
                title = " ".join((r.get("title") or "").lower().split())
                if path and path in seen_paths:
                    continue
                if len(title) > 15 and title in seen_titles:
                    continue
                seen_paths.add(path)
                seen_titles.add(title)
                unique.append(r)

            for r in unique:
                if len(results) >= limit:
                    break
                title = r.get("title") or ""
                url = r.get("url") or ""
                content = (r.get("content") or "").strip()
                idx = len(results)
                results.append({"title": title, "url": url, "content": content})
                if url and len(content) < 300:
                    fetch_urls.append((idx, url))

            if fetch_urls:
                self.logger.debug(f"Fetching page content for {len(fetch_urls)} results (short/poor snippets)")
                with ThreadPoolExecutor(max_workers=3) as executor:
                    future_map = {executor.submit(self._fetch_page_content, url, 8): idx for idx, url in fetch_urls}
                    for future in as_completed(future_map):
                        idx = future_map[future]
                        fetched = future.result()
                        if fetched:
                            results[idx]["content"] = fetched

            # Replace empty-content results with untapped pool candidates that already
            # carry content — degraded engines shouldn't flood the reasoning context
            # with title-only noise.
            if len(results) < limit:
                used_paths = {(r.get("url") or "").split("?")[0].rstrip("/") for r in results}
                for r in unique:
                    if len(results) >= limit:
                        break
                    key = (r.get("url") or "").split("?")[0].rstrip("/")
                    content = (r.get("content") or "").strip()
                    if key in used_paths or not content:
                        continue
                    results.append({"title": r.get("title") or "", "url": r.get("url") or "", "content": content})
                    used_paths.add(key)

            fetched_count = sum(1 for r in results if len(r.get("content", "")) > 300)
            if fetched_count > 0:
                self.logger.debug(f"Enhanced {fetched_count}/{len(results)} results with full page content")

            if results and not any(r["content"].strip() for r in results):
                self.logger.warning(f"All {len(results)} search results have empty content after page fetch")

            return results
        except RequestsTimeout:
            self.logger.warning(f"SearXNG search timeout ({self.timeout}s): {query[:60]}...")
            return []
        except Exception as e:
            self.logger.error(f"SearXNG search failed: {e}")
            return []

    def _fetch_page_content(self, url: str, timeout: int = 8) -> str:
        """Download a page and extract readable text via trafilatura.

        Args:
            url: Page URL to fetch.
            timeout: Request timeout in seconds.

        Returns:
            Extracted text content, or empty string on failure.
        """
        try:
            resp = requests.get(
                url,
                timeout=timeout,
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
                },
                allow_redirects=True,
            )
            resp.raise_for_status()
            extracted = trafilatura.extract(resp.content)
            if extracted:
                text = extracted.strip()
                if text:
                    self.logger.debug(f"Fetched page content ({len(text)} chars): {url[:80]}...")
                    return text  # type: ignore[no-any-return]
            self.logger.debug(f"No content extracted from: {url[:80]}...")
        except Exception as e:
            self.logger.debug(f"Failed to fetch page content from {url[:80]}...: {e}")
        return ""

    def format_results_context(self, results: list[dict], lang: str = "ru", max_chars: int = 0) -> str:
        """Format search results into a context string for the reasoning model.

        Each result's content is truncated to MAX_RESULT_CHARS to keep the total
        within budget. If total still exceeds max_chars, trailing results are dropped.

        Args:
            results: List of search result dicts.
            lang: Language for labels.
            max_chars: Maximum total chars for the context. If 0 (default) or unset,
                       computed dynamically from the reasoning model's context window.

        Returns:
            Formatted context string.
        """
        if not results:
            return ""

        if max_chars == 0 and hasattr(self, "app") and self.app:
            base = self.app.modules.get("base")
            if base and hasattr(base, "get_search_context_limit"):
                max_chars = base.get_search_context_limit()
        if max_chars <= 0:
            max_chars = 10000

        max_result_chars = 2000

        source_label = self._("Web search result", lang)
        parts = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            url = r.get("url", "")
            content = r.get("content", "")
            if len(content) > max_result_chars:
                content = content[:max_result_chars] + "…"
            parts.append(f"[{source_label} {i}: {title}]\n{url}\n{content}")

        joined = "\n\n".join(parts)

        if len(joined) > max_chars:
            self.logger.debug(f"Truncating search context: {len(joined)} chars > {max_chars} limit")
            truncated: list[str] = []
            current_len = 0
            for p in parts:
                next_len = current_len + len(p) + (2 if truncated else 0)
                if next_len > max_chars:
                    break
                truncated.append(p)
                current_len = next_len
            joined = "\n\n".join(truncated)
            self.logger.debug(f"Truncated to {len(truncated)}/{len(results)} results ({len(joined)} chars)")

        return joined
