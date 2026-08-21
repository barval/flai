# modules/search.py
"""Web search module via SearXNG — self-hosted metasearch engine."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import trafilatura
from requests.exceptions import Timeout as RequestsTimeout

from app.mixins import TranslationMixin


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
            return []

        limit = max_results or self.max_results
        start_time = time.time()

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
            self.logger.info(f"SearXNG search: '{query[:60]}...' → {len(raw_results)} results in {elapsed}s")

            results: list[dict] = []
            fetch_urls = []
            for r in raw_results[:limit]:
                title = r.get("title", "")
                url = r.get("url", "")
                content = r.get("content", "") or ""
                if url and (not content.strip() or len(content.strip()) < 300):
                    fetch_urls.append((len(results), url))
                results.append({"title": title, "url": url, "content": content})

            if fetch_urls:
                self.logger.debug(f"Fetching page content for {len(fetch_urls)} results (short/poor snippets)")
                with ThreadPoolExecutor(max_workers=3) as executor:
                    future_map = {executor.submit(self._fetch_page_content, url, 8): idx for idx, url in fetch_urls}
                    for future in as_completed(future_map):
                        idx = future_map[future]
                        fetched = future.result()
                        if fetched:
                            results[idx]["content"] = fetched

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
