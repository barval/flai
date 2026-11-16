# tests/test_netdiag_client.py
"""The sidecar client: one probe per half-minute, and a switch that can stop it.

The catalogue is built three times per chat message, so an uncached health check
would let one hung container cost nine seconds on every turn.
"""

import pytest

from app import netdiag

pytestmark = pytest.mark.unit


class _Response:
    def __init__(self, ok=True, payload=None, status=200):
        self._ok = ok
        self._payload = payload or {"ok": True, "tools": ["dns"]}
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self._ok:
            raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def _reset():
    netdiag.reset_availability_cache()
    yield
    netdiag.reset_availability_cache()


def test_the_health_answer_is_cached_for_thirty_seconds(monkeypatch, test_app):
    calls = {"n": 0}

    def fake_get(*_a, **_k):
        calls["n"] += 1
        return _Response()

    monkeypatch.setattr("app.netdiag.requests.get", fake_get)
    with test_app.app_context():
        test_app.config["NET_TOOLS_ENABLED"] = True
        test_app.config["NETDIAG_URL"] = "http://flai-netdiag:8099"
        assert netdiag.available() is True
        assert netdiag.available() is True
        assert netdiag.available() is True

    assert calls["n"] == 1


def test_a_disabled_switch_reports_unavailable_without_a_request(monkeypatch, test_app):
    monkeypatch.setattr("app.netdiag.requests.get", lambda *a, **k: pytest.fail("must not probe"))

    with test_app.app_context():
        test_app.config["NET_TOOLS_ENABLED"] = False
        assert netdiag.available() is False


def test_a_hung_sidecar_is_unavailable_not_an_exception(monkeypatch, test_app):
    def boom(*_a, **_k):
        raise netdiag.requests.RequestException("timeout")

    monkeypatch.setattr("app.netdiag.requests.get", boom)
    with test_app.app_context():
        test_app.config["NET_TOOLS_ENABLED"] = True
        test_app.config["NETDIAG_URL"] = "http://flai-netdiag:8099"
        assert netdiag.available() is False


def test_a_call_posts_to_its_own_endpoint_and_returns_the_output(monkeypatch, test_app):
    seen = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["json"] = kwargs.get("json")
        seen["timeout"] = kwargs.get("timeout")
        return _Response(payload={"ok": True, "output": "64 bytes from ..."})

    monkeypatch.setattr("app.netdiag.requests.post", fake_post)
    with test_app.app_context():
        test_app.config["NETDIAG_URL"] = "http://flai-netdiag:8099"
        test_app.config["NETDIAG_API_TOKEN"] = "secret"
        result = netdiag.call("ping", {"host": "example.com", "count": 3})

    assert seen["url"] == "http://flai-netdiag:8099/ping"
    assert seen["json"]["host"] == "example.com"
    assert seen["timeout"] == netdiag.TOOL_TIMEOUTS["ping"]
    assert result["output"].startswith("64 bytes")


def test_every_tool_has_a_ceiling(monkeypatch):
    assert set(netdiag.TOOL_TIMEOUTS) == {"dns", "whois", "ping", "trace", "ports", "http", "speed"}
    assert max(netdiag.TOOL_TIMEOUTS.values()) <= 45


def test_the_service_and_its_network_are_declared_on_both_platforms():
    from pathlib import Path

    for name in ("docker-compose.gpu.base.yml", "docker-compose.cpu.yml"):
        text = Path(name).read_text(encoding="utf-8")
        assert "flai-netdiag" in text, name
        assert "netdiag_network" in text, name
        assert "cap_add" in text and "NET_RAW" in text, name
        assert "read_only: true" in text, name
