# app/graph_extract.py
"""Turn one document chunk into graph entities and relations.

The model answers a strict JSON document; everything that does not match the
schema is rejected rather than repaired, because a half-parsed extraction puts
invented facts into a graph that later answers questions with them.
"""

import json
import re

from app.db import add_kg_occurrence, upsert_kg_edge, upsert_kg_node
from app.lemmatizer import lemmas as lemmatize

ENTITY_KINDS = ("person", "organization", "project", "document", "event", "place", "term")

_PUNCTUATION = re.compile(r"[\"'«»„“”()\[\]{}.,;:!?]+")
# Legal forms carry no identity: "ООО Вектор-Тех" and "Вектор-Тех" are one
# company, and leaving the prefix in the key creates a second node.
_LEGAL_FORMS = {"ооо", "оао", "зао", "пао", "ао", "ип", "нко", "llc", "ltd", "inc", "gmbh"}
_WHITESPACE = re.compile(r"\s+")


def normalise_entity(name: str) -> tuple[str, list[str]]:
    """Canonical key and lemma set of an entity name.

    "ООО «Вектор-Тех»", "Вектор-Тех" and "Вектор-Теха" collapse onto one node
    because the key is built from lemmas, not from the surface form.
    """
    cleaned = _PUNCTUATION.sub(" ", (name or "").strip().lower())
    words = [word for word in _WHITESPACE.sub(" ", cleaned).strip().split() if word]
    if len(words) > 1 and words[0] in _LEGAL_FORMS:
        words = words[1:]
    cleaned = " ".join(words)
    lemma_list = lemmatize(cleaned)
    if not lemma_list:
        return cleaned, []
    return " ".join(lemma_list), lemma_list


def _normalise_evidence(text: str) -> str:
    return _WHITESPACE.sub(" ", (text or "").strip().lower())


def parse_extraction(raw: str) -> dict | None:
    """Parse the model's answer, or return ``None`` on any schema drift."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    entities = data.get("entities")
    relations = data.get("relations", [])
    if not isinstance(entities, list) or not isinstance(relations, list):
        return None

    clean_entities = []
    for entity in entities:
        if not isinstance(entity, dict):
            return None
        name = str(entity.get("name") or "").strip()
        kind = str(entity.get("type") or "").strip().lower()
        if not name or kind not in ENTITY_KINDS:
            return None
        clean_entities.append(
            {"name": name, "type": kind, "description": str(entity.get("description") or "").strip()}
        )

    clean_relations = []
    for relation in relations:
        if not isinstance(relation, dict):
            return None
        source = str(relation.get("source") or "").strip()
        target = str(relation.get("target") or "").strip()
        predicate = str(relation.get("predicate") or "").strip()
        evidence = str(relation.get("evidence") or "").strip()
        if not (source and target and predicate):
            return None
        clean_relations.append({"source": source, "target": target, "predicate": predicate, "evidence": evidence})

    return {"entities": clean_entities, "relations": clean_relations}


def merge_extraction(user_id: str, doc_id: str, chunk_index: int, chunk_text: str, data: dict) -> tuple[int, int]:
    """Write one chunk's extraction into the graph.

    A relation survives only when its quoted evidence really occurs in the
    chunk; a relation whose endpoint was not extracted is dropped instead of
    creating a node the model never named.
    """
    if not data:
        return 0, 0

    haystack = _normalise_evidence(chunk_text)
    chunk_lemmas = set(lemmatize(chunk_text))
    nodes: dict[str, str] = {}
    written_nodes = 0

    for entity in data.get("entities", []):
        normalized, lemma_list = normalise_entity(entity["name"])
        if not normalized:
            continue
        # An entity the model invented is worse than a missed one: it merges
        # with a same-named node elsewhere and seeds unrelated questions.
        if not chunk_lemmas.intersection(lemma_list):
            continue
        node_id = upsert_kg_node(
            user_id,
            entity["type"],
            entity["name"],
            normalized,
            lemma_list,
            entity.get("description", ""),
        )
        add_kg_occurrence(node_id, user_id, doc_id, chunk_index, entity["name"])
        nodes[normalized] = node_id
        written_nodes += 1

    written_edges = 0
    for relation in data.get("relations", []):
        evidence = _normalise_evidence(relation.get("evidence", ""))
        if not evidence or evidence not in haystack:
            continue
        source_normalized, _ = normalise_entity(relation["source"])
        target_normalized, _ = normalise_entity(relation["target"])
        source_id = nodes.get(source_normalized)
        target_id = nodes.get(target_normalized)
        if not source_id or not target_id or source_id == target_id:
            continue
        upsert_kg_edge(
            user_id,
            source_id,
            target_id,
            relation["predicate"].lower(),
            doc_id,
            chunk_index,
            relation.get("evidence", ""),
        )
        written_edges += 1

    return written_nodes, written_edges


def build_extraction_prompt(chunk_text: str, lang: str = "ru") -> str:
    """Render the extraction instruction for one chunk."""
    from app.utils import format_prompt

    kinds = ", ".join(ENTITY_KINDS)
    # A document may contain braces: escape them before the template is
    # formatted, otherwise a stray "{" raises KeyError inside format_prompt.
    safe_text = (chunk_text or "").replace("{", "{{").replace("}", "}}")
    return format_prompt(
        "graph_extract.template",
        {"chunk_text": safe_text, "entity_kinds": kinds},
        lang=lang,
    )


def documents_with_chunks(user_id: str, doc_id: str = "") -> list[str]:
    """Documents of one account that have indexed chunks."""
    from app.database import get_db

    with get_db() as conn:
        c = conn.cursor()
        if doc_id:
            c.execute(
                "SELECT DISTINCT doc_id FROM rag_chunks WHERE user_id = %s AND doc_id = %s",
                (user_id, doc_id),
            )
        else:
            c.execute("SELECT DISTINCT doc_id FROM rag_chunks WHERE user_id = %s ORDER BY doc_id", (user_id,))
        return [row["doc_id"] for row in c.fetchall()]


def queue_missing_documents(queue, user_id: str, doc_id: str = "", rebuild: bool = False) -> int:
    """Queue extraction for every document that still has chunks to process.

    Shared by ``flask graph-build`` and ``POST /api/graph/build`` so the two can
    never disagree about what "already done" means.
    """
    from app.db import count_rag_chunks, delete_kg_document, get_kg_build_state, set_kg_build_state

    queued = 0
    for document in documents_with_chunks(user_id, doc_id):
        if rebuild:
            delete_kg_document(document, user_id)
        state = get_kg_build_state(document, user_id)
        total = count_rag_chunks(user_id, document)
        if total == 0:
            set_kg_build_state(document, user_id, "empty", 0, 0, "")
            continue
        if state and state.get("status") == "done" and int(state.get("chunks_done") or 0) >= total:
            continue
        start = int((state or {}).get("chunks_done") or 0)
        if start >= total:
            start = 0
        queue.enqueue_graph_extract(user_id, document, start)
        queued += 1
    return queued
