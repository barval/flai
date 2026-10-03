"""Tests for the Tavily-first web search provider and its SearXNG fallback."""

from unittest.mock import MagicMock, patch

import pytest
import requests as real_requests

from modules.search import SearchModule

TAVILY_PAYLOAD = {
    "results": [
        {"title": "First", "url": "https://a.example/1", "content": "A" * 800},
        {"title": "Second", "url": "https://b.example/2", "content": "B" * 800},
    ]
}


def _module(**overrides):
    app = MagicMock()
    app.config = {
        "SEARXNG_URL": "http://flai-searxng:8080",
        "SEARXNG_TIMEOUT": 10,
        "SEARXNG_MAX_RESULTS": 5,
        "TAVILY_ENABLED": True,
        "TAVILY_API_URL": "https://api.tavily.com",
        "TAVILY_TIMEOUT": 20,
        "TAVILY_MAX_RESULTS": 5,
        "TAVILY_SEARCH_DEPTH": "basic",
    }
    app.config.update(overrides)
    with patch("modules.search.requests") as mock_requests:
        health = MagicMock()
        health.status_code = 200
        mock_requests.get.return_value = health
        return SearchModule(app)


def _tavily_response(payload=None, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload if payload is not None else TAVILY_PAYLOAD
    return response


@pytest.mark.unit
class TestTavilyFirst:
    def test_tavily_answers_when_a_key_is_present(self):
        module = _module()

        with patch("modules.search.requests") as mock_requests:
            mock_requests.post.return_value = _tavily_response()
            results, provider = module.search_with_fallback("flight prices", api_key="tvly-key")

        assert provider == "tavily"
        assert [r["url"] for r in results] == ["https://a.example/1", "https://b.example/2"]

    def test_tavily_request_uses_bearer_auth_and_basic_depth(self):
        module = _module()

        with patch("modules.search.requests") as mock_requests:
            mock_requests.post.return_value = _tavily_response()
            module.search_with_fallback("q", api_key="tvly-key")

        _, kwargs = mock_requests.post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer tvly-key"
        assert kwargs["json"]["search_depth"] == "basic"
        assert kwargs["json"]["include_raw_content"] is False
        assert kwargs["json"]["max_results"] == 5

    def test_without_a_key_searxng_is_used(self):
        module = _module()
        searxng_results = [{"title": "t", "url": "u", "content": "c"}]

        with patch.object(module, "search", return_value=searxng_results) as searxng:
            results, provider = module.search_with_fallback("q", api_key=None)

        assert provider == "searxng"
        assert results == searxng_results
        searxng.assert_called_once()


@pytest.mark.unit
class TestTavilyFallback:
    @pytest.mark.parametrize(
        "failure",
        [
            {"status_code": 401},
            {"status_code": 429},
            {"status_code": 500},
            {"timeout": True},
            {"connection_error": True},
            {"empty": True},
        ],
    )
    def test_every_tavily_failure_falls_back_to_searxng(self, failure):
        module = _module()
        searxng_results = [{"title": "t", "url": "u", "content": "c"}]

        with patch("modules.search.requests") as mock_requests:
            if failure.get("timeout"):
                mock_requests.post.side_effect = real_requests.Timeout()
            elif failure.get("connection_error"):
                mock_requests.post.side_effect = real_requests.ConnectionError()
            else:
                payload = {"results": []} if failure.get("empty") else None
                mock_requests.post.return_value = _tavily_response(payload, failure.get("status_code", 200))
            with patch.object(module, "search", return_value=searxng_results) as searxng:
                results, provider = module.search_with_fallback("q", api_key="tvly-key")

        assert provider == "searxng"
        assert results == searxng_results
        searxng.assert_called_once()

    def test_disabled_by_config_never_calls_tavily(self):
        module = _module(TAVILY_ENABLED=False)

        with (
            patch("modules.search.requests") as mock_requests,
            patch.object(module, "search", return_value=[{"title": "t", "url": "u", "content": "c"}]),
        ):
            _, provider = module.search_with_fallback("q", api_key="tvly-key")

        assert provider == "searxng"
        mock_requests.post.assert_not_called()

    def test_both_providers_failing_returns_empty(self):
        module = _module(TAVILY_ENABLED=False)

        with patch("modules.search.requests"), patch.object(module, "search", return_value=[]):
            results, provider = module.search_with_fallback("q", api_key="tvly-key")

        assert (results, provider) == ([], "searxng")


@pytest.mark.unit
class TestTavilyResultNormalization:
    def test_entries_without_url_are_dropped(self):
        module = _module()
        payload = {"results": [{"title": "no url", "content": "x" * 500}, TAVILY_PAYLOAD["results"][0]]}

        with patch("modules.search.requests") as mock_requests:
            mock_requests.post.return_value = _tavily_response(payload)
            results, provider = module.search_with_fallback("q", api_key="tvly-key")

        assert provider == "tavily"
        assert [r["url"] for r in results] == ["https://a.example/1"]

    def test_short_snippets_are_enriched_from_the_page(self):
        module = _module()
        payload = {"results": [{"title": "Thin", "url": "https://a.example/1", "content": "tiny"}]}

        with (
            patch("modules.search.requests") as mock_requests,
            patch.object(module, "_fetch_page_content", return_value="full page text") as fetch,
        ):
            mock_requests.post.return_value = _tavily_response(payload)
            results, _ = module.search_with_fallback("q", api_key="tvly-key")

        assert results[0]["content"] == "full page text"
        fetch.assert_called_once()

    def test_rich_snippets_are_not_refetched(self):
        module = _module()

        with (
            patch("modules.search.requests"),
            patch.object(module, "_fetch_page_content", return_value="unused") as fetch,
        ):
            module.search_with_fallback("q", api_key="tvly-key")

        fetch.assert_not_called()
