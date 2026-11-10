# tests/test_graph_docs.py
"""Document layer: chunk centroids and similarity edges between documents."""

from types import SimpleNamespace

import pytest

LOGIN = "graph_docs"


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
                "kg_doc_layer",
                "kg_build_state",
            ):
                c.execute(f"DELETE FROM {table} WHERE user_id = %s", (LOGIN,))
        yield


def test_centroid_is_the_mean_of_the_chunk_vectors():
    from app.graph_docs import centroid_from_points

    points = [SimpleNamespace(vector=[1.0, 0.0, 0.0]), SimpleNamespace(vector=[0.0, 1.0, 0.0])]

    assert centroid_from_points(points) == [0.5, 0.5, 0.0]


def test_edges_keep_only_the_strongest_neighbour(database):
    from app.db import get_kg_doc_edges
    from app.graph_docs import rebuild_document_edges, record_document_centroid

    record_document_centroid(LOGIN, "doc-1", [1.0, 0.0, 0.0], "bge-m3")
    record_document_centroid(LOGIN, "doc-2", [0.9, 0.43589, 0.0], "bge-m3")
    record_document_centroid(LOGIN, "doc-3", [0.8, 0.0, 0.6], "bge-m3")

    rebuild_document_edges(LOGIN, similarity=0.75, neighbours=1)

    assert [doc for doc, _ in get_kg_doc_edges(LOGIN, "doc-1")] == ["doc-2"]


def test_a_centroid_from_another_model_marks_the_layer_stale(database):
    from app.graph_docs import document_layer_ready, rebuild_document_edges, record_document_centroid

    record_document_centroid(LOGIN, "doc-1", [1.0, 0.0], "bge-m3")
    record_document_centroid(LOGIN, "doc-2", [1.0, 0.0], "bge-m3")
    rebuild_document_edges(LOGIN)
    assert document_layer_ready(LOGIN) is True

    record_document_centroid(LOGIN, "doc-3", [1.0, 0.0], "other-model")
    assert document_layer_ready(LOGIN) is False
