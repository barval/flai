# tests/test_tasks_chat_tool.py
"""A task step can call any chat tool, through the same gate the chat uses.

The gate is `execute_tool`, which is what keeps the per-user permission, the rate limit
and the audit single-sourced: a second dispatcher inside the task runner would be a
second place for them to drift.
"""

import pytest

pytestmark = pytest.mark.unit


def _step(tool_name: str, arguments: dict | None = None) -> dict:
    return {
        "id": "s1",
        "tool": "chat_tool",
        "goal": "check the host",
        "extra": {"tool": tool_name, "arguments": arguments or {}},
    }


def test_a_chat_tool_step_runs_through_execute_tool(test_app, monkeypatch):
    from app.tasks import runner

    seen = {}

    def fake_execute(name, arguments, context):
        seen.update(name=name, arguments=arguments, user=context.get("user_id"))
        return "64 bytes from 8.8.8.8"

    monkeypatch.setattr("app.tools.execute_tool", fake_execute)
    with test_app.app_context():
        outcome = runner.run_direct_step(
            test_app, _step("write_document", {"name": "n", "content": "c"}), "", lambda: False,
            user_id="alice", verify="off",
        )

    assert seen == {"name": "write_document", "arguments": {"name": "n", "content": "c"}, "user": "alice"}
    assert outcome["result"].startswith("64 bytes")
    assert outcome["status"] == "passed"


def test_an_invented_tool_is_refused_not_executed(test_app, monkeypatch):
    from app.tasks import runner

    monkeypatch.setattr("app.tools.execute_tool", lambda *a, **k: pytest.fail("must not run"))
    with test_app.app_context():
        outcome = runner.run_direct_step(test_app, _step("rm"), "", lambda: False, user_id="alice", verify="off")

    assert outcome["status"] == "error"
    assert "rm" in outcome["result"]


def test_a_refusal_from_the_tool_fails_the_step(test_app, monkeypatch):
    from app.tasks import runner
    from app.tasks import runner as runner_module

    monkeypatch.setattr("app.tools.execute_tool", lambda *a, **k: "⚠️ Сетевые инструменты Вам недоступны.")
    monkeypatch.setattr(runner_module, "_allowed_chat_tools", lambda: {"net_ping"})
    with test_app.app_context():
        outcome = runner.run_direct_step(
            test_app, _step("net_ping", {"host": "8.8.8.8"}), "", lambda: False, user_id="bob", verify="off"
        )

    assert outcome["status"] == "error"
    assert "недоступны" in outcome["result"]


def test_the_allowed_set_is_the_chat_catalogue(test_app):
    from app.tasks.runner import _allowed_chat_tools

    with test_app.app_context():
        names = _allowed_chat_tools()

    # The catalogue is live: net_* appears only when its sidecar answers, so the
    # always-present tools are what this test pins.
    assert "web_search" in names and "write_document" in names
    assert "python" not in names and "sh" not in names and "activate_skill" not in names
