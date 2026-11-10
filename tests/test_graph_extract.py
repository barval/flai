# tests/test_graph_extract.py
"""Extraction output: schema validation, normalisation and merging."""

import json

import pytest

LOGIN = "graph_extract"


@pytest.fixture
def database(test_app):
    """An application context on a clean slate for this module's rows."""
    from app.database import get_db

    with test_app.app_context():
        with get_db() as conn:
            c = conn.cursor()
            for table in ("kg_edges", "kg_occurrences", "kg_nodes", "kg_doc_edges", "kg_doc_vectors"):
                c.execute(f"DELETE FROM {table} WHERE user_id = %s", (LOGIN,))
        yield


def _payload(name: str, evidence: str = "проект Сокол стартовал") -> str:
    return json.dumps(
        {
            "entities": [{"name": name, "type": "project", "description": ""}],
            "relations": [],
        },
        ensure_ascii=False,
    )


def test_an_unknown_entity_kind_is_rejected():
    from app.graph_extract import parse_extraction

    raw = '{"entities":[{"name":"X","type":"spaceship","description":""}],"relations":[]}'

    assert parse_extraction(raw) is None


def test_truncated_json_is_rejected():
    from app.graph_extract import parse_extraction

    assert parse_extraction('{"entities":[{"name":"X"') is None


def test_a_relation_whose_evidence_is_not_in_the_chunk_is_dropped(database):
    from app.db import find_kg_node_ids
    from app.graph_extract import merge_extraction, parse_extraction

    data = parse_extraction(
        json.dumps(
            {
                "entities": [
                    {"name": "Сокол", "type": "project", "description": ""},
                    {"name": "Луна", "type": "project", "description": ""},
                ],
                "relations": [
                    {
                        "source": "Сокол",
                        "target": "Луна",
                        "predicate": "рядом с",
                        "evidence": "фраза, которой в тексте нет",
                    }
                ],
            },
            ensure_ascii=False,
        )
    )

    nodes, edges = merge_extraction(LOGIN, "doc-1", 0, "проект Сокол стартовал", data)

    # "Сокол" is in the chunk and survives; "Луна" is not mentioned there at
    # all, so it is dropped too — an invented entity would merge with a
    # same-named node elsewhere and seed unrelated questions.
    assert (nodes, edges) == (1, 0)
    assert [node for node, _ in find_kg_node_ids(LOGIN, ["сокол"], None, 8)]


def test_the_same_entity_merged_across_documents_keeps_one_node(database):
    from app.db import find_kg_node_ids
    from app.graph_extract import merge_extraction, parse_extraction

    merge_extraction(LOGIN, "doc-1", 0, "ООО Вектор-Тех", parse_extraction(_payload("ООО Вектор-Тех")))
    merge_extraction(LOGIN, "doc-2", 0, "Вектор-Тех", parse_extraction(_payload("Вектор-Тех")))

    assert len(find_kg_node_ids(LOGIN, ["вектор"], None, 8)) == 1
