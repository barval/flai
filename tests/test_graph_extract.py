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

    # The first entity is in the chunk and survives; the second is not
    # mentioned there at all, so it is dropped too — an invented entity would
    # merge with a same-named node elsewhere and seed unrelated questions.
    assert (nodes, edges) == (1, 0)
    assert [node for node, _ in find_kg_node_ids(LOGIN, ["сокол"], None, 8)]


def test_the_same_entity_merged_across_documents_keeps_one_node(database):
    from app.db import find_kg_node_ids
    from app.graph_extract import merge_extraction, parse_extraction

    merge_extraction(LOGIN, "doc-1", 0, "ООО Вектор-Тех", parse_extraction(_payload("ООО Вектор-Тех")))
    merge_extraction(LOGIN, "doc-2", 0, "Вектор-Тех", parse_extraction(_payload("Вектор-Тех")))

    assert len(find_kg_node_ids(LOGIN, ["вектор"], None, 8)) == 1

def test_an_english_predicate_maps_onto_the_closed_list():
    from app.graph_extract import canonical_predicate

    # The list is written in one surface form, so both an inflected verb and a
    # light-verb phrase have to land on it.
    assert canonical_predicate("supplied equipment") == "supplies"
    assert canonical_predicate("is the supplier of") == "supplies"
    assert canonical_predicate("поставка") == "поставляет"
    assert canonical_predicate("осуществляет поставку") == "поставляет"


def test_an_english_entity_key_ignores_the_legal_form_and_word_order():
    from app.graph_extract import normalise_entity

    keys = {normalise_entity(name)[0] for name in ("Acme Corp", "ACME Corporation", "The Acme Company")}

    assert keys == {"acme"}


def test_an_english_relation_lands_under_one_predicate(database):
    from app.database import get_db
    from app.graph_extract import merge_extraction, parse_extraction

    chunk = "Brightline Systems supplies laptops to Project Halyard."
    data = parse_extraction(
        json.dumps(
            {
                "entities": [
                    {"name": "Brightline Systems", "type": "organization", "description": ""},
                    {"name": "Project Halyard", "type": "project", "description": ""},
                ],
                "relations": [
                    {
                        "source": "Brightline Systems",
                        "target": "Project Halyard",
                        "predicate": "supplied laptops to",
                        "evidence": "Brightline Systems supplies laptops to Project Halyard",
                    }
                ],
            }
        )
    )

    nodes, edges = merge_extraction(LOGIN, "doc-en", 0, chunk, data)

    assert (nodes, edges) == (2, 1)
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT predicate FROM kg_edges WHERE user_id = %s", (LOGIN,))
        assert [row["predicate"] for row in c.fetchall()] == ["supplies"]

def test_a_stray_closing_fence_does_not_void_the_answer():
    from app.graph_extract import parse_extraction

    # Measured on the live build: the model closed a block it never opened, and
    # a valid entity list was retried four times before the chunk failed.
    raw = '{"entities":[{"name":"mon.example.invalid","type":"place","description":""}],"relations":[]}```'

    parsed = parse_extraction(raw)

    assert parsed is not None
    assert parsed["entities"][0]["name"] == "mon.example.invalid"

def test_json_followed_by_prose_is_still_an_answer():
    from app.graph_extract import parse_extraction

    # The model closed its JSON and then explained why it found no relations.
    # Requiring the whole answer to be JSON threw the extraction away.
    raw = (
        '{"entities":[{"name":"Мотив","type":"organization","description":""}],"relations":[]}\n\n'
        "Поскольку в фрагменте нет явных связей между сущностями, связей не выделено."
    )

    parsed = parse_extraction(raw)

    assert parsed is not None
    assert parsed["entities"][0]["name"] == "Мотив"
