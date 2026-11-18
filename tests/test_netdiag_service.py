# tests/test_netdiag_service.py
"""The diagnostics container refuses bad input before a process starts.

Everything the model can reach is a typed field, so the only way a shell
metacharacter could matter is if a command were built as a string. These tests
exist to keep it a list.
"""

import importlib.util
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MODULE_PATH = Path(__file__).resolve().parent.parent / "services/netdiag/app.py"


@pytest.fixture(scope="module")
def netdiag():
    spec = importlib.util.spec_from_file_location("netdiag_service", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Done:
    returncode = 0
    stdout = "ok output"
    stderr = ""


def test_a_metacharacter_never_reaches_a_process(netdiag, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append((a, k)) or _Done())

    text = netdiag.run("ping", {"host": "example.com; rm -rf /", "count": 1, "timeout": 5})

    assert calls == []
    assert text.startswith("⚠️ ")


def test_a_command_is_a_list_and_never_a_shell_string(netdiag, monkeypatch):
    seen = {}
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: seen.update({"args": a[0], "kwargs": k}) or _Done())

    netdiag.run("ping", {"host": "example.com", "count": 2, "timeout": 5})

    assert isinstance(seen["args"], list)
    assert seen["kwargs"].get("shell") is False
    assert "example.com" in seen["args"]


def test_a_link_local_target_is_blocked(netdiag):
    assert netdiag.is_blocked_target("169.254.169.254")
    assert netdiag.is_blocked_target("127.0.0.1")
    assert netdiag.is_blocked_target("::1")


def test_a_public_target_is_not_blocked(netdiag):
    assert netdiag.is_blocked_target("203.0.113.10") is None


def test_a_blocked_target_never_reaches_a_process(netdiag, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("a blocked target must not run"))

    text = netdiag.run("ping", {"host": "127.0.0.1", "count": 1, "timeout": 5})

    assert text.startswith("⚠️ ")


def test_the_count_and_the_port_range_are_bounded(netdiag):
    assert netdiag.run("ping", {"host": "example.com", "count": 99, "timeout": 5}).startswith("⚠️ ")
    assert netdiag.run("speed", {"host": "example.com", "port": 70000, "seconds": 5}).startswith("⚠️ ")


def test_an_unknown_tool_is_refused(netdiag):
    assert netdiag.run("sh", {"host": "example.com"}).startswith("⚠️ ")


def test_a_cidr_scan_is_discovery_only(netdiag, monkeypatch):
    seen = {}
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: seen.update({"args": a[0]}) or _Done())

    netdiag.run("ports", {"host": "192.168.1.0/24", "timeout": 30})

    assert "-sn" in seen["args"]
    assert "--top-ports" not in seen["args"]


def test_a_single_host_scan_keeps_the_port_list_small(netdiag, monkeypatch):
    seen = {}
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: seen.update({"args": a[0]}) or _Done())

    netdiag.run("ports", {"host": "192.168.1.10", "timeout": 30})

    assert "--top-ports" in seen["args"]
    assert seen["args"][seen["args"].index("--top-ports") + 1] == "100"
    assert "-O" not in seen["args"]
    assert "-sS" not in seen["args"]


def test_a_url_is_parsed_before_the_address_is_checked(netdiag, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("a blocked url must not run"))

    for url in ("http://127.0.0.1/", "http://user@127.0.0.1:8080/x", "http://[::1]/", "http://169.254.169.254/"):
        assert netdiag.run("http", {"url": url, "timeout": 5}).startswith("⚠️ "), url


def test_an_ipv6_literal_is_a_valid_host(netdiag, monkeypatch):
    seen = {}
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: seen.update({"args": a[0]}) or _Done())

    netdiag.run("ping", {"host": "2001:4860:4860::8888", "count": 1, "timeout": 5})

    assert "2001:4860:4860::8888" in seen["args"]


def test_the_container_ceiling_matches_the_application(netdiag):
    """Two tables describe one limit; a container that allows longer than the caller
    waits turns a slow scan into a truncated answer with no explanation."""
    from app import netdiag as client

    assert set(netdiag.DEFAULT_TIMEOUT) == set(client.TOOL_TIMEOUTS)
    for tool, limit in client.TOOL_TIMEOUTS.items():
        assert netdiag.DEFAULT_TIMEOUT[tool] <= limit, tool


class _Failed:
    """An iperf3 run that never got a session: it prints an error and exits non-zero."""

    returncode = 1
    stdout = "Connecting to host example.com, port 5201\n"
    stderr = "iperf3: error - unable to connect to server: Connection refused\n"


def test_a_speed_run_that_cannot_connect_is_a_failure(netdiag, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Failed())

    ok, text = netdiag.execute("speed", {"host": "example.com", "seconds": 1})

    assert ok is False
    assert "unable to connect" in text


def test_a_ping_with_no_reply_is_still_an_answer(netdiag, monkeypatch):
    """100% packet loss is the result, not a broken tool: its exit code must not fail it."""

    class _NoReply(_Failed):
        stdout = "1 packets transmitted, 0 received, 100% packet loss, time 0ms\n"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _NoReply())

    ok, text = netdiag.execute("ping", {"host": "example.com", "count": 1, "timeout": 5})

    assert ok is True
    assert "100% packet loss" in text


def test_speed_does_not_wait_out_the_whole_timeout_to_connect(netdiag, monkeypatch):
    seen = {}
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: seen.update({"args": a[0]}) or _Done())

    netdiag.run("speed", {"host": "example.com", "seconds": 1})

    assert "--connect-timeout" in seen["args"]
