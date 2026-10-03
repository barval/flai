"""Tests for the Tavily-first web search provider and its SearXNG fallback."""

import logging
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

    def test_tavily_request_respects_its_own_result_ceiling(self):
        """TAVILY_MAX_RESULTS caps the Tavily request, not SEARXNG_MAX_RESULTS."""
        module = _module(SEARXNG_MAX_RESULTS=7, TAVILY_MAX_RESULTS=3)

        with patch("modules.search.requests") as mock_requests:
            mock_requests.post.return_value = _tavily_response()
            module.search_with_fallback("q", api_key="tvly-key")

        _, kwargs = mock_requests.post.call_args
        assert kwargs["json"]["max_results"] == 3

    def test_caller_max_results_wins_when_smaller_than_the_tavily_ceiling(self):
        """An explicit max_results below the ceiling still wins for Tavily."""
        module = _module(SEARXNG_MAX_RESULTS=7, TAVILY_MAX_RESULTS=5)

        with patch("modules.search.requests") as mock_requests:
            mock_requests.post.return_value = _tavily_response()
            module.search_with_fallback("q", api_key="tvly-key", max_results=2)

        _, kwargs = mock_requests.post.call_args
        assert kwargs["json"]["max_results"] == 2

    def test_the_tavily_ceiling_does_not_shrink_the_searxng_fallback(self):
        """SearXNG keeps the caller's limit when Tavily drops out."""
        module = _module(SEARXNG_MAX_RESULTS=7, TAVILY_MAX_RESULTS=3)

        with patch("modules.search.requests") as mock_requests:
            mock_requests.post.return_value = _tavily_response(status_code=401)
            with patch.object(module, "search", return_value=[]) as searxng:
                module.search_with_fallback("q", api_key="tvly-key")

        assert searxng.call_args.kwargs["max_results"] == 7


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

    @pytest.mark.parametrize(
        "payload",
        [
            ["a", "b"],
            "a bare string",
            5,
            {"results": "abc"},
            {"results": 5},
            {"results": ["oops"]},
        ],
        ids=["array-body", "string-body", "number-body", "string-results", "number-results", "non-dict-entry"],
    )
    def test_wrong_typed_payloads_fall_back_instead_of_raising(self, payload):
        """A 200 with an unusable shape degrades to SearXNG rather than raising."""
        module = _module()
        searxng_results = [{"title": "t", "url": "u", "content": "c"}]

        with patch("modules.search.requests") as mock_requests:
            mock_requests.post.return_value = _tavily_response(payload)
            with patch.object(module, "search", return_value=searxng_results):
                results, provider = module.search_with_fallback("q", api_key="tvly-key")

        assert (results, provider) == (searxng_results, "searxng")

    def test_non_json_body_falls_back_to_searxng(self):
        """A 200 whose body cannot be decoded as JSON degrades to SearXNG."""
        module = _module()
        searxng_results = [{"title": "t", "url": "u", "content": "c"}]
        response = MagicMock()
        response.status_code = 200
        response.json.side_effect = ValueError("Expecting value")

        with patch("modules.search.requests") as mock_requests:
            mock_requests.post.return_value = response
            with patch.object(module, "search", return_value=searxng_results):
                results, provider = module.search_with_fallback("q", api_key="tvly-key")

        assert (results, provider) == (searxng_results, "searxng")

    def test_empty_tavily_and_empty_searxng_returns_empty(self):
        """Tavily 200 with zero results plus an empty SearXNG search reports searxng."""
        module = _module()

        with patch("modules.search.requests") as mock_requests:
            mock_requests.post.return_value = _tavily_response({"results": []})
            with patch.object(module, "search", return_value=[]):
                results, provider = module.search_with_fallback("q", api_key="tvly-key")

        assert (results, provider) == ([], "searxng")

    @pytest.mark.parametrize(
        "failure",
        [{"status_code": 401}, {"timeout": True}, {"non_json": True}, {"empty": True}],
        ids=["http-error", "timeout", "non-json", "empty-results"],
    )
    def test_the_api_key_is_never_logged(self, failure, caplog):
        """No log record at any level may carry the Tavily key."""
        module = _module()
        secret = "tvly-SECRET-key"

        caplog.set_level(logging.DEBUG)
        with patch("modules.search.requests") as mock_requests:
            if failure.get("timeout"):
                mock_requests.post.side_effect = real_requests.Timeout()
            else:
                response = MagicMock()
                response.status_code = failure.get("status_code", 200)
                if failure.get("non_json"):
                    response.json.side_effect = ValueError("Expecting value")
                else:
                    response.json.return_value = {"results": []} if failure.get("empty") else TAVILY_PAYLOAD
                mock_requests.post.return_value = response
            with patch.object(module, "search", return_value=[{"title": "t", "url": "u", "content": "c"}]):
                module.search_with_fallback("q", api_key=secret)

        assert caplog.records, "expected the Tavily failure to be logged"
        for record in caplog.records:
            assert secret not in record.getMessage()
            assert secret not in str(record.args)

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
