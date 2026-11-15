"""Co-occurrence edges: two entities found in one chunk are linked."""

from __future__ import annotations

import pytest

from app import graph_extract

pytestmark = pytest.mark.unit

CHUNK = "Иван Петров работает в компании Ромашка над проектом Ласточка с марта."


def _entities(*pairs):
    return {"entities": [{"name": name, "type": kind, "description": ""} for name, kind in pairs], "relations": []}


@pytest.fixture
def written(monkeypatch):
    calls = {"nodes": [], "occurrences": [], "edges": [], "cooccurrence": []}

    def _node(user, kind, name, normalized, lemmas, description="", source_kind="document"):
        calls["nodes"].append((kind, name))
        return f"id-{normalized}"

    monkeypatch.setattr(graph_extract, "upsert_kg_node", _node)
    monkeypatch.setattr(graph_extract, "add_kg_occurrence", lambda *a, **k: calls["occurrences"].append(a))
    monkeypatch.setattr(graph_extract, "upsert_kg_edge", lambda *a, **k: calls["edges"].append(a))
    monkeypatch.setattr(
        graph_extract, "add_kg_cooccurrence", lambda *a, **k: calls["cooccurrence"].append(a), raising=False
    )
    return calls


def test_two_entities_in_one_chunk_are_linked(written):
    graph_extract._merge(
        "u1", "doc-1", 0, CHUNK, _entities(("Иван Петров", "person"), ("Ромашка", "organization"))
    )

    assert len(written["cooccurrence"]) == 1
    call = written["cooccurrence"][0]
    assert call[0] == "u1"
    assert set(call[1:3]) == {"id-иван петров", "id-ромашка"}


def test_one_entity_makes_no_edge(written):
    graph_extract._merge("u1", "doc-1", 0, CHUNK, _entities(("Иван Петров", "person")))

    assert written["cooccurrence"] == []


def test_an_entity_the_window_does_not_contain_is_not_paired(written):
    """The guard that rejects an invented entity also keeps it out of the pairs."""
    graph_extract._merge(
        "u1",
        "doc-1",
        0,
        CHUNK,
        _entities(("Иван Петров", "person"), ("Космический Корабль", "organization")),
    )

    assert written["cooccurrence"] == []


def test_the_pair_count_is_capped_per_chunk(written):
    """A chunk naming many things must not become a clique."""
    chunk = "Иван Петров Ромашка Ласточка Договор Сервер Отдел Проект Компания Заказчик Подрядчик"
    names = ["Иван Петров", "Ромашка", "Ласточка", "Договор", "Сервер", "Отдел", "Проект", "Компания", "Заказчик", "Подрядчик"]
    data = _entities(*[(name, "term") for name in names])

    graph_extract._merge("u1", "doc-1", 0, chunk, data)

    assert len(written["cooccurrence"]) <= graph_extract.COOCCURRENCE_MAX_PAIRS
    assert len(written["cooccurrence"]) > 1


@pytest.mark.requires_db
def test_the_backfill_links_the_entities_a_chunk_already_has(test_app):
    """No model call: the occurrences table already says who shares a chunk."""
    from app.database import get_db
    from app.db import add_kg_occurrence, upsert_kg_node
    from app.graph_extract import backfill_cooccurrence

    user = "graph_cooccur"
    with test_app.app_context():
        with get_db() as conn:
            c = conn.cursor()
            for table in ("kg_edges", "kg_occurrences", "kg_nodes"):
                c.execute(f"DELETE FROM {table} WHERE user_id = %s", (user,))
        ids = []
        for name in ("Ласточка", "Ромашка", "Договор"):
            node_id = upsert_kg_node(user, "term", name, name.lower(), [name.lower()], "")
            add_kg_occurrence(node_id, user, "doc-1", 0, name)
            ids.append(node_id)

        first = backfill_cooccurrence(user)
        second = backfill_cooccurrence(user)

        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT count(*) AS n FROM kg_edges WHERE user_id = %s AND predicate = 'co-occurs'",
                (user,),
            )
            edges = c.fetchone()["n"]
            c.execute("DELETE FROM kg_edges WHERE user_id = %s", (user,))
            c.execute("DELETE FROM kg_occurrences WHERE user_id = %s", (user,))
            c.execute("DELETE FROM kg_nodes WHERE user_id = %s", (user,))

    assert first["pairs"] == 3
    assert edges == 3, "a second run must not duplicate or inflate anything"
    assert second["pairs"] == 3
