# tests/test_graph_queue.py
"""Graph extraction as a background task: one chunk per call, interruption, empty input."""

import json

import pytest

LOGIN = "graph_queue"
DOC = "graph-doc-1"


@pytest.fixture
def graph_env(test_app):
    """Chunks for one document, on a clean slate for this module's rows."""
    from app.database import get_db

    with test_app.app_context():
        with get_db() as conn:
            c = conn.cursor()
            for table in ("kg_edges", "kg_occurrences", "kg_nodes", "kg_doc_edges", "kg_doc_vectors", "kg_build_state"):
                c.execute(f"DELETE FROM {table} WHERE user_id = %s", (LOGIN,))
            c.execute("DELETE FROM rag_chunks WHERE user_id = %s", (LOGIN,))
            c.execute(
                "INSERT INTO rag_chunks (user_id, doc_id, chunk_index, filename, text, lemmas)"
                " VALUES (%s, %s, 0, 'contract.pdf', 'проект Сокол стартовал', %s)",
                (LOGIN, DOC, ["проект", "сокол", "стартовать"]),
            )
        yield


class _FakeRedis:
    """Just enough Redis for the extractor: flags and two queues."""

    def __init__(self):
        self.values: dict = {}
        self.lists: dict = {}

    def exists(self, key):
        return 1 if key in self.values else 0

    def set(self, key, value, ex=None):
        self.values[key] = value
        return True

    def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)
        return 1

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)
        return 1

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return 1

    def llen(self, key):
        return len(self.lists.get(key, []))


@pytest.fixture
def queue(test_app):
    from app.queue import RedisRequestQueue

    queue = RedisRequestQueue(test_app, start_workers=False)
    # The suite patches redis.from_url to a mock whose exists() is truthy, which
    # would make every extraction look interrupted; the flag needs real state.
    queue.redis = _FakeRedis()
    return queue


def _answer(monkeypatch, app, payload: dict):
    """Replace the streaming call with one that yields ``payload`` as JSON."""

    def stream(_messages, **_kwargs):
        yield json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(app.modules["base"].llamacpp, "chat_stream", stream)


def _clear_flags(queue) -> None:
    """A warm-up or a previous test can leave the quiet-window flag set."""
    queue.redis.delete(queue.GRAPH_EXTRACT_INTERRUPT_KEY)
    queue.redis.delete(queue.MEMORY_EXTRACT_INTERRUPT_KEY)


def _task(chunk_index: int = 0) -> dict:
    return {
        "id": "graph-task-1",
        "type": "graph_extract_task",
        "user_id": LOGIN,
        "data": {"doc_id": DOC, "chunk_index": chunk_index},
    }


def test_one_chunk_advances_the_build_state(graph_env, queue, monkeypatch):
    from app.db import get_kg_build_state

    _answer(monkeypatch, queue.app, {"entities": [], "relations": []})
    _clear_flags(queue)

    result = queue._process_graph_extract(_task())

    assert result["status"] == "ok", result
    assert get_kg_build_state(DOC, LOGIN)["chunks_done"] == 1


def test_an_interrupt_leaves_the_chunk_unwritten(graph_env, queue, monkeypatch):
    from app.db import find_kg_node_ids, get_kg_build_state

    _answer(monkeypatch, queue.app, {"entities": [{"name": "Сокол", "type": "project", "description": ""}], "relations": []})
    _clear_flags(queue)
    queue.redis.set(queue.GRAPH_EXTRACT_INTERRUPT_KEY, "1", ex=30)

    result = queue._process_graph_extract(_task())

    assert result["status"] == "interrupted"
    assert get_kg_build_state(DOC, LOGIN)["chunks_done"] == 0
    assert find_kg_node_ids(LOGIN, ["сокол"], None, 8) == []


def test_a_document_without_chunks_is_marked_empty(graph_env, queue):
    from app.db import get_kg_build_state

    _clear_flags(queue)
    task = _task()
    task["data"]["doc_id"] = "graph-doc-empty"

    queue._process_graph_extract(task)

    assert get_kg_build_state("graph-doc-empty", LOGIN)["status"] == "empty"
