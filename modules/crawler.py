"""Crawl4AI sidecar client: page reading and bounded site crawling.

The container is optional (profile with-crawler). Every caller treats an
unavailable crawler as "feature off" — availability is checked, never assumed.
"""

import logging
import re
import time
from urllib.parse import urlsplit

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

    def _headers(self) -> dict:
        token = self.app.config.get("CRAWL_API_TOKEN") or ""
        return {"Authorization": f"Bearer {token}"} if token else {}

    def read_page(self, url: str, max_chars: int | None = None) -> str:
        """Render one page in the browser and return its markdown."""
        clean = validate_url(url)
        limit = max_chars or int(self.app.config.get("CRAWL_MAX_PAGE_CHARS", 50000))
        resp = requests.post(
            f"{self.app.config['CRAWLER_URL']}/md",
            json={"url": clean},
            headers=self._headers(),
            timeout=self._timeout("CRAWL_PAGE_TIMEOUT_S", 30),
        )
        resp.raise_for_status()
        markdown = resp.json().get("markdown") or ""
        return markdown[:limit]

    def crawl_site(self, url: str, max_pages: int | None = None, max_depth: int | None = None) -> list[dict]:
        """Crawl the starting URL up to the configured limits.

        The sidecar deliberately forbids deep-crawl strategies on untrusted
        requests (arbitrary strategy objects), so the BFS lives here: fetch
        pages one by one via /md?f=raw, collect links from the markdown, and
        stop at the page/depth/char caps.

        Scope: when the start URL points deeper than the domain root (a path
        beyond "/"), the traversal is confined to that path prefix — the docs
        section stays the docs section instead of drifting into the whole
        portal's navigation.
        """
        clean = validate_url(url)
        pages_cap = max_pages or int(self.app.config.get("CRAWL_MAX_PAGES", 50))
        depth_cap = max_depth or int(self.app.config.get("CRAWL_MAX_DEPTH", 3))
        total_cap = int(self.app.config.get("CRAWL_MAX_TOTAL_CHARS", 1000000))
        page_cap = self._timeout("CRAWL_MAX_PAGE_CHARS", 50000)
        timeout = self._timeout("CRAWL_TIMEOUT_S", 300)
        deadline = time.monotonic() + timeout

        start = urlsplit(clean)
        base = f"{start.scheme}://{start.netloc}"
        domain = start.hostname or ""
        path = start.path or "/"
        # Crawl scope: the starting URL's directory prefix. A link to the
        # domain root (https://site.com) crawls the whole domain; a link to
        # https://site.com/docs/ (or /docs/page.html) is confined to /docs/.
        scope_prefix = path if path.endswith("/") else path.rsplit("/", 1)[0] + "/"
        if scope_prefix == "//":
            scope_prefix = "/"

        collected: list[dict] = []
        seen: set[str] = set()
        used = 0
        frontier: list[tuple[str, int]] = [(clean, 0)]
        while frontier and len(collected) < pages_cap and used < total_cap:
            if time.monotonic() > deadline:
                break
            current, depth = frontier.pop(0)
            if current in seen:
                continue
            seen.add(current)
            markdown = self._fetch_markdown(current)
            if not markdown:
                continue
            if depth < depth_cap:
                for link in _extract_links(markdown, base, domain):
                    if link not in seen and _in_scope(link, domain, scope_prefix):
                        frontier.append((link, depth + 1))
            trimmed = markdown[:page_cap]
            if used + len(trimmed) > total_cap and collected:
                break
            collected.append({"url": current, "markdown": trimmed})
            used += len(trimmed)
        return collected

    def _fetch_markdown(self, url: str) -> str:
        """Fetch one page with links preserved (raw filter) for the crawler BFS."""
        try:
            resp = requests.post(
                f"{self.app.config['CRAWLER_URL']}/md",
                json={"url": url, "f": "raw"},
                headers=self._headers(),
                timeout=self._timeout("CRAWL_PAGE_TIMEOUT_S", 30),
            )
            resp.raise_for_status()
            return resp.json().get("markdown") or ""
        except OSError:
            return ""


def _extract_links(markdown: str, base: str, domain: str) -> list[str]:
    """Same-domain http(s) links found in a raw-markdown page."""
    links: list[str] = []
    for target in re.findall(r"\[[^\]]*\]\(([^)\s]+)[^)]*\)", markdown):
        if target.startswith("//"):
            target = f"{urlsplit(base).scheme}:{target}"
        if not target.startswith(("http://", "https://")):
            if target.startswith("/"):
                target = base + target
            else:
                continue
        parsed = urlsplit(target)
        if parsed.hostname != domain or parsed.scheme not in ("http", "https"):
            continue
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if parsed.query:
            clean += f"?{parsed.query}"
        links.append(clean)
    return links


def _in_scope(url: str, domain: str, scope_prefix: str) -> bool:
    """True for same-host links within the crawl's path-prefix scope."""
    parsed = urlsplit(url)
    if parsed.hostname != domain or parsed.scheme not in ("http", "https"):
        return False
    if scope_prefix == "/":
        return True
    return parsed.path == scope_prefix.rstrip("/") or parsed.path.startswith(scope_prefix)
