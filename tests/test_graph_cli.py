# tests/test_graph_cli.py
"""CLI: graph-build queues work without calling a model, graph-status reports."""

import pytest

from tests.graph_fakes import FakeRedis

LOGIN = "graph_cli"
DOC = "graph-cli-doc"


@pytest.fixture
def graph_env(test_app):
    """Three indexed chunks for one document, on a clean slate otherwise."""
    from app.database import get_db

    with test_app.app_context():
        with get_db() as conn:
            c = conn.cursor()
            for table in (
                "kg_edges",
                "kg_occurrences",
                "kg_nodes",
                "kg_doc_edges",
                "kg_doc_vectors",
                "kg_build_state",
            ):
                c.execute(f"DELETE FROM {table} WHERE user_id = %s", (LOGIN,))
            c.execute("DELETE FROM rag_chunks WHERE user_id = %s", (LOGIN,))
            for index in range(3):
                c.execute(
                    "INSERT INTO rag_chunks (user_id, doc_id, chunk_index, filename, text, lemmas)"
                    " VALUES (%s, %s, %s, 'contract.pdf', %s, %s)",
                    (LOGIN, DOC, index, f"фрагмент {index}", ["фрагмент"]),
                )
        yield


@pytest.fixture
def queue(test_app):
    """The queue the CLI reaches through ``current_app.request_queue``."""
    test_app.request_queue.redis = FakeRedis()
    return test_app.request_queue


def test_graph_build_queues_one_seed_task_per_document(graph_env, queue, runner):
    # The extractor chains the next chunk itself, so one task per document is
    # enough and the shared background queue is not flooded with hundreds.
    from app.cli import graph_build

    result = runner.invoke(graph_build, ["--user", LOGIN, "--no-follow"])

    assert result.exit_code == 0, result.output
    assert queue.redis.llen(queue.background_queue_key) == 1


def test_graph_build_never_calls_a_model(graph_env, queue, runner, test_app):
    from app.cli import graph_build

    def refuse(*_args, **_kwargs):
        raise AssertionError("the CLI must not call the model")

    test_app.modules["base"].llamacpp.chat_stream = refuse

    result = runner.invoke(graph_build, ["--user", LOGIN, "--no-follow"])

    assert result.exit_code == 0, result.output


def test_graph_build_skips_a_finished_document(graph_env, queue, runner):
    from app.cli import graph_build
    from app.db import set_kg_build_state

    set_kg_build_state(DOC, LOGIN, "done", 3, 3, "multimodal")

    result = runner.invoke(graph_build, ["--user", LOGIN, "--no-follow"])

    assert result.exit_code == 0, result.output
    assert queue.redis.llen(queue.background_queue_key) == 0


def test_graph_status_prints_the_counters(graph_env, queue, runner):
    from app.cli import graph_status

    result = runner.invoke(graph_status, ["--user", LOGIN])

    assert result.exit_code == 0, result.output
    assert LOGIN in result.output
