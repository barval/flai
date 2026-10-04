"""HTTP-contract tests for the Crawl4AI client wrapper."""

from unittest.mock import MagicMock, patch

import pytest

from modules.crawler import CrawlerModule


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
        payload = {"data": {"markdown": "# Title\n\nBody"}}
        with (
            patch("modules.crawler.requests.post", return_value=_resp(200, payload)),
            patch("modules.crawler.requests.get", return_value=_resp(200)),
        ):
            assert module.read_page("https://example.com/a") == "# Title\n\nBody"

    def test_truncates_to_max_chars(self, module):
        payload = {"data": {"markdown": "x" * 70000}}
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


@pytest.mark.unit
class TestCrawlSite:
    def test_returns_page_list(self, module):
        pages = {
            "results": [
                {"url": "https://example.com/a", "markdown": "A"},
                {"url": "https://example.com/b", "markdown": "B"},
            ]
        }
        with (
            patch("modules.crawler.requests.post", return_value=_resp(200, pages)) as post_mock,
            patch("modules.crawler.requests.get", return_value=_resp(200)),
        ):
            pages_out = module.crawl_site("https://example.com")
        assert pages_out == [
            {"url": "https://example.com/a", "markdown": "A"},
            {"url": "https://example.com/b", "markdown": "B"},
        ]
        # The deep-crawl request pins the starting domain server-side.
        body = post_mock.call_args.kwargs["json"]
        assert body["crawler_config"]["allowed_domains"] == ["example.com"]
        assert body["crawler_config"]["max_pages"] == 50

    def test_caps_pages_and_total_chars(self, module):
        big = {"results": [{"url": f"https://example.com/{i}", "markdown": "y" * 60000} for i in range(60)]}
        with (
            patch("modules.crawler.requests.post", return_value=_resp(200, big)),
            patch("modules.crawler.requests.get", return_value=_resp(200)),
        ):
            module.app.config["CRAWL_MAX_TOTAL_CHARS"] = 120000
            out = module.crawl_site("https://example.com")
        assert len(out) == 2  # 2 × 60000 = 120000 budget exhausted
