# tests/test_graph_store.py
"""Knowledge graph storage: occurrences, edge weight, traversal scope."""

import pytest

LOGIN = "graph_alice"


@pytest.fixture
def database(test_app):
    """An application context on a clean slate for this module's rows."""
    from app.database import get_db

    with test_app.app_context():
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM kg_edges WHERE user_id = %s", (LOGIN,))
            cursor.execute("DELETE FROM kg_occurrences WHERE user_id = %s", (LOGIN,))
            cursor.execute("DELETE FROM kg_nodes WHERE user_id = %s", (LOGIN,))
            cursor.execute("DELETE FROM kg_doc_edges WHERE user_id = %s", (LOGIN,))
            cursor.execute("DELETE FROM kg_doc_vectors WHERE user_id = %s", (LOGIN,))
            cursor.execute("DELETE FROM kg_build_state WHERE user_id = %s", (LOGIN,))
        yield


def _node(name: str, kind: str = "organization") -> str:
    from app.db import upsert_kg_node

    return upsert_kg_node(LOGIN, kind, name, name.lower(), [name.lower()], "")


@pytest.mark.requires_db
def test_deleting_a_document_removes_its_mentions_and_orphan_nodes(database):
    from app.db import add_kg_occurrence, delete_kg_document, find_kg_node_ids

    node = _node("Sokol")
    add_kg_occurrence(node, LOGIN, "doc-1", 3, "проект Сокол")

    assert find_kg_node_ids(LOGIN, ["sokol"], None, 8) == [(node, 1)]

    delete_kg_document("doc-1", LOGIN)

    assert find_kg_node_ids(LOGIN, ["sokol"], None, 8) == []


@pytest.mark.requires_db
def test_repeated_edge_raises_its_weight(database):
    from app.db import get_kg_edges, upsert_kg_edge

    source = _node("Alpha")
    target = _node("Beta")
    for _ in range(3):
        upsert_kg_edge(LOGIN, source, target, "supplies", "doc-1", 0, "Alpha supplies Beta")

    edges = get_kg_edges(LOGIN, [source], hops=1, doc_ids=None)

    assert len(edges) == 1
    assert edges[0]["weight"] == 3


@pytest.mark.requires_db
def test_traversal_stays_inside_the_document_scope(database):
    from app.db import get_kg_edges, upsert_kg_edge

    source = _node("Alpha")
    inside = _node("Beta")
    outside = _node("Gamma")
    upsert_kg_edge(LOGIN, source, inside, "supplies", "doc-1", 0, "Alpha supplies Beta")
    upsert_kg_edge(LOGIN, source, outside, "competes with", "doc-2", 0, "Alpha competes with Gamma")

    edges = get_kg_edges(LOGIN, [source], hops=1, doc_ids=["doc-1"])

    assert [edge["doc_id"] for edge in edges] == ["doc-1"]
