"""Crawl4AI sidecar client: page reading and bounded site crawling.

The container is optional (profile with-crawler). Every caller treats an
unavailable crawler as "feature off" — availability is checked, never assumed.
"""

import logging

import requests

from app.crawler_guard import validate_url

logger = logging.getLogger(__name__)


class CrawlerModule:
    name = "crawler"

    def __init__(self, app):
        self.app = app
        self.logger = logger

    @property
    def available(self) -> bool:
        if not self.app.config.get("CRAWL_ENABLED"):
            return False
        try:
            resp = requests.get(f"{self.app.config['CRAWLER_URL']}/health", timeout=3)
            return resp.status_code == 200
        except OSError:  # RequestException inherits from IOError/OSError
            return False

    def _timeout(self, key: str, default: int) -> int:
        return int(self.app.config.get(key, default))

    def read_page(self, url: str, max_chars: int | None = None) -> str:
        """Render one page in the browser and return its markdown."""
        clean = validate_url(url)
        limit = max_chars or int(self.app.config.get("CRAWL_MAX_PAGE_CHARS", 50000))
        resp = requests.post(
            f"{self.app.config['CRAWLER_URL']}/md",
            json={"url": clean},
            timeout=self._timeout("CRAWL_PAGE_TIMEOUT_S", 30),
        )
        resp.raise_for_status()
        markdown = (resp.json().get("data") or {}).get("markdown") or ""
        return markdown[:limit]

    def crawl_site(self, url: str, max_pages: int | None = None, max_depth: int | None = None) -> list[dict]:
        """Crawl the starting domain up to the configured limits."""
        clean = validate_url(url)
        pages_cap = max_pages or int(self.app.config.get("CRAWL_MAX_PAGES", 50))
        depth_cap = max_depth or int(self.app.config.get("CRAWL_MAX_DEPTH", 3))
        total_cap = int(self.app.config.get("CRAWL_MAX_TOTAL_CHARS", 1000000))
        resp = requests.post(
            f"{self.app.config['CRAWLER_URL']}/crawl",
            json={
                "urls": [clean],
                "browser_config": {"headless": True},
                "crawler_config": {
                    "mode": "deep",
                    "max_pages": pages_cap,
                    "max_depth": depth_cap,
                    "allowed_domains": [_domain_of(clean)],
                },
            },
            timeout=self._timeout("CRAWL_TIMEOUT_S", 300),
        )
        resp.raise_for_status()
        collected: list[dict] = []
        used = 0
        for page in resp.json().get("results") or []:
            markdown = (page.get("markdown") or "")[: self._timeout("CRAWL_MAX_PAGE_CHARS", 50000)]
            if not markdown:
                continue
            if used + len(markdown) > total_cap and collected:
                break
            collected.append({"url": page.get("url") or clean, "markdown": markdown})
            used += len(markdown)
        return collected


def _domain_of(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).hostname or ""
