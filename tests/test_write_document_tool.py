# tests/test_write_document_tool.py
"""The chat tool around the writer: arguments, one localized answer, one queued job."""

import pytest

from app import documents_write, tools

pytestmark = pytest.mark.requires_db

LOGIN = "writer_tool_test"


@pytest.fixture
def ctx(test_app, tmp_path, monkeypatch):
    root = tmp_path / "documents"
    monkeypatch.setattr(documents_write, "_documents_root", lambda: str(root))
    with test_app.app_context():
        from app.database import get_db

        with get_db() as conn:
            conn.cursor().execute("DELETE FROM documents WHERE user_id = %s", (LOGIN,))
        test_app.config["DOCUMENTS_FOLDER"] = str(root)
        yield {"app": test_app, "user_id": LOGIN, "lang": "ru", "user_class": 2}


@pytest.fixture
def queued(test_app, monkeypatch):
    calls = []

    def fake_add_request(**kwargs):
        calls.append(kwargs)
        return "task-id"

    monkeypatch.setattr(test_app.request_queue, "add_request", fake_add_request)
    return calls


def test_the_tool_writes_and_returns_the_stored_name(ctx, queued):
    result = tools.execute_tool("write_document", {"name": "Итоги", "content": "# Заголовок"}, ctx)

    assert "Итоги.md" in result
    assert not result.startswith("⚠️ ")


def test_a_failure_is_a_sentence_naming_the_problem(ctx, queued):
    result = tools.execute_tool("write_document", {"name": "x", "content": "y", "format": "pdf"}, ctx)

    assert result.startswith("⚠️ ")
    assert "pdf" in result


def test_the_tool_queues_no_indexing_at_all(ctx, queued):
    """A written document is stored unindexed; its card carries the switch instead."""
    tools.execute_tool("write_document", {"name": "n", "content": "c"}, ctx)

    assert queued == []


def test_the_answer_points_at_the_switch(ctx, queued):
    result = tools.execute_tool("write_document", {"name": "n", "content": "c"}, ctx)

    assert "n.md" in result
    assert "поиск" in result.lower()   # the sentence points at the search switch


def test_the_tool_is_kept_when_the_kernel_narrows_the_list(test_app):
    with test_app.app_context():
        names = {d["function"]["name"] for d in tools.select_tool_definitions(("web_search",), "ru")}

    assert "write_document" in names


def test_rows_reach_the_writer_for_csv(ctx, queued):
    result = tools.execute_tool(
        "write_document",
        {"name": "таблица", "format": "csv", "rows": [["a", "b"], ["1", "2"]]},
        ctx,
    )

    assert "таблица.csv" in result
