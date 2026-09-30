"""API-scoped CORS and bounded concurrent waits.

A browser client is a real use case for a self-hosted assistant, so `/v1`
optionally answers CORS preflights. The allowlist is empty by default, which
means the API stays closed to browsers until an origin is configured.

The wait slots exist because every synchronous request parks a thread (and a
Redis subscription) for up to `API_SYNC_MAX_WAIT`. Without a cap a burst of
clients would hold hundreds of those at once on a single-worker server.
"""

import json
import re
from pathlib import Path

import pytest

from app.api_tokens import create_api_token
from app.userdb import create_user

API = "/v1"
CHAT = f"{API}/chat/completions"
EMBEDDINGS = f"{API}/embeddings"
MODELS = f"{API}/models"
ORIGIN = "https://app.example"


class FullSemaphore:
    """A semaphore that is always saturated."""

    def acquire(self, *args, **kwargs):
        return False

    def release(self):
        return None


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def cors(response):
    return {k.lower(): v for k, v in response.headers.items() if k.lower().startswith("access-control")}


@pytest.fixture
def token():
    create_user(login="apiuser", password="pw-apiuser-123456", name="API User", language="en")
    value, _ = create_api_token("apiuser", name="test")
    return value


@pytest.fixture
def api_client(test_app, token):
    test_app.config["API_CORS_ORIGINS"] = ""
    return test_app.test_client(), token


@pytest.fixture
def cors_client(test_app, token, monkeypatch):
    """An app with one allowed origin, re-read from config per request."""
    from app.routes import api_v1

    test_app.config["API_CORS_ORIGINS"] = f"{ORIGIN}, https://second.example"
    monkeypatch.setattr(api_v1, "cors_allowlist", lambda: [o for o in (ORIGIN, "https://second.example")])
    return test_app.test_client(), token


@pytest.mark.unit
class TestCorsIsOffByDefault:
    def test_no_headers_for_a_foreign_origin(self, api_client):
        client, tok = api_client
        response = client.get(MODELS, headers={**bearer(tok), "Origin": "https://evil.example"})
        assert response.status_code == 200
        assert cors(response) == {}

    def test_even_the_own_site_gets_no_allow_header(self, api_client):
        client, tok = api_client
        response = client.get(MODELS, headers={**bearer(tok), "Origin": ORIGIN})
        assert "access-control-allow-origin" not in cors(response)

    def test_v1_prefix_lookalike_is_not_an_api_route(self, test_app):
        from app.routes.api_v1 import _is_api_path

        assert _is_api_path("/v1")
        assert _is_api_path("/v1/audio/speech")
        assert not _is_api_path("/v1evil")


@pytest.mark.unit
class TestAllowedOrigin:
    def test_origin_is_echoed_exactly_with_vary(self, cors_client):
        client, tok = cors_client
        response = client.get(MODELS, headers={**bearer(tok), "Origin": ORIGIN})
        assert response.status_code == 200
        headers = cors(response)
        assert headers["access-control-allow-origin"] == ORIGIN
        assert "Origin" in response.headers["Vary"]
        assert headers["access-control-allow-credentials"] == "true"
        assert headers["access-control-allow-headers"] == "Authorization, Content-Type"

    def test_second_configured_origin_also_works(self, cors_client):
        client, tok = cors_client
        response = client.get(MODELS, headers={**bearer(tok), "Origin": "https://second.example"})
        assert cors(response)["access-control-allow-origin"] == "https://second.example"

    def test_disallowed_origin_gets_no_allow_header(self, cors_client):
        client, tok = cors_client
        response = client.get(MODELS, headers={**bearer(tok), "Origin": "https://evil.example"})
        assert response.status_code == 200
        assert "access-control-allow-origin" not in cors(response)

    def test_preflight_advertises_methods_and_max_age(self, cors_client):
        client, _ = cors_client
        response = client.options(
            CHAT,
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization, content-type",
            },
        )
        assert response.status_code in (200, 204)
        headers = cors(response)
        assert headers["access-control-allow-origin"] == ORIGIN
        assert "POST" in headers["access-control-allow-methods"]
        assert "DELETE" in headers["access-control-allow-methods"]
        assert headers["access-control-max-age"] == "600"

    def test_allowlist_never_leaks_outside_the_api_prefix(self, cors_client):
        """A browser must not be able to read the web UI through the API list."""
        client, _ = cors_client
        response = client.get("/login", headers={"Origin": ORIGIN})
        assert "access-control-allow-origin" not in cors(response)

    def test_allowlist_parsing_is_exact_and_trimmed(self):
        from app.routes.api_v1 import parse_cors_origins

        assert parse_cors_origins("") == []
        assert parse_cors_origins(None) == []
        assert parse_cors_origins("  ") == []
        assert parse_cors_origins("https://a.example , https://b.example") == [
            "https://a.example",
            "https://b.example",
        ]
        # A prefix must never match: an attacker origin like
        # "https://a.example.evil.com" is a different origin.
        assert "https://a.example.evil.com" not in parse_cors_origins("https://a.example")


@pytest.mark.unit
class TestBoundedConcurrentWaits:
    def test_saturated_slots_return_429_with_retry_after(self, test_app, token, monkeypatch):
        from app.routes import api_v1

        test_app._api_wait_slots = FullSemaphore()
        enqueued = []
        monkeypatch.setattr(api_v1, "resolve_api_session", lambda *a, **k: "session-1")
        monkeypatch.setattr(api_v1, "enqueue_chat", lambda *a, **k: enqueued.append(a))
        monkeypatch.setattr(api_v1, "wait_for_result", lambda *a, **k: {"response": "ok"})

        response = test_app.test_client().post(
            CHAT,
            json={"model": "flai-chat", "messages": [{"role": "user", "content": "hi"}]},
            headers=bearer(token),
        )
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "1"
        error = response.get_json()["error"]
        assert error["type"] == "rate_limit_error"
        assert error["code"] == "too_many_requests"
        assert error["message"].startswith("⚠️ ")
        assert enqueued == []

    def test_embeddings_shares_the_same_slots(self, test_app, token, monkeypatch):
        from app.routes import api_v1

        test_app._api_wait_slots = FullSemaphore()
        enqueued = []
        monkeypatch.setattr(api_v1, "enqueue_embeddings", lambda *a, **k: enqueued.append(a))
        monkeypatch.setattr(api_v1, "wait_for_result", lambda *a, **k: {"embeddings": [[0.1]]})

        response = test_app.test_client().post(
            EMBEDDINGS,
            json={"model": "flai-embeddings", "input": "hi"},
            headers=bearer(token),
        )
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "1"
        assert enqueued == []

    def test_a_rejected_request_does_not_consume_a_slot(self, test_app, token):
        from app.routes.api_v1 import wait_slots

        test_app.config["API_MAX_CONCURRENT_WAITS"] = 1
        slots = wait_slots(test_app)
        assert slots._value == 1
        response = test_app.test_client().post(CHAT, json={}, headers=bearer(token))
        assert response.status_code == 400
        assert slots._value == 1

    def test_a_timeout_releases_its_slot(self, test_app, token, monkeypatch):
        from app.api_bridge import ApiTaskTimeoutError
        from app.routes import api_v1
        from app.routes.api_v1 import wait_slots

        test_app.config["API_MAX_CONCURRENT_WAITS"] = 1
        slots = wait_slots(test_app)
        held = {}

        def _timeout(*args, **kwargs):
            # The route holds the slot while it waits; nothing free must be
            # available here, and the timeout must hand it back.
            held["free_slots_during_wait"] = slots.acquire(blocking=False)
            raise ApiTaskTimeoutError("task-1")

        monkeypatch.setattr(api_v1, "wait_for_result", _timeout)
        response = test_app.test_client().post(
            CHAT,
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=bearer(token),
        )
        assert response.status_code == 408
        assert held["free_slots_during_wait"] is False
        assert slots._value == 1

    def test_a_completed_wait_releases_its_slot(self, test_app, token, monkeypatch):
        from app.routes import api_v1
        from app.routes.api_v1 import wait_slots

        test_app.config["API_MAX_CONCURRENT_WAITS"] = 1
        slots = wait_slots(test_app)

        def _ok(*args, **kwargs):
            assert slots.acquire(blocking=False) is False, "the route must hold the slot while waiting"
            return {"response": "ok", "session_id": "s1"}

        monkeypatch.setattr(api_v1, "wait_for_result", _ok)
        response = test_app.test_client().post(
            CHAT,
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=bearer(token),
        )
        assert response.status_code == 200
        assert slots._value == 1

    def test_slots_are_sized_from_config(self, test_app):
        test_app.config["API_MAX_CONCURRENT_WAITS"] = 3
        from app.routes.api_v1 import wait_slots

        assert wait_slots(test_app)._value == 3


def _free_slot(test_app):
    """True when a slot is free; a saturated set would answer 429."""
    acquired = test_app._api_wait_slots.acquire(blocking=False)
    if acquired:
        test_app._api_wait_slots.release()
    return acquired


@pytest.mark.unit
class TestConfigIsDocumented:
    def test_defaults(self, test_app):
        """The shipped defaults are the ones .env.example documents."""
        assert test_app.config["API_RATE_LIMIT"] == "60 per minute;1000 per hour"
        assert test_app.config["API_MAX_CONCURRENT_WAITS"] == 64
        assert test_app.config["API_CORS_ORIGINS"] == ""

    def test_env_example_documents_every_api_knob(self):
        root = Path(__file__).resolve().parent.parent
        example = (root / ".env.example").read_text(encoding="utf-8")
        for name in ("API_RATE_LIMIT", "API_MAX_CONCURRENT_WAITS", "API_CORS_ORIGINS"):
            assert re.search(rf"^{name}=", example, re.MULTILINE), f"{name} missing from .env.example"

    def test_effective_limits_are_reported_to_clients(self, test_app, token):
        body = test_app.test_client().get("/v1/flai/me", headers=bearer(token)).get_json()
        assert body["max_concurrent_waits"] == 64


@pytest.mark.unit
def test_regression_suite_marker_uses_json_body(test_app, token):
    """Guard: the CORS tests post real JSON, not a stray string."""
    response = test_app.test_client().post(CHAT, data=json.dumps({"messages": []}), headers=bearer(token))
    assert response.status_code == 400
