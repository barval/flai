# tests/test_graph_rag.py
"""Graph retrieval primitives: seeds, ranking, chunk refs and the path."""

import pytest

LOGIN = "graph_rag"


@pytest.fixture
def database(test_app):
    """An application context on a clean slate for this module's rows."""
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
        yield


def _node(name: str, lemmas: list[str] | None = None, doc_id: str = "doc-1", chunk: int = 0) -> str:
    from app.db import add_kg_occurrence, upsert_kg_node

    normalized = name.lower()
    node_id = upsert_kg_node(LOGIN, "term", name, normalized, lemmas or [normalized], "")
    add_kg_occurrence(node_id, LOGIN, doc_id, chunk, name)
    return node_id


def test_a_rare_lemma_outranks_a_common_one(database):
    from app.graph_rag import seed_nodes

    _node("Общий один", ["общий"])
    _node("Общий два", ["общий"])
    rare = _node("Редкий", ["редкий"])

    seeds = seed_nodes(LOGIN, "общий редкий", None, [], max_seeds=3)

    assert seeds[0] == rare


def test_nodes_mentioned_in_the_retrieved_chunks_become_seeds(database):
    from app.graph_rag import seed_nodes

    mentioned = _node("Сокол", doc_id="doc-7", chunk=2)

    seeds = seed_nodes(LOGIN, "запрос без совпадений", None, [("doc-7", 2)])

    assert seeds == [mentioned]


def test_chunk_refs_stay_inside_the_document_scope(database):
    from app.db import get_kg_edges, upsert_kg_edge
    from app.graph_rag import graph_chunk_refs, rank_edges

    source = _node("Альфа", doc_id="doc-1")
    inside = _node("Бета", doc_id="doc-1")
    outside = _node("Гамма", doc_id="doc-2")
    upsert_kg_edge(LOGIN, source, inside, "поставляет", "doc-1", 0, "Альфа поставляет Бета")
    upsert_kg_edge(LOGIN, source, outside, "рядом с", "doc-2", 0, "Альфа рядом с Гамма")

    refs = graph_chunk_refs(rank_edges(get_kg_edges(LOGIN, [source], hops=1, doc_ids=["doc-1"])))

    assert refs == [("doc-1", 0)]


def test_a_direct_edge_outranks_a_distant_one():
    from app.graph_rag import rank_edges

    edges = [
        {"weight": 1, "distance": 2, "doc_id": "doc-1", "chunk_index": 0},
        {"weight": 1, "distance": 1, "doc_id": "doc-1", "chunk_index": 1},
    ]

    assert rank_edges(edges)[0]["distance"] == 1


def test_the_path_between_two_nodes_returns_its_edges(database):
    from app.db import upsert_kg_edge
    from app.graph_rag import shortest_path_edges

    first = _node("Альфа")
    middle = _node("Бета")
    last = _node("Гамма")
    upsert_kg_edge(LOGIN, first, middle, "поставляет", "doc-1", 0, "Альфа поставляет Бета")
    upsert_kg_edge(LOGIN, middle, last, "входит в", "doc-1", 1, "Бета входит в Гамма")

    path = shortest_path_edges(LOGIN, first, last, None)

    assert [edge["predicate"] for edge in path] == ["поставляет", "входит в"]
