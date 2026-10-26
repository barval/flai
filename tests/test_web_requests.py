# tests/test_web_requests.py
"""Accounting for every outbound internet request.

The Users tab shows a monthly counter, the Monitoring tab charts the same rows
per range, and the rows are erased with the account (GDPR) and pruned with the
monitoring archive.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from app.web_requests import KIND_PAGE, KIND_SEARCH, record_request

pytestmark = pytest.mark.requires_db


@pytest.fixture
def admin_client(client, test_app):
    with test_app.app_context():
        from app.userdb import create_user, get_user_by_login, update_password

        if get_user_by_login("admin"):
            update_password("admin", "adminpass")
        else:
            create_user("admin", "adminpass", "Admin User", is_admin=True)
        if not get_user_by_login("requser"):
            create_user(login="requser", password="pw-requser-123456", name="Req User")

    client.post("/login", data={"login": "admin", "password": "adminpass"})
    return client


def _reset() -> None:
    """Clear the request log for the fixture users (the DB is shared across tests)."""
    from app.database import get_db

    with get_db() as conn:
        conn.cursor().execute("DELETE FROM web_requests WHERE user_id IN ('requser', 'reqgone')")


def _count_rows(login: str) -> int:
    from app.database import get_db

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COALESCE(SUM(request_count), 0) AS n FROM web_requests WHERE user_id = %s", (login,))
        return int(cursor.fetchone()["n"])


def test_record_request_stores_a_batch_count(test_app):
    with test_app.app_context():
        _reset()
        record_request("requser", KIND_SEARCH, "tavily")
        record_request("requser", "crawl", "crawl4ai", count=4)

        assert _count_rows("requser") == 5


def test_record_request_without_a_user_is_a_noop(test_app):
    with test_app.app_context():
        record_request(None, KIND_SEARCH, "searxng")
        record_request("", KIND_SEARCH, "searxng")


def test_monitoring_chart_counts_internet_requests(test_app):
    from app.monitoring.queries import build_payload
    from app.monitoring.ranges import bucket_starts

    now = datetime.now(UTC).replace(microsecond=0)
    with test_app.app_context():
        import pytz

        _reset()
        test_app.config["TIMEZONE"] = pytz.UTC
        record_request("requser", KIND_SEARCH, "tavily")
        record_request("requser", KIND_PAGE, "trafilatura", count=3)

        payload = build_payload(["requser"], "24h", now=now)

    assert len(payload["series"]["web_requests"]) == len(bucket_starts(now, "24h"))
    assert sum(payload["series"]["web_requests"]) == 4
    assert payload["totals"]["web_requests"] == 4


def test_users_tab_reports_this_month_requests(admin_client):
    with admin_client.application.app_context():
        _reset()
        record_request("requser", KIND_SEARCH, "searxng")

    rows = {row["login"]: row for row in admin_client.get("/admin/api/users").get_json()}

    assert rows["requser"]["web_requests_month"] == 1


def test_full_backup_includes_the_request_log():
    from app.routes.backups import FULL_TABLES

    assert "web_requests" in FULL_TABLES


def test_deleting_the_account_erases_the_request_log(test_app):
    from app.userdb import delete_user

    with test_app.app_context():
        from app.userdb import create_user, get_user_by_login

        if not get_user_by_login("reqgone"):
            create_user(login="reqgone", password="pw-reqgone-123456", name="Req Gone")
        _reset()
        record_request("reqgone", KIND_SEARCH, "tavily")

        delete_user("reqgone")

        assert _count_rows("reqgone") == 0


@pytest.mark.unit
def test_searxng_search_is_recorded():
    from tests.test_tavily_search import _module

    module = _module()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"results": []}

    with (
        patch("modules.search.record_request") as record,
        patch("modules.search.requests") as mock_requests,
    ):
        mock_requests.post.return_value = response
        module.search("query", user_id="u1")

    record.assert_called_once_with("u1", "search", "searxng")


@pytest.mark.unit
def test_page_download_is_recorded():
    from tests.test_tavily_search import _module

    module = _module()
    response = MagicMock()
    response.status_code = 200
    response.content = b"<html>text</html>"

    with (
        patch("modules.search.record_request") as record,
        patch("modules.search.requests") as mock_requests,
        patch("modules.search.trafilatura") as trafilatura,
    ):
        mock_requests.get.return_value = response
        trafilatura.extract.return_value = "text"
        module._fetch_page_content("https://a.example/1", user_id="u1")

    record.assert_called_once_with("u1", "page", "trafilatura")


@pytest.mark.unit
def test_crawler_page_is_recorded():
    import modules.crawler as crawler_module

    app = MagicMock()
    app.config = {"CRAWLER_URL": "http://flai-crawler:11235", "CRAWL_PAGE_TIMEOUT_S": 30, "CRAWL_MAX_PAGE_CHARS": 1000}
    module = crawler_module.CrawlerModule(app)

    response = MagicMock()
    response.json.return_value = {"markdown": "hello"}

    with (
        patch.object(crawler_module, "validate_url", side_effect=lambda url: url),
        patch.object(crawler_module, "record_request") as record,
        patch.object(crawler_module.requests, "post", return_value=response),
    ):
        module.read_page("https://a.example/1", user_id="u1")

    record.assert_called_once_with("u1", "page", "crawl4ai")


@pytest.mark.unit
def test_users_tab_orders_the_request_column_before_tavily():
    """The header and the rendered row must agree: requests, then Tavily."""
    from pathlib import Path

    html = Path("app/templates/admin.html").read_text(encoding="utf-8")
    assert html.index("web_requests_month") < html.index("{{ _('Tavily API') }}")

    js = Path("app/static/js/admin.js").read_text(encoding="utf-8")
    assert js.index("webRequestsCell") < js.index("const tavilyCell = document")
