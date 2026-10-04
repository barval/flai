"""Router mapping for the deep-study category."""

from unittest.mock import MagicMock

import pytest

from modules.base import BaseModule


@pytest.mark.unit
class TestRouterCrawl:
    def _module(self):
        module = BaseModule.__new__(BaseModule)
        module.logger = MagicMock()
        return module

    def test_crawl_marker_maps_to_crawl_action(self):
        module = self._module()
        result = module._parse_router_response(
            "[-CRAWL-] изучи документацию https://docs.example.com",
            "изучи документацию https://docs.example.com",
            "2026-10-04 12:00:00",
            "ru",
        )
        assert result["action"] == "crawl"
        assert result["needs_reasoning"] is False
        assert "изучи документацию" in result["query"]

    def test_search_marker_is_untouched(self):
        module = self._module()
        result = module._parse_router_response("[-SEARCH-] курс биткоина", "курс биткоина", "2026-10-04 12:00:00", "ru")
        assert result["action"] == "search"
