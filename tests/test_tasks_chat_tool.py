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


def test_a_planned_step_keeps_its_extra_all_the_way_to_the_row(test_app):
    """The plan carries `extra`; without it a chat_tool step arrives with no tool name.

    This is the path a real task takes — plan → step row → runner — and the earlier
    tests built the step by hand, which is exactly how a dead feature passed them.
    """
    from app.tasks import executor as ex
    from app.tasks.executor import get_task_steps
    from app.tasks.store import create_task

    plan = ex._parse_plan(
        '{"title": "t", "steps": [{"seq": 1, "goal": "g", "tool": "chat_tool", '
        '"checkpoint_criteria": "c", "extra": {"tool": "write_document", "arguments": {"name": "n"}}}]}'
    )

    with test_app.app_context():
        from app.database import init_db

        init_db()
        task_id = create_task(
            user_id="alice", task_class=5, title="t", lang="en", response_style="neutral"
        )
        executor = ex.TaskExecutor(test_app)
        executor._create_plan_step_rows(task_id, plan["steps"])
        row = get_task_steps(task_id)[0]

    assert row["extra"]["tool"] == "write_document"
    assert row["extra"]["arguments"] == {"name": "n"}


def test_a_plan_carries_at_most_two_skills():
    from app.tasks.executor import _parse_plan

    plan = _parse_plan(
        '{"title": "t", "skills": ["a", "b", "c"], "steps": [{"seq": 1, "goal": "g", "tool": "llm"}]}'
    )

    assert plan["skills"] == ["a", "b"]
