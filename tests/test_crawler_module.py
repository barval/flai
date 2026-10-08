"""HTTP-contract tests for the Crawl4AI client wrapper."""

import time
from unittest.mock import MagicMock, patch

import pytest

from modules.crawler import CrawlerModule, SiteBlockedError


@pytest.fixture
def module(test_app):
    test_app.config["CRAWL_ENABLED"] = True
    test_app.config["CRAWLER_URL"] = "http://crawler-test:11235"
    test_app.config["CRAWL_MAX_PAGE_CHARS"] = 50000
    test_app.config["CRAWL_PAGE_TIMEOUT_S"] = 30
    test_app.config["CRAWL_MAX_PAGES"] = 50
    test_app.config["CRAWL_MAX_DEPTH"] = 3
    test_app.config["CRAWL_TIMEOUT_S"] = 300
    test_app.config["CRAWL_CONCURRENCY"] = 2
    return CrawlerModule(test_app)


def _resp(status=200, payload=None):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload if payload is not None else {}
    return resp


@pytest.mark.unit
class TestAvailability:
    def test_disabled_by_config(self, test_app):
        test_app.config["CRAWL_ENABLED"] = False
        assert CrawlerModule(test_app).available is False

    def test_container_down_is_not_available(self, module):
        with patch("modules.crawler.requests.get", side_effect=OSError):
            assert module.available is False

    def test_healthy_container_is_available(self, module):
        with patch("modules.crawler.requests.get", return_value=_resp(200)):
            assert module.available is True


@pytest.mark.unit
class TestReadPage:
    def test_reads_markdown_from_crawl4ai_payload(self, module):
        payload = {"url": "https://example.com/a", "markdown": "# Title\n\nBody", "success": True}
        with (
            patch("modules.crawler.requests.post", return_value=_resp(200, payload)),
            patch("modules.crawler.requests.get", return_value=_resp(200)),
        ):
            assert module.read_page("https://example.com/a") == "# Title\n\nBody"

    def test_truncates_to_max_chars(self, module):
        payload = {"url": "https://example.com/a", "markdown": "x" * 70000, "success": True}
        with (
            patch("modules.crawler.requests.post", return_value=_resp(200, payload)),
            patch("modules.crawler.requests.get", return_value=_resp(200)),
        ):
            assert len(module.read_page("https://example.com/a")) == 50000

    def test_failure_raises(self, module):
        with (
            patch("modules.crawler.requests.post", side_effect=OSError),
            patch("modules.crawler.requests.get", return_value=_resp(200)),
            pytest.raises(OSError),
        ):
            module.read_page("https://example.com/a")

    def test_sends_bearer_token_when_configured(self, module):
        module.app.config["CRAWL_API_TOKEN"] = "secret-token"
        payload = {"url": "https://example.com/a", "markdown": "ok", "success": True}
        with (
            patch("modules.crawler.requests.post", return_value=_resp(200, payload)) as post_mock,
            patch("modules.crawler.requests.get", return_value=_resp(200)),
        ):
            module.read_page("https://example.com/a")
        assert post_mock.call_args.kwargs["headers"] == {"Authorization": "Bearer secret-token"}


@pytest.mark.unit
class TestCrawlSite:
    def test_returns_page_list(self, module):
        # The client-side BFS calls /md per page (deep crawl is forbidden
        # server-side on untrusted requests); links come from the raw markdown.
        home = {
            "url": "https://example.com/",
            "markdown": "# Home\n\n[Page A](/a) [Page B](https://example.com/b) [Other](https://other.org/x)",
            "success": True,
        }
        page_a = {"url": "https://example.com/a", "markdown": "AAA", "success": True}
        page_b = {"url": "https://example.com/b", "markdown": "BBB", "success": True}
        responses = [home, page_a, page_b]
        with patch("modules.crawler.requests.post", side_effect=lambda *a, **k: _resp(200, responses.pop(0))):
            pages_out = module.crawl_site("https://example.com", max_pages=3, max_depth=1)
        assert pages_out == [
            {
                "url": "https://example.com",
                "markdown": "# Home\n\n[Page A](/a) [Page B](https://example.com/b) [Other](https://other.org/x)",
            },
            {"url": "https://example.com/a", "markdown": "AAA"},
            {"url": "https://example.com/b", "markdown": "BBB"},
        ]

    def test_skips_failed_and_empty_pages(self, module):
        home = {"url": "https://example.com/", "markdown": "", "success": True}
        with patch("modules.crawler.requests.post", return_value=_resp(200, home)):
            assert module.crawl_site("https://example.com") == []

    def test_caps_pages_and_total_chars(self, module):
        module.app.config["CRAWL_MAX_TOTAL_CHARS"] = 120000
        home = {"url": "https://example.com", "markdown": "x" * 60000 + "\n[a](/a) [b](/b)", "success": True}
        page_a = {"url": "https://example.com/a", "markdown": "y" * 60000, "success": True}
        page_b = {"url": "https://example.com/b", "markdown": "z" * 60000, "success": True}
        responses = [home, page_a, page_b]
        with patch("modules.crawler.requests.post", side_effect=lambda *a, **k: _resp(200, responses.pop(0))):
            out = module.crawl_site("https://example.com")
        assert len(out) == 2  # 60000 + 50000(truncated) = 110000; page B would exceed → stop
        assert len(out[1]["markdown"]) == 50000

    def test_respects_deadline(self, module):
        module.app.config["CRAWL_TIMEOUT_S"] = 1
        with patch(
            "modules.crawler.requests.post",
            side_effect=lambda *a, **k: (time.sleep(2), _resp(200, {"markdown": "x", "success": True}))[1],
        ):
            out = module.crawl_site("https://example.com", max_pages=10)
        assert len(out) <= 1  # deadline stops after the first fetch

    def test_broken_links_are_dropped_not_fatal(self, module):
        home = {"url": "https://example.com/", "markdown": "[dead](/dead) [live](/live)", "success": True}
        dead_error = {"url": "https://example.com/dead", "markdown": "", "success": True}
        live = {"url": "https://example.com/live", "markdown": "LIVE", "success": True}
        responses = [home, dead_error, live]
        with patch("modules.crawler.requests.post", side_effect=lambda *a, **k: _resp(200, responses.pop(0))):
            out = module.crawl_site("https://example.com", max_pages=3, max_depth=1)
        assert out[-1] == {"url": "https://example.com/live", "markdown": "LIVE"}

    def test_deep_start_url_is_confined_to_its_path_prefix(self, module):
        # Start at /docs/: sibling sections of the portal (/blog/, /pricing,
        # the root page) stay out of scope even though they are same-domain.
        start = {
            "url": "https://site.com/docs/",
            "markdown": "[Guide](/docs/guide.html) [Blog](/blog/x) [Pricing](/pricing) [Root](/)",
            "success": True,
        }
        guide = {"url": "https://site.com/docs/guide.html", "markdown": "GUIDE", "success": True}
        blog = {"url": "https://site.com/blog/x", "markdown": "BLOG", "success": True}
        root = {"url": "https://site.com", "markdown": "ROOT", "success": True}
        responses = [start, guide, blog, root]
        with patch("modules.crawler.requests.post", side_effect=lambda *a, **k: _resp(200, responses.pop(0))):
            out = module.crawl_site("https://site.com/docs/", max_pages=5, max_depth=1)
        assert [p["url"] for p in out] == ["https://site.com/docs/", "https://site.com/docs/guide.html"]

    def test_domain_root_start_crawls_whole_domain(self, module):
        # A root start has scope "/" — every same-domain link is in scope.
        start = {
            "url": "https://site.com",
            "markdown": "[Docs](/docs/a) [Blog](/blog/x)",
            "success": True,
        }
        docs = {"url": "https://site.com/docs/a", "markdown": "DOCS", "success": True}
        blog = {"url": "https://site.com/blog/x", "markdown": "BLOG", "success": True}
        responses = [start, docs, blog]
        with patch("modules.crawler.requests.post", side_effect=lambda *a, **k: _resp(200, responses.pop(0))):
            out = module.crawl_site("https://site.com", max_pages=5, max_depth=1)
        assert [p["url"] for p in out] == ["https://site.com", "https://site.com/docs/a", "https://site.com/blog/x"]


@pytest.mark.unit
class TestSiteBlocked:
    def _blocked_502(self):
        resp = MagicMock()
        resp.status_code = 502
        resp.json.return_value = {"detail": "Blocked by anti-bot protection: structural shell"}
        return resp

    def test_start_page_502_raises_site_blocked(self, module):
        with (
            patch("modules.crawler.requests.post", return_value=self._blocked_502()),
            pytest.raises(SiteBlockedError),
        ):
            module.crawl_site("https://dns-shop.ru")

    def test_block_page_markdown_raises_site_blocked(self, module):
        block_page = {"markdown": "403 Error\nForbidden\nAccess to dns-shop.ru is forbidden.\nIP: 1.2.3.4"}
        with (
            patch("modules.crawler.requests.post", return_value=_resp(200, block_page)),
            pytest.raises(SiteBlockedError),
        ):
            module.crawl_site("https://dns-shop.ru")

    def test_blocked_inner_page_is_skipped(self, module):
        home = {"url": "https://site.com", "markdown": "[x](/x) [y](/y)", "success": True}
        blocked_x = {"url": "https://site.com/x", "markdown": "403 Error\nForbidden\nAccess is forbidden"}
        y = {"url": "https://site.com/y", "markdown": "PAGE Y", "success": True}
        responses = [home, blocked_x, y]
        with patch("modules.crawler.requests.post", side_effect=lambda *a, **k: _resp(200, responses.pop(0))):
            out = module.crawl_site("https://site.com", max_pages=3, max_depth=1)
        assert out == [
            {"url": "https://site.com", "markdown": "[x](/x) [y](/y)"},
            {"url": "https://site.com/y", "markdown": "PAGE Y"},
        ]
