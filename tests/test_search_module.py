# tests/test_search_module.py
"""Tests for web search module (SearXNG integration)."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
import requests as real_requests

from modules.search import SearchModule, enhance_query_with_date


@pytest.fixture
def search_module():
    """Create a SearchModule with mocked Flask app."""
    app = MagicMock()
    app.config = {
        "SEARXNG_URL": "http://flai-searxng:8080",
        "SEARXNG_TIMEOUT": 10,
        "SEARXNG_MAX_RESULTS": 5,
    }
    with patch("modules.search.requests") as mock_requests:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_requests.get.return_value = mock_resp
        module = SearchModule(app)
    return module


class TestSearchModuleInit:
    """Test module initialization."""

    def test_init_with_config(self, search_module):
        """Module loads config from app.config."""
        assert search_module.searxng_url == "http://flai-searxng:8080"
        assert search_module.timeout == 10
        assert search_module.max_results == 5

    def test_init_without_url(self):
        """Module is unavailable when SEARXNG_URL is not set."""
        app = MagicMock()
        app.config = {"SEARXNG_URL": None}
        module = SearchModule(app)
        assert module.available is False


class TestCheckAvailability:
    """Test health check."""

    def test_available_on_200(self, search_module):
        """Module is available when health check returns 200."""
        assert search_module.available is True

    def test_unavailable_on_error(self):
        """Module is unavailable when health check fails."""
        app = MagicMock()
        app.config = {
            "SEARXNG_URL": "http://flai-searxng:8080",
            "SEARXNG_TIMEOUT": 10,
            "SEARXNG_MAX_RESULTS": 5,
        }
        with patch("modules.search.requests") as mock_requests:
            mock_requests.get.side_effect = Exception("Connection refused")
            module = SearchModule(app)
            assert module.available is False

    def test_unavailable_on_non_200(self):
        """Module is unavailable when health check returns non-200."""
        app = MagicMock()
        app.config = {
            "SEARXNG_URL": "http://flai-searxng:8080",
            "SEARXNG_TIMEOUT": 10,
            "SEARXNG_MAX_RESULTS": 5,
        }
        with patch("modules.search.requests") as mock_requests:
            mock_resp = MagicMock()
            mock_resp.status_code = 503
            mock_requests.get.return_value = mock_resp
            module = SearchModule(app)
            assert module.available is False


class TestSearch:
    """Test search method."""

    def test_search_returns_results(self, search_module):
        """Search returns formatted results from SearXNG."""
        search_module.available = True
        with patch("modules.search.requests") as mock_requests:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "results": [
                    {"title": "Result 1", "url": "http://example1.com", "content": "Content 1"},
                    {"title": "Result 2", "url": "http://example2.com", "content": "Content 2"},
                    {"title": "Result 3", "url": "http://example3.com", "content": "Content 3"},
                ]
            }
            mock_resp.raise_for_status = MagicMock()
            mock_requests.post.return_value = mock_resp

            results = search_module.search("test query", lang="en")

            assert len(results) == 3
            assert results[0]["title"] == "Result 1"
            assert results[0]["url"] == "http://example1.com"
            assert results[0]["content"] == "Content 1"

    def test_search_respects_max_results(self, search_module):
        """Search limits results to max_results."""
        search_module.available = True
        with patch("modules.search.requests") as mock_requests:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "results": [
                    {"title": f"Result {i}", "url": f"http://example{i}.com", "content": f"Content {i}"}
                    for i in range(10)
                ]
            }
            mock_resp.raise_for_status = MagicMock()
            mock_requests.post.return_value = mock_resp

            results = search_module.search("test query", lang="en", max_results=3)

            assert len(results) == 3

    def test_search_returns_empty_on_error(self, search_module):
        """Search returns empty list on exception."""
        search_module.available = True
        with patch("modules.search.requests") as mock_requests:
            mock_requests.post.side_effect = real_requests.Timeout("Timeout")

            results = search_module.search("test query", lang="en")

            assert results == []

    def test_search_returns_empty_when_unavailable(self, search_module):
        """Search returns empty list when module is unavailable."""
        search_module.available = False

        results = search_module.search("test query", lang="en")

        assert results == []

    def test_search_tries_reconnect(self, search_module):
        """Search tries to reconnect when not available."""
        search_module.available = False
        with patch("modules.search.requests") as mock_requests:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_requests.get.return_value = mock_resp

            mock_search_resp = MagicMock()
            mock_search_resp.json.return_value = {"results": []}
            mock_search_resp.raise_for_status = MagicMock()
            mock_requests.post.return_value = mock_search_resp

            results = search_module.search("test query", lang="en")

            assert results == []
            assert search_module.available is True

    def test_search_sends_correct_params(self, search_module):
        """Search sends correct POST parameters to SearXNG."""
        search_module.available = True
        with patch("modules.search.requests") as mock_requests:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"results": []}
            mock_resp.raise_for_status = MagicMock()
            mock_requests.post.return_value = mock_resp

            search_module.search("погода в Москве", lang="ru")

            mock_requests.post.assert_called_once_with(
                "http://flai-searxng:8080/search",
                data={"q": "погода в Москве", "format": "json", "language": "ru"},
                timeout=10,
            )


class TestEnhanceQueryWithDate:
    """Test relative date normalization in search queries."""

    def test_ru_yesterday(self):
        """'вчера' gets an absolute date appended."""
        now = datetime(2026, 9, 15, 12, 0)
        q = enhance_query_with_date("Какие ИТ новости были вчера в мире?", lang="ru", now=now)
        assert "вчера (14 сентября 2026)" in q

    def test_ru_today(self):
        """'сегодня' gets today's date appended."""
        now = datetime(2026, 9, 15, 12, 0)
        q = enhance_query_with_date("Какие новости произошли сегодня?", lang="ru", now=now)
        assert "сегодня (15 сентября 2026)" in q

    def test_ru_day_before_yesterday(self):
        """'позавчера' gets the date two days back."""
        now = datetime(2026, 9, 15, 12, 0)
        q = enhance_query_with_date("что случилось позавчера", lang="ru", now=now)
        assert "позавчера (13 сентября 2026)" in q

    def test_ru_month_boundary(self):
        """'вчера' on the 1st resolves to the last day of the previous month."""
        now = datetime(2026, 9, 1, 12, 0)
        q = enhance_query_with_date("новости вчера", lang="ru", now=now)
        assert "вчера (31 августа 2026)" in q

    def test_en_yesterday(self):
        """'yesterday' gets an absolute date appended in English format."""
        now = datetime(2026, 9, 15, 12, 0)
        q = enhance_query_with_date("What IT news happened yesterday?", lang="en", now=now)
        assert "yesterday (September 14, 2026)" in q

    def test_no_relative_date_unchanged(self):
        """Queries without relative date words are returned unchanged."""
        now = datetime(2026, 9, 15, 12, 0)
        q = enhance_query_with_date("погода в Москве", lang="ru", now=now)
        assert q == "погода в Москве"

    def test_search_sends_enhanced_query(self, search_module):
        """search() sends the date-enhanced query to SearXNG."""
        search_module.available = True

        class _FixedDatetime(datetime):  # type: ignore[type-hint]
            @classmethod
            def now(cls, tz=None):  # noqa: ARG003
                return datetime(2026, 9, 15, 12, 0)

        with patch("modules.search.requests") as mock_requests, patch("modules.search.datetime", _FixedDatetime):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"results": []}
            mock_resp.raise_for_status = MagicMock()
            mock_requests.post.return_value = mock_resp

            search_module.search("Какие ИТ новости были вчера в мире?", lang="ru")

            call_args = mock_requests.post.call_args
            sent_q = call_args.kwargs["data"]["q"]
            assert "вчера (14 сентября 2026)" in sent_q


class TestFormatResultsContext:
    """Test format_results_context method."""

    def test_format_empty_results(self, search_module):
        """Empty results produce empty string."""
        result = search_module.format_results_context([], lang="ru")
        assert result == ""

    def test_format_single_result(self, search_module):
        """Single result is formatted correctly."""
        results = [{"title": "Title", "url": "http://example.com", "content": "Content"}]
        result = search_module.format_results_context(results, lang="ru")
        assert "Title" in result
        assert "http://example.com" in result
        assert "Content" in result

    def test_format_multiple_results(self, search_module):
        """Multiple results are separated by double newlines."""
        results = [
            {"title": "Title 1", "url": "http://example1.com", "content": "Content 1"},
            {"title": "Title 2", "url": "http://example2.com", "content": "Content 2"},
        ]
        result = search_module.format_results_context(results, lang="ru")
        assert "\n\n" in result
        assert "Title 1" in result
        assert "Title 2" in result

    def test_format_includes_source_label(self, search_module):
        """Formatted results include source label."""
        results = [{"title": "Title", "url": "http://example.com", "content": "Content"}]
        result = search_module.format_results_context(results, lang="ru")
        # Source label is translated, but should contain the result number
        assert "1" in result
