# tests/test_graph_search_integration.py
"""Graph evidence joins retrieval inside its own budget share."""

from types import SimpleNamespace

import pytest

LOGIN = "graph_search"


@pytest.fixture
def database(test_app):
    """A small corpus and a small graph, on a clean slate."""
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
        yield


def _chunk(doc_id: str, index: int, text: str) -> dict:
    return {"doc_id": doc_id, "chunk_index": index, "text": text, "filename": f"{doc_id}.pdf"}


def _store_chunk(doc_id: str, index: int, text: str) -> None:
    from app.database import get_db

    with get_db() as conn:
        conn.cursor().execute(
            "INSERT INTO rag_chunks (user_id, doc_id, chunk_index, filename, text, lemmas)"
            " VALUES (%s, %s, %s, 'contract.pdf', %s, %s)",
            (LOGIN, doc_id, index, text, ["альфа"]),
        )


def _node(name: str, doc_id: str, chunk: int) -> str:
    from app.db import add_kg_occurrence, upsert_kg_node

    node_id = upsert_kg_node(LOGIN, "project", name, name.lower(), [name.lower()], "")
    add_kg_occurrence(node_id, LOGIN, doc_id, chunk, name)
    return node_id


def _graph(app) -> None:
    from app.db import upsert_kg_edge

    alpha = _node("Альфа", "doc-1", 0)
    beta = _node("Бета", "doc-2", 1)
    upsert_kg_edge(LOGIN, alpha, beta, "поставляет", "doc-2", 1, "Альфа поставляет Бета")
    _store_chunk("doc-2", 1, "Альфа поставляет Бета")


def _stage(app, budget_fragments: int):
    rag = app.modules["rag"]
    return rag._graph_stage_with_section(
        LOGIN,
        "что известно про Альфа",
        [(LOGIN, None)],
        [_chunk("doc-1", 0, "Альфа")],
        [0.9],
        SimpleNamespace(graph_fragments=budget_fragments),
        top_k=5,
    )


def test_a_disabled_graph_returns_the_retrieval_unchanged(database, test_app):
    test_app.config["GRAPH_RAG_ENABLED"] = False
    _graph(test_app)

    chunks, scores, section = _stage(test_app, 3)

    assert chunks == [_chunk("doc-1", 0, "Альфа")]
    assert scores == [0.9]
    assert section == ""


def test_a_reachable_chunk_joins_the_candidates(database, test_app):
    test_app.config["GRAPH_RAG_ENABLED"] = True
    _graph(test_app)

    chunks, scores, _section = _stage(test_app, 3)

    assert [chunk["doc_id"] for chunk in chunks] == ["doc-1", "doc-2"]
    assert chunks[1]["origin"] == "graph"
    assert len(scores) == len(chunks)


def test_the_allowance_bounds_the_candidates(database, test_app):
    test_app.config["GRAPH_RAG_ENABLED"] = True
    _graph(test_app)

    chunks, _scores, _section = _stage(test_app, 0)

    assert [chunk["doc_id"] for chunk in chunks] == ["doc-1"]


def test_the_budget_reserves_graph_room_only_when_enabled(test_app):
    from app.rag_budget import retrieval_budget

    test_app.config["GRAPH_RAG_ENABLED"] = False
    without = retrieval_budget(test_app, "ru")
    test_app.config["GRAPH_RAG_ENABLED"] = True
    with_graph = retrieval_budget(test_app, "ru")

    assert without.graph_fragments == 0
    assert with_graph.graph_fragments > 0
    assert with_graph.top_k < without.top_k
