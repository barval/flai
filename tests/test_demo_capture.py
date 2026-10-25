"""Tests for the demo capture client."""

import pytest

from demo.capture import CaptureTimeout, DemoSession, rewrite_for_publication, wait_for


class Response:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class FakeTransport:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, "kwargs": kwargs})
        for suffix, response in self.responses.items():
            if url.endswith(suffix):
                return response
        return Response(200, {"status": "ok"})


def test_session_fetches_csrf_after_login():
    transport = FakeTransport({"/chat": Response(200, text='<meta name="csrf-token" content="tok">')})
    session = DemoSession("http://demo", transport=transport)
    session.login("demo_ru_chat", "secret")
    assert session.csrf_token == "tok"


def test_session_sends_the_token_on_post():
    transport = FakeTransport({"/chat": Response(200, text='<meta name="csrf-token" content="tok">')})
    session = DemoSession("http://demo", transport=transport)
    session.login("demo_ru_chat", "secret")
    session.post_json("/api/sessions/new", {})
    headers = transport.calls[-1]["kwargs"].get("headers", {})
    assert headers.get("X-CSRFToken") == "tok"


def test_publication_replaces_real_users():
    snapshot = {"routes": [{"path": "/admin/api/users", "body": [{"login": "real", "name": "Real"}]}]}
    out = rewrite_for_publication(snapshot, lang="ru")
    assert [user["login"] for user in out["routes"][0]["body"]] == ["anna", "igor", "maria"]
    assert [user["name"] for user in out["routes"][0]["body"]] == ["Анна", "Игорь", "Мария"]


def test_publication_replaces_monitoring_series():
    snapshot = {"routes": [{"path": "/admin/api/monitoring", "body": {"series": {"messages": [99, 98]}}}]}
    out = rewrite_for_publication(snapshot, lang="en")
    assert out["routes"][0]["body"]["series"]["messages"] != [99, 98]


def test_wait_for_raises_on_timeout():
    with pytest.raises(CaptureTimeout):
        wait_for(lambda: False, timeout_s=0.05, interval_s=0.01)


def test_wait_for_returns_when_ready():
    calls = {"count": 0}

    def ready():
        calls["count"] += 1
        return calls["count"] >= 2

    assert wait_for(ready, timeout_s=1, interval_s=0.01) is True
