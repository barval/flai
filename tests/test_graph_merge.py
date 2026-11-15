"""Merging two of the caller's graph nodes through the API."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.requires_db

LOGIN = "graph_merge"
OTHER = "graph_merge_other"


def _cleanup():
    from app.database import get_db

    with get_db() as conn:
        c = conn.cursor()
        for table in ("kg_edges", "kg_occurrences", "kg_nodes"):
            c.execute(f"DELETE FROM {table} WHERE user_id = ANY(%s)", ([LOGIN, OTHER],))


def _node(login: str, name: str, doc_id: str = "doc-1") -> str:
    from app.db import add_kg_occurrence, upsert_kg_node

    node_id = upsert_kg_node(login, "project", name, name.lower(), [name.lower()], "")
    add_kg_occurrence(node_id, login, doc_id, 0, name)
    return node_id


@pytest.fixture
def client(test_app):
    test_client = test_app.test_client()
    with test_client.session_transaction() as sess:
        sess["login"] = LOGIN
    return test_client


def test_merging_moves_everything_to_the_kept_node(test_app, client):
    from app.database import get_db

    with test_app.app_context():
        _cleanup()
        victim = _node(LOGIN, "Ласточка")
        keeper = _node(LOGIN, "Проект Ласточка")

        response = client.post("/api/graph/merge", json={"source": victim, "target": keeper})

        assert response.status_code == 200
        assert response.get_json()["kept"] == keeper
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT id FROM kg_nodes WHERE user_id = %s", (LOGIN,))
            assert [str(row["id"]) for row in c.fetchall()] == [keeper]
            # Both mentions were in the same chunk, so the moved occurrence
            # collides with the keeper's own and is deduplicated on purpose.
            c.execute("SELECT count(*) AS n FROM kg_occurrences WHERE node_id = %s", (keeper,))
            assert c.fetchone()["n"] == 1
            c.execute("SELECT count(*) AS n FROM kg_occurrences WHERE node_id = %s", (victim,))
            assert c.fetchone()["n"] == 0
        _cleanup()


def test_a_node_of_another_account_is_refused(test_app, client):
    with test_app.app_context():
        _cleanup()
        mine = _node(LOGIN, "Ласточка")
        foreign = _node(OTHER, "Чужой")

        response = client.post("/api/graph/merge", json={"source": mine, "target": foreign})

        assert response.status_code == 404
        _cleanup()


def test_merging_a_node_into_itself_is_refused(test_app, client):
    with test_app.app_context():
        _cleanup()
        node = _node(LOGIN, "Ласточка")

        response = client.post("/api/graph/merge", json={"source": node, "target": node})

        assert response.status_code == 400
        _cleanup()
