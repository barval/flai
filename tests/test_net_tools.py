# tests/test_net_tools.py
"""The seven diagnostics tools: who may call them, how often, and what is recorded.

The permission is a per-user flag an administrator sets, so the refusal has to be a
sentence the model can say — a hidden tool cannot explain itself to the person who
asked for a ping.
"""

import pytest

from app import netdiag, tools

pytestmark = pytest.mark.unit


class _FakeRedis:
    def __init__(self):
        self.values = {}
        self.lists = {}

    def get(self, key):
        return self.values.get(key)

    def incr(self, key):
        self.values[key] = int(self.values.get(key, 0)) + 1
        return self.values[key]

    def expire(self, key, _ttl):
        return True

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    def ltrim(self, key, start, stop):
        items = self.lists.get(key, [])
        self.lists[key] = items[start : stop + 1]
        return True

    def lrange(self, key, start, stop):
        return self.lists.get(key, [])[start : stop + 1]


@pytest.fixture
def redis_stub(monkeypatch):
    stub = _FakeRedis()
    monkeypatch.setattr(netdiag, "_redis", lambda: stub)
    return stub


@pytest.fixture
def ctx(test_app):
    # A tool always runs inside a request; the application context is what makes
    # force_locale() resolve the refusal sentence in the user's language.
    with test_app.app_context():
        test_app.config["NET_TOOLS_ENABLED"] = True
        yield {"app": test_app, "user_id": "alice", "lang": "ru"}


@pytest.fixture
def permitted(monkeypatch, test_app):
    monkeypatch.setattr(netdiag, "user_allowed", lambda login: True)
    monkeypatch.setattr(netdiag, "available", lambda: True)
    with test_app.app_context():
        test_app.config["NET_TOOLS_ENABLED"] = True
    return True


def test_a_user_without_the_flag_is_told_so(ctx, monkeypatch):
    monkeypatch.setattr(netdiag, "user_allowed", lambda login: False)
    monkeypatch.setattr(netdiag, "call", lambda *a, **k: pytest.fail("must not reach the sidecar"))

    result = tools.execute_tool("net_ping", {"host": "example.com"}, ctx)

    assert result.startswith("⚠️ ")
    assert "Сетевые инструменты" in result


def test_an_allowed_user_gets_the_sidecar_output(ctx, permitted, redis_stub, monkeypatch):
    monkeypatch.setattr(netdiag, "call", lambda tool, payload, **k: {"ok": True, "output": "3 packets transmitted"})

    result = tools.execute_tool("net_ping", {"host": "example.com", "count": 3}, ctx)

    assert result.startswith("3 packets transmitted")


def test_the_eleventh_call_in_a_minute_is_refused_and_not_counted(ctx, permitted, redis_stub, monkeypatch):
    monkeypatch.setattr(netdiag, "call", lambda tool, payload, **k: {"ok": True, "output": "ok"})

    for _ in range(netdiag.RATE_PER_MINUTE):
        tools.execute_tool("net_ping", {"host": "example.com"}, ctx)
    result = tools.execute_tool("net_ping", {"host": "example.com"}, ctx)

    assert result.startswith("⚠️ ")
    assert max(redis_stub.values.values()) == netdiag.RATE_PER_MINUTE


def test_a_call_is_audited_once_with_its_target(ctx, permitted, redis_stub, monkeypatch):
    monkeypatch.setattr(netdiag, "call", lambda tool, payload, **k: {"ok": True, "output": "ok"})

    tools.execute_tool("net_ping", {"host": "example.com"}, ctx)

    entries = netdiag.recent_calls()
    assert len(entries) == 1
    assert entries[0]["login"] == "alice"
    assert entries[0]["tool"] == "net_ping"
    assert entries[0]["target"] == "example.com"
    assert entries[0]["ok"] is True


def test_a_blocked_target_never_reaches_the_sidecar(ctx, permitted, redis_stub, monkeypatch):
    monkeypatch.setattr(netdiag, "call", lambda *a, **k: pytest.fail("must not reach the sidecar"))

    result = tools.execute_tool("net_ping", {"host": "169.254.169.254"}, ctx)

    assert result.startswith("⚠️ ")


def test_the_audit_is_bounded_and_expires(redis_stub):
    for index in range(600):
        netdiag.record_call("alice", "net_ping", f"host{index}", True, 0.1)

    assert len(redis_stub.lists["net:audit"]) == 500
    assert netdiag.recent_calls(5)[0]["target"] == "host599"


def test_a_refused_call_is_not_written_to_the_audit(ctx, monkeypatch, redis_stub):
    monkeypatch.setattr(netdiag, "user_allowed", lambda login: False)

    tools.execute_tool("net_ping", {"host": "example.com"}, ctx)

    assert "net:audit" not in redis_stub.lists


def test_the_admin_can_grant_the_permission(app, monkeypatch):
    from flask import session

    from app.routes import admin as admin_module

    seen = {}
    monkeypatch.setattr(admin_module, "update_user", lambda **kw: seen.update(kw))

    with app.test_request_context("/admin/api/users/alice", method="PUT", json={"net_tools_enabled": True}):
        session["is_admin"] = True
        admin_module.update_user_data.__wrapped__("alice")

    assert seen["net_tools_enabled"] is True


def test_the_audit_endpoint_returns_the_recent_calls(app, redis_stub):
    from flask import session

    from app.routes.admin import net_audit

    netdiag.record_call("alice", "net_ping", "example.com", True, 0.5)

    with app.test_request_context("/admin/api/net-audit", method="GET"):
        session["is_admin"] = True
        payload = net_audit.__wrapped__().get_json()

    assert payload["calls"][0]["target"] == "example.com"


@pytest.mark.requires_db
def test_the_cli_prints_one_line_per_call(redis_stub):
    from click.testing import CliRunner

    from app.cli import net_audit

    netdiag.record_call("alice", "net_ports", "192.168.1.10", True, 1.25)

    result = CliRunner().invoke(net_audit, ["--limit", "5"])

    assert result.exit_code == 0
    assert "192.168.1.10" in result.output
    assert "net_ports" in result.output


def test_the_flask_command_is_registered(test_app):
    assert "net-audit" in test_app.cli.commands


def test_the_switch_reads_a_string_from_the_environment(monkeypatch):
    monkeypatch.delenv("NET_TOOLS_ENABLED", raising=False)
    assert netdiag._setting("NET_TOOLS_ENABLED", False) is False
    monkeypatch.setenv("NET_TOOLS_ENABLED", "true")
    assert netdiag._setting("NET_TOOLS_ENABLED", False) is True
