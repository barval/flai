"""Tests for bearer authentication and the public API foundation."""

import pytest

from app.api_tokens import create_api_token, revoke_api_token
from app.userdb import create_user, get_user_by_login


@pytest.fixture
def api_owner(test_app):
    create_user(login="apiowner", password="pw-apiowner-123", name="API Owner", language="en")
    return "apiowner"


@pytest.fixture
def api_token(test_app, api_owner):
    token, _ = create_api_token(api_owner, name="test")
    return token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.unit
class TestApiV1Authentication:
    def test_missing_unknown_and_revoked_tokens_have_same_error(self, client, test_app, api_owner, api_token):
        missing = client.get("/v1/models")
        unknown = client.get("/v1/models", headers=bearer("flai-unknown"))
        token_rows = __import__("app.api_tokens", fromlist=["list_api_tokens"]).list_api_tokens(api_owner)
        revoke_api_token(api_owner, token_rows[0]["id"])
        revoked = client.get("/v1/models", headers=bearer(api_token))

        assert missing.status_code == unknown.status_code == revoked.status_code == 401
        assert missing.get_json() == unknown.get_json() == revoked.get_json()
        assert missing.headers["WWW-Authenticate"] == "Bearer"

    def test_disabled_user_token_is_indistinguishable_from_invalid_token(self, client, test_app, api_owner, api_token):
        user = get_user_by_login(api_owner)
        assert user is not None
        from app.userdb import update_user

        update_user(api_owner, is_active=False)
        disabled = client.get("/v1/models", headers=bearer(api_token))
        unknown = client.get("/v1/models", headers=bearer("flai-unknown"))

        assert disabled.status_code == 401
        assert disabled.get_json() == unknown.get_json()

    def test_valid_token_lists_models_without_setting_cookie(self, client, api_token):
        response = client.get("/v1/models", headers=bearer(api_token))

        assert response.status_code == 200
        assert "Set-Cookie" not in response.headers
        body = response.get_json()
        assert body["object"] == "list"
        model_ids = [model["id"] for model in body["data"]]
        assert "flai-chat" in model_ids
        assert len(model_ids) == len(set(model_ids))
        assert all(model["owned_by"] == "flai" for model in body["data"])

    def test_api_identity_is_request_local_and_does_not_replace_web_session(self, client, test_app, api_token):
        with client.session_transaction() as browser_session:
            browser_session["login"] = "webuser"
            browser_session["theme"] = "dark"

        response = client.get("/v1/flai/me", headers=bearer(api_token))
        assert response.status_code == 200
        assert "Set-Cookie" not in response.headers

        with client.session_transaction() as browser_session:
            assert browser_session["login"] == "webuser"
            assert browser_session["theme"] == "dark"

        assert response.get_json()["login"] == "apiowner"
        assert response.get_json()["language"] == "en"

    def test_disabled_api_returns_openai_shaped_error(self, client, test_app, api_token):
        test_app.config["API_ENABLED"] = False

        response = client.get("/v1/models", headers=bearer(api_token))

        assert response.status_code == 503
        assert response.get_json()["error"]["code"] == "api_disabled"
        assert response.get_json()["error"]["message"].startswith("⚠️ ")

    def test_unauthorized_response_has_openai_error_shape(self, client):
        response = client.get("/v1/models")
        body = response.get_json()

        assert set(body["error"]) == {"message", "type", "param", "code"}
        assert body["error"]["code"] == "invalid_api_key"
        assert body["error"]["message"].startswith("⚠️ ")

    def test_csrf_exempts_v1_blueprint_only(self, test_app):
        from app import csrf

        assert test_app.blueprints["api_v1"] in csrf._exempt_blueprints
        assert test_app.blueprints["messages"] not in csrf._exempt_blueprints

    def test_me_reports_identity_and_unsupported_capabilities(self, client, api_token):
        response = client.get("/v1/flai/me", headers=bearer(api_token))

        assert response.status_code == 200
        body = response.get_json()
        assert body["login"] == "apiowner"
        assert body["language"] == "en"
        assert body["capabilities"]["chat_completions"] is True
        assert body["capabilities"]["streaming"] is True
        assert body["capabilities"]["embeddings"] is True
        assert body["capabilities"]["tools"] is False
        assert body["capabilities"]["response_format_json_schema"] is False
