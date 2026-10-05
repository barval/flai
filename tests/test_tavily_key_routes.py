"""Tests for the per-user Tavily key endpoints behind the profile popup."""

from unittest.mock import patch

import pytest

from app.userdb import create_user


@pytest.fixture
def tavily_client(client, test_app):
    create_user(login="tavilyowner", password="pw-tavilyowner-123", name="Tavily Owner")
    with client.session_transaction() as browser_session:
        browser_session["login"] = "tavilyowner"
        browser_session["user_id"] = "tavilyowner"
        browser_session["name"] = "Tavily Owner"
    return client


def _usage(status="ok", remaining=880):
    return {"status": status, "plan": "Free", "limit": 1000, "used": 120, "remaining": remaining}


@pytest.mark.unit
class TestTavilyKeyRoutes:
    def test_endpoints_require_a_web_login(self, client):
        assert client.get("/api-keys/tavily").status_code == 401
        assert client.post("/api-keys/tavily", json={"api_key": "tvly-abcdefgh12345678"}).status_code == 401
        assert client.delete("/api-keys/tavily").status_code == 401

    def test_state_without_a_key_needs_no_outbound_call(self, tavily_client):
        with patch("app.routes.api_keys.fetch_tavily_usage") as usage:
            body = tavily_client.get("/api-keys/tavily").get_json()

        assert body == {"has_key": False, "masked_key": "", "status": "no_key"}
        usage.assert_not_called()

    def test_state_with_a_key_never_returns_the_key(self, tavily_client):
        with patch("app.routes.api_keys.fetch_tavily_usage", return_value=_usage()):
            created = tavily_client.post("/api-keys/tavily", json={"api_key": "tvly-abcdefgh12345678"})

        assert created.status_code == 201
        body = created.get_json()
        assert body["has_key"] is True
        assert body["masked_key"] == "tvly-…5678"
        assert "tvly-abcdefgh12345678" not in str(body)
        assert body["remaining"] == 880

        listed = tavily_client.get("/api-keys/tavily").get_json()
        assert listed["masked_key"] == "tvly-…5678"
        assert "tvly-abcdefgh12345678" not in str(listed)

    def test_wrong_shape_is_rejected_without_a_lookup(self, tavily_client):
        with patch("app.routes.api_keys.fetch_tavily_usage") as usage:
            response = tavily_client.post("/api-keys/tavily", json={"api_key": "not-a-tavily-key"})

        assert response.status_code == 400
        assert response.get_json()["error"].startswith("⚠️ ")
        usage.assert_not_called()
        assert tavily_client.get("/api-keys/tavily").get_json()["has_key"] is False

    def test_key_rejected_by_tavily_is_not_stored(self, tavily_client):
        with patch("app.routes.api_keys.fetch_tavily_usage", return_value=_usage(status="invalid", remaining=None)):
            response = tavily_client.post("/api-keys/tavily", json={"api_key": "tvly-abcdefgh12345678"})

        assert response.status_code == 400
        assert tavily_client.get("/api-keys/tavily").get_json()["has_key"] is False

    def test_lookup_failure_still_stores_the_key(self, tavily_client):
        with patch("app.routes.api_keys.fetch_tavily_usage", return_value=_usage(status="unavailable", remaining=None)):
            response = tavily_client.post("/api-keys/tavily", json={"api_key": "tvly-abcdefgh12345678"})

        assert response.status_code == 201
        assert response.get_json()["status"] == "unavailable"
        assert tavily_client.get("/api-keys/tavily").get_json()["has_key"] is True

    def test_second_key_is_rejected_without_replacing_the_first(self, tavily_client):
        with patch("app.routes.api_keys.fetch_tavily_usage", return_value=_usage()):
            tavily_client.post("/api-keys/tavily", json={"api_key": "tvly-abcdefgh12345678"})

        with patch("app.routes.api_keys.fetch_tavily_usage") as usage:
            response = tavily_client.post("/api-keys/tavily", json={"api_key": "tvly-zyxwvu9876543210"})

        assert response.status_code == 409
        assert response.get_json()["error"].startswith("⚠️ ")
        usage.assert_not_called()  # No second credit spent on /usage either.
        listed = tavily_client.get("/api-keys/tavily").get_json()
        assert listed["masked_key"] == "tvly-…5678"

    def test_delete_clears_the_key(self, tavily_client):
        with patch("app.routes.api_keys.fetch_tavily_usage", return_value=_usage()):
            tavily_client.post("/api-keys/tavily", json={"api_key": "tvly-abcdefgh12345678"})

        removed = tavily_client.delete("/api-keys/tavily")

        assert removed.status_code == 200
        assert removed.get_json()["status"] == "no_key"
        assert tavily_client.get("/api-keys/tavily").get_json()["has_key"] is False

    def test_globally_disabled_tavily_reports_unavailable(self, tavily_client, test_app):
        test_app.config["TAVILY_ENABLED"] = False
        with patch("app.routes.api_keys.fetch_tavily_usage", return_value=_usage()):
            tavily_client.post("/api-keys/tavily", json={"api_key": "tvly-abcdefgh12345678"})

        with patch("app.routes.api_keys.fetch_tavily_usage") as usage:
            body = tavily_client.get("/api-keys/tavily").get_json()

        assert body["status"] == "unavailable"
        usage.assert_not_called()
