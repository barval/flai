# tests/test_graph_api.py
"""Graph API: owner scope, the node cap and anonymous refusal."""

import pytest

LOGIN = "graph_api"
OTHER = "graph_api_other"


@pytest.fixture
def database(test_app):
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
                c.execute(f"DELETE FROM {table} WHERE user_id = ANY(%s)", ([LOGIN, OTHER],))
        yield


@pytest.fixture
def client(test_app):
    client = test_app.test_client()
    with client.session_transaction() as sess:
        sess["login"] = LOGIN
    return client


def _node(login: str, name: str, doc_id: str = "doc-1") -> str:
    from app.db import add_kg_occurrence, upsert_kg_node

    node_id = upsert_kg_node(login, "project", name, name.lower(), [name.lower()], "")
    add_kg_occurrence(node_id, login, doc_id, 0, name)
    return node_id


def test_the_endpoint_returns_only_the_callers_nodes(database, client):
    own = _node(LOGIN, "Своя")
    _node(OTHER, "Чужая")

    data = client.get("/api/graph").get_json()

    assert [node["id"] for node in data["nodes"]] == [own]


def test_the_node_cap_is_enforced(database, client, test_app):
    test_app.config["GRAPH_MAX_NODES"] = 2
    for index in range(5):
        _node(LOGIN, f"Узел {index}")

    data = client.get("/api/graph?limit=9999").get_json()

    assert len(data["nodes"]) == 2


def test_the_document_layer_lists_documents_and_their_edges(database, client, test_app):
    from app.database import get_db
    from app.graph_docs import record_document_centroid

    with test_app.app_context():
        with get_db() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM documents WHERE user_id = %s", (LOGIN,))
            for doc_id in ("doc-1", "doc-2"):
                c.execute(
                    "INSERT INTO documents (id, user_id, filename, file_path, file_size, file_ext, index_status)"
                    " VALUES (%s, %s, %s, %s, 10, '.pdf', 'indexed')",
                    (doc_id, LOGIN, f"{doc_id}.pdf", f"{LOGIN}/{doc_id}.pdf"),
                )
        # The layer is rebuilt on demand: two identical centroids make one pair.
        record_document_centroid(LOGIN, "doc-1", [1.0, 0.0], "bge-m3")
        record_document_centroid(LOGIN, "doc-2", [1.0, 0.0], "bge-m3")

    data = client.get("/api/graph?layer=documents").get_json()

    assert {node["id"] for node in data["nodes"]} == {"doc-1", "doc-2"}
    assert {edge["source"] for edge in data["edges"]} == {"doc-1", "doc-2"}


def test_an_anonymous_request_is_refused(database, test_app):
    response = test_app.test_client().get("/api/graph")

    assert response.status_code in (302, 401, 403)
