"""Tests for the Tavily / FLAI API columns in the admin Users tab."""

import json
from unittest.mock import patch

import pytest

from app.api_tokens import create_api_token, revoke_api_token
from app.tavily_keys import set_tavily_key


@pytest.fixture
def admin_client(client, test_app):
    with test_app.app_context():
        from app.userdb import create_user, get_user_by_login, update_password

        if get_user_by_login("admin"):
            update_password("admin", "adminpass")
        else:
            create_user("admin", "adminpass", "Admin User", is_admin=True)
    client.post("/login", data={"login": "admin", "password": "adminpass"})
    return client


@pytest.fixture
def member(test_app):
    with test_app.app_context():
        from app.userdb import create_user, get_user_by_login

        if not get_user_by_login("member1"):
            create_user(login="member1", password="pw-member1-123456", name="Member One")
    return "member1"


class _FakeRedis:
    """Minimal Redis stand-in for the quota cache (the app-wide mock is a MagicMock)."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value

    def close(self):
        pass


@pytest.mark.unit
class TestAdminUserColumns:
    def test_endpoint_requires_admin(self, client, member):
        with client.session_transaction() as browser_session:
            browser_session["login"] = member
        assert client.get("/admin/api/users").status_code == 403

    def test_api_key_count_counts_only_active_keys(self, admin_client, member):
        _first, record = create_api_token(member, name="one")
        create_api_token(member, name="two")
        revoke_api_token(member, record["id"])

        rows = {row["login"]: row for row in admin_client.get("/admin/api/users").get_json()}

        assert rows[member]["api_keys_count"] == 1

    def test_plaintext_tavily_key_never_leaves_the_server(self, admin_client, member):
        set_tavily_key(member, "tvly-abcdefgh12345678")

        payload = admin_client.get("/admin/api/users").get_data(as_text=True)
        rows = {row["login"]: row for row in admin_client.get("/admin/api/users").get_json()}

        assert "tvly-abcdefgh12345678" not in payload
        assert rows[member]["has_tavily_key"] is True
        assert "tavily_api_key" not in rows[member]

    def test_user_without_a_key_is_flagged(self, admin_client, member):
        rows = {row["login"]: row for row in admin_client.get("/admin/api/users").get_json()}

        assert rows[member]["has_tavily_key"] is False


@pytest.mark.unit
class TestAdminTavilyUsage:
    def test_no_key_is_reported_without_any_outbound_call(self, admin_client, member):
        with patch("app.tavily_keys.fetch_tavily_usage") as usage:
            body = admin_client.get("/admin/api/users/tavily-usage").get_json()

        assert body[member]["status"] == "no_key"
        usage.assert_not_called()

    def test_remaining_credits_are_reported(self, admin_client, member):
        set_tavily_key(member, "tvly-abcdefgh12345678")

        with patch("app.tavily_keys.fetch_tavily_usage", return_value={"status": "ok", "remaining": 880}):
            body = admin_client.get("/admin/api/users/tavily-usage").get_json()

        assert body[member]["remaining"] == 880
        assert body[member]["has_key"] is True

    def test_second_call_is_served_from_the_cache(self, admin_client, member):
        set_tavily_key(member, "tvly-abcdefgh12345678")
        redis_client = _FakeRedis()

        with (
            patch("app.api_bridge.get_redis_client", return_value=redis_client),
            patch("app.tavily_keys.fetch_tavily_usage", return_value={"status": "ok", "remaining": 880}) as usage,
        ):
            admin_client.get("/admin/api/users/tavily-usage")
            assert usage.call_count == 1
            redis_client.store["admin:tavily_usage:member1"] = json.dumps({"status": "ok", "remaining": 42})
            body = admin_client.get("/admin/api/users/tavily-usage").get_json()

        assert body[member]["remaining"] == 42
        assert usage.call_count == 1

    def test_response_never_contains_key_material(self, admin_client, member):
        set_tavily_key(member, "tvly-abcdefgh12345678")

        with patch("app.tavily_keys.fetch_tavily_usage", return_value={"status": "ok", "remaining": 1}):
            payload = admin_client.get("/admin/api/users/tavily-usage").get_data(as_text=True)

        assert "tvly-abcdefgh12345678" not in payload
