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


def graph_lemmas(text: str) -> list[str]:
    """Lemmas as the graph keys them: Russian analysed, English folded.

    The lexical index of a document is stored without English folding, so this
    stays graph-local and no corpus has to be re-indexed.
    """
    return lemmatize(text, stem_latin=True)

ENTITY_KINDS = ("person", "organization", "project", "document", "event", "place", "term")

# A closed vocabulary: measured on the live corpus, an open list produced
# "происходит на", "созвон с" and "обновление" — ad-hoc phrases that never merge
# across documents and cannot gain weight.
PREDICATES = {
    "ru": (
        "поставляет",
        "входит в",
        "работает в",
        "руководит",
        "подписал",
        "арендует",
        "обслуживает",
        "выполняет",
        "запрещает",
        "разрешает",
        "расположен в",
        "связан с",
    ),
    "en": (
        "supplies",
        "belongs to",
        "works for",
        "leads",
        "signed",
        "rents",
        "maintains",
        "performs",
        "forbids",
        "allows",
        "located in",
        "related to",
    ),
}

_PUNCTUATION = re.compile(r"[\"'«»„“”()\[\]{}.,;:!?]+")
# Legal forms carry no identity: "ООО Вектор-Тех" and "Вектор-Тех" are one
# company, and leaving the prefix in the key creates a second node.
_LEGAL_FORMS = {
    "ооо", "оао", "зао", "пао", "ао", "ип", "нко",
    "llc", "ltd", "inc", "gmbh", "corp", "corporation", "company", "co", "limited", "incorporated", "plc",
}
_WHITESPACE = re.compile(r"\s+")


def normalise_entity(name: str) -> tuple[str, list[str]]:
    """Canonical key and lemma set of an entity name.

    "ООО «Вектор-Тех»", "Вектор-Тех" and "Вектор-Теха" collapse onto one node
    because the key is built from lemmas, not from the surface form.
    """
    cleaned = _PUNCTUATION.sub(" ", (name or "").strip().lower())
    words = [word for word in _WHITESPACE.sub(" ", cleaned).strip().split() if word]
    # Both ends: "ООО Вектор-Тех" and "Acme Corp" carry the form on either side.
    while len(words) > 1 and words[0] in _LEGAL_FORMS:
        words = words[1:]
    while len(words) > 1 and words[-1] in _LEGAL_FORMS:
        words = words[:-1]
    cleaned = " ".join(words)
    lemma_list = graph_lemmas(cleaned)
    if not lemma_list:
        return cleaned, []
    # Sorted: the order of a name carries nothing, and "Валерий Барсуков" must
    # key the same as "Барсуков Валерий".
    return " ".join(sorted(set(lemma_list))), lemma_list


def _normalise_evidence(text: str) -> str:
    return _WHITESPACE.sub(" ", (text or "").strip().lower())


def parse_extraction(raw: str) -> dict | None:
    """Parse the model's answer, or return ``None`` on any schema drift."""
    if not raw:
        return None
    text = raw.strip()
    # Both fences are stripped independently: the model sometimes closes a
    # block it never opened, and that stray fence alone made the answer
    # unparsable, so a chunk with valid entities was retried four times.
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
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


def merge_extraction_with_stats(
    user_id: str, doc_id: str, chunk_index: int, chunk_text: str, data: dict
) -> dict:
    """Same as :func:`merge_extraction`, plus what the evidence check rejected."""
    result = _merge(user_id, doc_id, chunk_index, chunk_text, data)
    return result


def merge_extraction(user_id: str, doc_id: str, chunk_index: int, chunk_text: str, data: dict) -> tuple[int, int]:
    result = _merge(user_id, doc_id, chunk_index, chunk_text, data)
    return result["nodes"], result["edges"]


def _merge(user_id: str, doc_id: str, chunk_index: int, chunk_text: str, data: dict) -> dict:
    """Write one chunk's extraction into the graph.

    A relation survives only when its quoted evidence really occurs in the
    chunk; a relation whose endpoint was not extracted is dropped instead of
    creating a node the model never named.
    """
    if not data:
        return {"nodes": 0, "edges": 0, "entities": 0, "relations_found": 0, "relations_kept": 0}

    haystack = _normalise_evidence(chunk_text)
    chunk_lemmas = set(graph_lemmas(chunk_text))
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
    proposed = len(data.get("relations", []))
    window_lemmas = set(graph_lemmas(chunk_text))
    for relation in data.get("relations", []):
        evidence = _normalise_evidence(relation.get("evidence", ""))
        if not evidence:
            continue
        if evidence not in haystack:
            # A verbatim match is too strict for an 8B model: measured, half the
            # proposed relations were dropped for a reordered quote. The quote
            # still has to be made of words the window actually contains.
            words = set(graph_lemmas(evidence))
            if not words or len(words & window_lemmas) / len(words) < 0.8:
                continue
        source_normalized, _ = normalise_entity(relation["source"])
        target_normalized, _ = normalise_entity(relation["target"])
        source_id = nodes.get(source_normalized)
        target_id = nodes.get(target_normalized)
        # An endpoint the model named inside a relation but forgot in the entity
        # list is still a real mention: create it, provided its words are in the
        # window (the same guard that rejects invented entities).
        for missing, key in ((source_id, source_normalized), (target_id, target_normalized)):
            if missing:
                continue
            name = relation["source"] if missing is source_id else relation["target"]
            _, name_lemmas = normalise_entity(name)
            if not name_lemmas or not window_lemmas.intersection(name_lemmas):
                continue
            node_id = upsert_kg_node(user_id, "term", name, key, name_lemmas, "")
            add_kg_occurrence(node_id, user_id, doc_id, chunk_index, name)
            nodes[key] = node_id
            written_nodes += 1
        source_id = nodes.get(source_normalized)
        target_id = nodes.get(target_normalized)
        if not source_id or not target_id or source_id == target_id:
            continue
        upsert_kg_edge(
            user_id,
            source_id,
            target_id,
            canonical_predicate(relation["predicate"]),
            doc_id,
            chunk_index,
            relation.get("evidence", ""),
        )
        written_edges += 1

    return {
        "nodes": written_nodes,
        "edges": written_edges,
        "entities": len(data.get("entities", [])),
        "relations_found": proposed,
        "relations_kept": written_edges,
    }


def _stem_key(lemma: str) -> str:
    """A five-letter prefix: enough to identify a stem, short enough to ignore inflection."""
    return lemma[:5] if len(lemma) >= 5 else lemma


def canonical_predicate(predicate: str) -> str:
    """Map a free-form predicate onto the closed list by lemma overlap.

    "осуществляет поставку", "поставка" and "поставляет" become one predicate,
    which is what lets two documents share an edge and give it weight.

    Both lists are matched. The call used to pin the Russian one, so every
    English predicate came back unchanged even though the English prompt offers
    a list; Latin and Cyrillic lemmas cannot collide, so one lookup serves a
    bilingual corpus as well.
    """
    allowed = PREDICATES["ru"] + PREDICATES["en"]
    cleaned = _WHITESPACE.sub(" ", (predicate or "").strip().lower())
    for candidate in allowed:
        if cleaned == candidate:
            return candidate
    wanted = {_stem_key(lemma) for lemma in graph_lemmas(cleaned)}
    if not wanted:
        return cleaned
    # Compare stems, not whole lemmas: the list is written in one surface form
    # ("поставляет"), and "поставка" or "осуществляет поставку" must map onto it.
    best, best_hits = cleaned, 0
    for candidate in allowed:
        stems = {_stem_key(lemma) for lemma in graph_lemmas(candidate)}
        hits = len(wanted & stems)
        if hits > best_hits:
            best, best_hits = candidate, hits
    return best if best_hits else cleaned


def build_extraction_prompt(chunk_text: str, lang: str = "ru", context_before: str = "", context_after: str = "") -> str:
    """Render the extraction instruction for one chunk."""
    from app.utils import format_prompt

    kinds = ", ".join(ENTITY_KINDS)
    # A document may contain braces: escape them before the template is
    # formatted, otherwise a stray "{" raises KeyError inside format_prompt.
    safe_text = (chunk_text or "").replace("{", "{{").replace("}", "}}")
    safe_before = (context_before or "").replace("{", "{{").replace("}", "}}")
    safe_after = (context_after or "").replace("{", "{{").replace("}", "}}")
    return format_prompt(
        "graph_extract.template",
        {
            "chunk_text": safe_text,
            "context_before": safe_before,
            "context_after": safe_after,
            "entity_kinds": kinds,
            "predicates": ", ".join(PREDICATES.get(lang) or PREDICATES["ru"]),
        },
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
