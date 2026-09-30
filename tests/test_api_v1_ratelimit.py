"""Rate-limit contract for the expensive /v1 endpoints.

`GET /v1/flai/me` advertises a rate limit, so one has to exist. Only the
endpoints that cost GPU time are limited: the introspection endpoints stay
free, and the budget is per API key owner so one noisy client cannot spend
another user's quota.
"""

import pytest

from app import limiter
from app.api_tokens import create_api_token
from app.userdb import create_user


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def limited(test_app, monkeypatch):
    """Enable the limiter with a tiny budget and a stubbed bridge."""
    test_app.config["RATELIMIT_ENABLED"] = True
    test_app.config["API_RATE_LIMIT"] = "2 per minute"
    limiter.reset()

    create_user(login="apiowner", password="pw-apiowner-123", name="API Owner", language="en")
    token, _ = create_api_token("apiowner", name="test")

    monkeypatch.setattr(
        "app.routes.api_v1.resolve_api_session",
        lambda api_user, session_id=None, client_user=None: "session-1",
    )
    monkeypatch.setattr(
        "app.routes.api_v1.enqueue_chat",
        lambda api_user, session_id, text, images=None: (
            "task-1",
            {"position": 1, "estimated_seconds": 3, "queue_type": "fast"},
        ),
    )
    monkeypatch.setattr(
        "app.routes.api_v1.wait_for_result",
        lambda login, task_id, timeout_s: {
            "response": "OK",
            "session_id": "session-1",
            "model_used": "reasoning-model",
            "is_error": False,
            "message_id": 12,
            "prompt_tokens": 5,
            "completion_tokens": 2,
        },
    )
    monkeypatch.setattr(
        "app.routes.api_v1.enqueue_embeddings",
        lambda api_user, texts: "task-9",
    )
    monkeypatch.setattr(
        "app.routes.api_v1.wait_for_result",
        lambda login, task_id, timeout_s: {"embeddings": [[0.1, 0.2]], "model": "embedding"},
    )
    yield token
    limiter.reset()


def chat(client, token, text="hi"):
    return client.post(
        "/v1/chat/completions",
        json={"model": "flai-chat", "messages": [{"role": "user", "content": text}]},
        headers=bearer(token),
    )


def embed(client, token):
    return client.post("/v1/embeddings", json={"model": "flai-embeddings", "input": "hi"}, headers=bearer(token))


@pytest.mark.unit
class TestChatRateLimit:
    def test_allows_up_to_the_budget_then_returns_429(self, test_app, limited):
        client = test_app.test_client()
        assert chat(client, limited).status_code == 200
        assert chat(client, limited).status_code == 200

        blocked = chat(client, limited)
        assert blocked.status_code == 429
        error = blocked.get_json()["error"]
        assert error["type"] == "rate_limit_error"
        assert error["code"] == "rate_limit_exceeded"
        assert error["param"] is None
        assert error["message"].startswith("⚠️ ")

    def test_429_is_localized_for_the_key_owner(self, test_app, limited):
        test_app.config["API_RATE_LIMIT"] = "1 per minute"
        create_user(login="ruowner", password="pw-ruowner-12345", name="RU", language="ru")
        token, _ = create_api_token("ruowner", name="test")
        client = test_app.test_client()
        payload = {"messages": [{"role": "user", "content": "hi"}]}

        assert client.post("/v1/chat/completions", json=payload, headers=bearer(token)).status_code == 200
        blocked = client.post("/v1/chat/completions", json=payload, headers=bearer(token))

        assert blocked.status_code == 429
        error = blocked.get_json()["error"]
        assert error["code"] == "rate_limit_exceeded"
        assert error["message"].startswith("⚠️ ")
        # A Russian key owner must not receive the English msgid.
        assert any("\u0400" <= char <= "\u04ff" for char in error["message"])

    def test_unauthenticated_requests_still_return_401(self, test_app, limited):
        client = test_app.test_client()
        response = client.post("/v1/chat/completions", json={"messages": []})
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.unit
class TestEmbeddingsRateLimit:
    def test_embeddings_shares_the_same_budget(self, test_app, limited):
        client = test_app.test_client()
        assert embed(client, limited).status_code == 200
        assert embed(client, limited).status_code == 200
        assert embed(client, limited).status_code == 429


@pytest.mark.unit
class TestIntrospectionIsNotLimited:
    def test_models_and_me_stay_available(self, test_app, limited):
        client = test_app.test_client()
        for _ in range(6):
            assert client.get("/v1/models", headers=bearer(limited)).status_code == 200
            assert client.get("/v1/flai/me", headers=bearer(limited)).status_code == 200

    def test_me_reports_the_configured_limit(self, test_app, limited):
        body = test_app.test_client().get("/v1/flai/me", headers=bearer(limited)).get_json()
        assert body["rate_limit"] == "2 per minute"


@pytest.mark.unit
class TestBudgetIsPerUser:
    def test_another_user_is_not_blocked(self, test_app, limited):
        client = test_app.test_client()
        for _ in range(3):
            assert chat(client, limited).status_code in (200, 429)

        create_user(login="otherowner", password="pw-otherowner-123", name="Other", language="en")
        other_token, _ = create_api_token("otherowner", name="test")
        assert chat(client, other_token).status_code == 200
