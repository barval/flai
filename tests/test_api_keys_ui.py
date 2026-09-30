"""Tests for authenticated self-service API key management."""

import re

import pytest

from app.api_tokens import create_api_token, list_api_tokens, verify_api_token
from app.userdb import create_user


@pytest.fixture
def logged_in_client(client, test_app):
    create_user(login="keyowner", password="pw-keyowner-123", name="Key Owner")
    with client.session_transaction() as browser_session:
        browser_session["login"] = "keyowner"
        browser_session["user_id"] = "keyowner"
        browser_session["name"] = "Key Owner"
    return client


@pytest.mark.unit
class TestApiKeyManagementRoutes:
    def test_list_requires_web_login(self, client):
        response = client.get("/api-keys/tokens")

        assert response.status_code == 401
        assert response.get_json()["error"].startswith("⚠️ ")

    def test_create_returns_secret_once_and_list_does_not_return_it(self, logged_in_client):
        created = logged_in_client.post("/api-keys/tokens", json={"name": "n8n"})

        assert created.status_code == 201
        body = created.get_json()
        assert body["token"].startswith("flai-")
        assert body["name"] == "n8n"
        assert "token_hash" not in body

        listed = logged_in_client.get("/api-keys/tokens")
        record = listed.get_json()["tokens"][0]
        assert "token" not in record
        assert "token_hash" not in record
        assert record["token_prefix"] == body["token"][:11]

    def test_revoke_is_owner_scoped(self, logged_in_client, test_app):
        token, record = create_api_token("keyowner", name="n8n")
        create_user(login="otherkey", password="pw-otherkey-123", name="Other")

        with test_app.test_client() as other_client:
            with other_client.session_transaction() as browser_session:
                browser_session["login"] = "otherkey"
            denied = other_client.post(f"/api-keys/tokens/{record['id']}/revoke")

        assert denied.status_code == 404
        assert verify_api_token(token) is not None

        revoked = logged_in_client.post(f"/api-keys/tokens/{record['id']}/revoke")
        assert revoked.status_code == 200
        assert verify_api_token(token) is None

    def test_unknown_key_revoke_is_not_found(self, logged_in_client):
        response = logged_in_client.post("/api-keys/tokens/9999/revoke")

        assert response.status_code == 404
        assert response.get_json()["error"].startswith("⚠️ ")

    def test_chat_header_renders_key_panel_for_regular_user(self, logged_in_client):
        response = logged_in_client.get("/chat")

        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'id="api-keys-panel"' in html
        assert 'id="api-keys-btn"' in html

    def test_key_management_post_requires_csrf_when_enabled(self, logged_in_client, test_app):
        test_app.config["WTF_CSRF_ENABLED"] = True
        response = logged_in_client.post("/api-keys/tokens", json={"name": "without-csrf"})

        assert response.status_code == 400

    def test_key_management_post_accepts_valid_csrf_token(self, logged_in_client, test_app):
        test_app.config["WTF_CSRF_ENABLED"] = True
        page = logged_in_client.get("/chat")
        csrf_match = re.search(r'<meta name="csrf-token" content="([^"]+)"', page.get_data(as_text=True))
        assert csrf_match is not None

        response = logged_in_client.post(
            "/api-keys/tokens",
            json={"name": "with-csrf"},
            headers={"X-CSRFToken": csrf_match.group(1)},
        )

        assert response.status_code == 201
        assert len(list_api_tokens("keyowner")) == 1
