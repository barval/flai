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


@pytest.mark.unit
class TestQueueSearchUsesTavilyFirst:
    @staticmethod
    def _queue(monkeypatch, tavily_results, searxng_results):
        from app.queue import RedisRequestQueue

        monkeypatch.setattr("app.tavily_keys.get_tavily_key", lambda login: "tvly-key")

        app = MagicMock()
        app.config = {"REDIS_URL": "redis://localhost:6379/0", "SECRET_KEY": "test-secret-key"}
        app.logger = MagicMock()
        base = MagicMock()
        base.get_search_context_limit.return_value = 10000
        base._.side_effect = lambda msg, lang="ru": msg
        search = MagicMock()
        search.available = True
        search.search_with_fallback.side_effect = lambda q, lang="ru", **kw: (
            (tavily_results, "tavily") if kw.get("api_key") else (searxng_results, "searxng")
        )
        search.search.return_value = searxng_results
        search.format_results_context.side_effect = lambda res, **kw: "x" * 4000 if res else ""
        app.modules = {"base": base, "search": search}

        queue = RedisRequestQueue.__new__(RedisRequestQueue)
        queue.app = app
        queue._publish_stream_event = MagicMock()
        queue._build_error_response = MagicMock(return_value={"error": "⚠️ boom"})
        queue._requeue_reasoning_task = MagicMock(return_value={"status": "queued"})
        return queue, search

    def test_thin_tavily_context_is_upgraded_from_searxng_without_a_second_tavily_call(self, monkeypatch):
        queue, search = self._queue(
            monkeypatch,
            tavily_results=[{"title": "t", "url": "u", "content": "c"}],
            searxng_results=[{"title": "t", "url": "u", "content": "c" * 900}],
        )

        queue._process_search_task("q", "s1", "u1", "ru", "neutral")

        search.search_with_fallback.assert_called_once()
        assert search.search_with_fallback.call_args.kwargs["api_key"] == "tvly-key"
        search.search.assert_called()
        queue._requeue_reasoning_task.assert_called_once()

    def test_rich_tavily_context_is_not_supplemented(self, monkeypatch):
        rich = [{"title": f"t{i}", "url": f"u{i}", "content": "c" * 3000} for i in range(6)]
        queue, search = self._queue(monkeypatch, tavily_results=rich, searxng_results=[])

        queue._process_search_task("q", "s1", "u1", "ru", "neutral")

        search.search.assert_not_called()

    def test_search_without_a_user_key_never_reaches_tavily(self, monkeypatch):
        queue, search = self._queue(monkeypatch, tavily_results=[], searxng_results=[])
        monkeypatch.setattr("app.tavily_keys.get_tavily_key", lambda login: None)

        queue._process_search_task("q", "s1", "u1", "ru", "neutral")

        assert search.search_with_fallback.call_args.kwargs["api_key"] is None


@pytest.mark.unit
class TestToolWebSearchUsesTavilyFirst:
    """The chat tool must reach the provider policy too, not SearXNG directly."""

    @staticmethod
    def _ctx(monkeypatch, results, available=True):
        monkeypatch.setattr("app.tavily_keys.get_tavily_key", lambda login: "tvly-key")

        app = MagicMock()
        app.config = {"SEARXNG_MAX_RESULTS": 7}
        search = MagicMock()
        search.available = available
        search.search_with_fallback.return_value = (results, "tavily")
        search.format_results_context.return_value = "formatted context"
        app.modules = {"search": search}
        return {"app": app, "user_id": "u1"}, search

    def test_tool_search_passes_the_user_key_to_the_policy(self, monkeypatch):
        from app.tools import _exec_web_search

        ctx, search = self._ctx(monkeypatch, [{"title": "t", "url": "u", "content": "c"}])

        assert _exec_web_search(ctx, "q") == "formatted context"
        assert search.search_with_fallback.call_args.kwargs["api_key"] == "tvly-key"
        search.search.assert_not_called()

    def test_tool_search_survives_searxng_being_down(self, monkeypatch):
        """A Tavily-only deployment must still get search results from the tool."""
        from app.tools import _exec_web_search

        ctx, search = self._ctx(monkeypatch, [{"title": "t", "url": "u", "content": "c"}], available=False)

        assert _exec_web_search(ctx, "q") == "formatted context"
        assert search.search_with_fallback.call_args.kwargs["api_key"] == "tvly-key"
        search.search.assert_not_called()


@pytest.mark.unit
class TestRlmWebFetchUsesTavilyFirst:
    """The RLM deep-analysis web_fetch tool must reach the provider policy too."""

    def test_broker_passes_the_user_key_to_the_policy(self, monkeypatch):
        from modules.rlm import RlmModule, _RlmBroker

        monkeypatch.setattr("app.tavily_keys.get_tavily_key", lambda login: "tvly-key")

        app = MagicMock()
        search = MagicMock()
        search.available = False  # SearXNG down: only Tavily can answer
        search.search_with_fallback.return_value = ([{"title": "t", "url": "u", "content": "page"}], "tavily")
        app.modules = {"search": search}

        module = RlmModule(app)
        broker = _RlmBroker(module, "en", 1024, 5, user_id="u1")

        out = module.broker_web_fetch("q", broker)

        assert out.startswith("[t](u)")
        assert search.search_with_fallback.call_args.kwargs["api_key"] == "tvly-key"
        search.search.assert_not_called()
