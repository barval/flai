"""Tests for per-user API token lifecycle."""

import pytest

from app import api_tokens
from app.userdb import create_user, update_user


@pytest.fixture
def token_owner(test_app):
    create_user(login="apiowner", password="pw-apiowner-123", name="API Owner")
    return "apiowner"


@pytest.mark.unit
class TestCreateApiToken:
    def test_create_returns_a_prefixed_secret_and_metadata(self, test_app, token_owner):
        token, record = api_tokens.create_api_token(token_owner, name="home-assistant")

        assert token.startswith("flai-")
        assert record["login"] == token_owner
        assert record["name"] == "home-assistant"
        assert record["token_prefix"] == token[:11]

    def test_database_persists_only_the_digest(self, test_app, token_owner):
        token, _ = api_tokens.create_api_token(token_owner)

        with api_tokens.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT token_hash FROM api_tokens WHERE login = %s", (token_owner,))
            stored_digest = cursor.fetchone()["token_hash"]

        assert stored_digest == api_tokens.hash_token(token)
        assert stored_digest != token

    def test_each_created_token_is_unique(self, test_app, token_owner):
        first, _ = api_tokens.create_api_token(token_owner)
        second, _ = api_tokens.create_api_token(token_owner)

        assert first != second


@pytest.mark.unit
class TestVerifyApiToken:
    def test_valid_token_resolves_its_active_owner(self, test_app, token_owner):
        token, _ = api_tokens.create_api_token(token_owner)

        result = api_tokens.verify_api_token(token)

        assert result["user"]["login"] == token_owner
        assert isinstance(result["token_id"], int)

    def test_unknown_or_malformed_token_is_rejected(self, test_app, token_owner):
        api_tokens.create_api_token(token_owner)

        assert api_tokens.verify_api_token("flai-unknown-secret") is None
        assert api_tokens.verify_api_token("") is None
        assert api_tokens.verify_api_token("not-a-token") is None

    def test_revoked_token_is_rejected(self, test_app, token_owner):
        token, record = api_tokens.create_api_token(token_owner)

        assert api_tokens.revoke_api_token(token_owner, record["id"]) is True
        assert api_tokens.verify_api_token(token) is None

    def test_inactive_owner_token_is_rejected(self, test_app, token_owner):
        token, _ = api_tokens.create_api_token(token_owner)
        update_user(token_owner, is_active=False)

        assert api_tokens.verify_api_token(token) is None


@pytest.mark.unit
class TestListAndRevokeApiTokens:
    def test_list_is_owner_scoped_and_never_returns_digest(self, test_app, token_owner):
        create_user(login="otherapi", password="pw-otherapi-123", name="Other API")
        api_tokens.create_api_token(token_owner, name="ha")

        records = api_tokens.list_api_tokens(token_owner)

        assert len(records) == 1
        assert records[0]["name"] == "ha"
        assert "token_hash" not in records[0]
        assert api_tokens.list_api_tokens("otherapi") == []

    def test_other_user_cannot_revoke_token(self, test_app, token_owner):
        create_user(login="otherapi", password="pw-otherapi-123", name="Other API")
        token, record = api_tokens.create_api_token(token_owner)

        assert api_tokens.revoke_api_token("otherapi", record["id"]) is False
        assert api_tokens.verify_api_token(token) is not None

    def test_unknown_token_id_cannot_be_revoked(self, test_app, token_owner):
        assert api_tokens.revoke_api_token(token_owner, 9999) is False


@pytest.mark.unit
class TestTouchApiToken:
    def test_touch_updates_last_used_timestamp(self, test_app, token_owner):
        _, record = api_tokens.create_api_token(token_owner)

        api_tokens.touch_api_token(record["id"])

        assert api_tokens.list_api_tokens(token_owner)[0]["last_used_at"] is not None
