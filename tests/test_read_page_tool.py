# tests/test_read_page_tool.py
"""The read_page tool: registration gating and executor behavior."""

from unittest.mock import MagicMock, patch

import pytest

from app import tools


@pytest.fixture
def ctx(test_app):
    test_app.config["CRAWL_ENABLED"] = True
    test_app.config["CRAWLER_URL"] = "http://crawler-test:11235"
    test_app.config["CRAWL_PAGE_TIMEOUT_S"] = 30
    test_app.config["CRAWL_MAX_PAGE_CHARS"] = 50000
    return {"app": test_app, "user_id": "valery", "lang": "ru"}


@pytest.mark.unit
class TestReadPageTool:
    def test_definition_exists(self):
        names = [d["function"]["name"] for d in tools.TOOL_DEFINITIONS]
        assert "read_page" in names

    def test_executor_returns_markdown(self, ctx, test_app):
        # In production execute_tool always runs inside an app context
        # (queue workers push one before dispatch); force_locale is a no-op
        # without it, mirroring test_tools.py's app_context() wrappers.
        with (
            test_app.app_context(),
            patch("app.tools.get_crawler_module") as get_crawler,
            patch("modules.crawler.requests.post") as post,
            patch("modules.crawler.requests.get", return_value=MagicMock(status_code=200)),
        ):
            crawler = MagicMock()
            crawler.read_page.return_value = "# Page"
            get_crawler.return_value = crawler
            assert tools.execute_tool("read_page", {"url": "https://example.com"}, ctx) == "# Page"
            assert post.called is False

    def test_blocked_url_returns_localized_error(self, ctx, test_app):
        # The real module instance bypasses get_crawler_module's availability
        # check, so the SSRF guard itself (validate_url) runs for real.
        with (
            test_app.app_context(),
            patch("app.tools.get_crawler_module", return_value=ctx["app"].modules["crawler"]),
        ):
            result = tools.execute_tool("read_page", {"url": "http://127.0.0.1:6379/"}, ctx)
        assert result.startswith("⚠️ ")

    def test_unavailable_crawler_returns_localized_error(self, ctx, test_app):
        with test_app.app_context(), patch("app.tools.get_crawler_module", return_value=None):
            result = tools.execute_tool("read_page", {"url": "https://example.com"}, ctx)
        assert result.startswith("⚠️ ")

    def test_read_page_failure_returns_localized_error(self, ctx, test_app):
        crawler = MagicMock()
        crawler.read_page.side_effect = RuntimeError("crawler exploded")
        with test_app.app_context(), patch("app.tools.get_crawler_module", return_value=crawler):
            result = tools.execute_tool("read_page", {"url": "https://example.com"}, ctx)
        assert result.startswith("⚠️ ")

    def test_gating_filter_hides_tool_without_crawler(self, test_app):
        with test_app.app_context():
            assert tools.get_crawler_module() is None
            defs = tools.get_tool_definitions()
            assert "read_page" not in {d["function"]["name"] for d in defs}

    def test_gating_filter_keeps_tool_with_available_crawler(self, test_app):
        crawler = MagicMock()
        crawler.available = True
        with test_app.app_context():
            test_app.modules["crawler"] = crawler
            try:
                assert tools.get_crawler_module() is crawler
                defs = tools.get_tool_definitions()
                assert "read_page" in {d["function"]["name"] for d in defs}
                assert defs is tools.TOOL_DEFINITIONS
            finally:
                del test_app.modules["crawler"]
