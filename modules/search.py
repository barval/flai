# modules/search.py
"""Web search module via SearXNG — self-hosted metasearch engine."""

import logging
import time

import requests
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
            results = data.get("results", [])
            elapsed = round(time.time() - start_time, 2)
            self.logger.info(
                f"SearXNG search: '{query[:60]}...' → {len(results)} results in {elapsed}s"
            )
            return [
                {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
                for r in results[:limit]
            ]
        except RequestsTimeout:
            self.logger.warning(f"SearXNG search timeout ({self.timeout}s): {query[:60]}...")
            return []
        except Exception as e:
            self.logger.error(f"SearXNG search failed: {e}")
            return []

    def format_results_context(self, results: list[dict], lang: str = "ru") -> str:
        """Format search results into a context string for the reasoning model.

        Args:
            results: List of search result dicts.
            lang: Language for labels.

        Returns:
            Formatted context string.
        """
        if not results:
            return ""

        source_label = self._("Web search result", lang)
        parts = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            url = r.get("url", "")
            content = r.get("content", "")
            parts.append(f"[{source_label} {i}: {title}]\n{url}\n{content}")

        return "\n\n".join(parts)
