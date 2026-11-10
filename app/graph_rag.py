# app/graph_rag.py
"""Query-time graph retrieval: seeds, weighted traversal, chunk refs and paths.

Nothing here inspects the wording of a question. Seeds come from lemma overlap
and from the chunks the vector half already retrieved; the rest is arithmetic
on the graph itself.
"""

from app.database import get_db
from app.db import get_kg_edges, get_kg_node_ids_for_chunks
from app.lemmatizer import lemmas as lemmatize


def seed_lemmas(query: str) -> list[str]:
    """Content lemmas of the question, long enough to be meaningful."""
    return [lemma for lemma in dict.fromkeys(lemmatize(query or "")) if len(lemma) > 2]


def _candidate_nodes(user_id: str, lemmas: list[str], doc_ids: list[str] | None, limit: int) -> list[dict]:
    """Nodes sharing at least one lemma with the question, with their lemma sets.

    The lemma sets come back so the caller can weigh a match by how rare each
    lemma is: "работа" appears everywhere, "Сокол" does not.
    """
    if not lemmas:
        return []
    params: list = [user_id, list(lemmas)]
    scope_sql = ""
    if doc_ids is not None:
        scope_sql = (
            " AND EXISTS (SELECT 1 FROM kg_occurrences o"
            " WHERE o.node_id = n.id AND o.user_id = n.user_id AND o.doc_id = ANY(%s::text[]))"
        )
        params.append(list(doc_ids))
    params.append(limit)
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            f"""
            SELECT n.id, n.lemmas
            FROM kg_nodes n
            WHERE n.user_id = %s AND n.lemmas && %s::text[]{scope_sql}
            LIMIT %s
            """,
            tuple(params),
        )
        return [{"id": str(row["id"]), "lemmas": list(row["lemmas"] or [])} for row in c.fetchall()]


def _lemma_frequencies(user_id: str, lemmas: list[str]) -> dict[str, int]:
    """How many nodes of the account carry each lemma."""
    if not lemmas:
        return {}
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT lemma, count(*) AS n
            FROM (SELECT unnest(lemmas) AS lemma FROM kg_nodes WHERE user_id = %s) AS terms
            WHERE lemma = ANY(%s::text[])
            GROUP BY lemma
            """,
            (user_id, list(lemmas)),
        )
        return {row["lemma"]: int(row["n"]) for row in c.fetchall()}


def seed_nodes(
    user_id: str,
    query: str,
    doc_ids: list[str] | None = None,
    top_chunk_refs: list[tuple[str, int]] | None = None,
    max_seeds: int = 8,
) -> list[str]:
    """Entity seeds for one question: lemma matches first, retrieved chunks second."""
    question_lemmas = seed_lemmas(query)
    seeds: list[tuple[float, str]] = []

    if question_lemmas:
        frequencies = _lemma_frequencies(user_id, question_lemmas)
        for candidate in _candidate_nodes(user_id, question_lemmas, doc_ids, max_seeds * 4):
            matched = [lemma for lemma in candidate["lemmas"] if lemma in question_lemmas]
            if not matched:
                continue
            score = sum(1.0 / max(1, frequencies.get(lemma, 1)) for lemma in matched)
            seeds.append((score, candidate["id"]))

    for node_id in get_kg_node_ids_for_chunks(user_id, top_chunk_refs or []):
        if node_id not in {node for _, node in seeds}:
            seeds.append((0.0, node_id))

    seeds.sort(key=lambda pair: pair[0], reverse=True)
    return [node_id for _, node_id in seeds[:max_seeds]]


def rank_edges(edges: list[dict]) -> list[dict]:
    """Order edges by weight, distance and the node's standing in the graph."""
    def score(edge: dict) -> float:
        pagerank = float(edge.get("pagerank") or 0.0)
        distance = int(edge.get("distance") or 1)
        return float(edge.get("weight") or 1) * (1.0 / (1.0 + distance)) * (1.0 + 0.3 * pagerank)

    return sorted(edges, key=score, reverse=True)


def graph_chunk_refs(edges: list[dict], max_edges: int = 12) -> list[tuple[str, int]]:
    """The chunks that support the strongest edges, in order and deduplicated."""
    refs: list[tuple[str, int]] = []
    for edge in edges[:max_edges]:
        ref = (edge.get("doc_id"), edge.get("chunk_index"))
        if ref[0] is None or ref[1] is None or ref in refs:
            continue
        refs.append(ref)
    return refs


def shortest_path_edges(user_id: str, source: str, target: str, doc_ids: list[str] | None, hops: int = 4) -> list[dict]:
    """Edges along the shortest relation path between two entities."""
    if not source or not target or source == target:
        return []
    import networkx as nx

    edges = get_kg_edges(user_id, [source], hops=hops, doc_ids=doc_ids)
    graph = nx.DiGraph()
    by_pair: dict[tuple[str, str], dict] = {}
    for edge in edges:
        pair = (edge["src_id"], edge["dst_id"])
        by_pair[pair] = edge
        graph.add_edge(edge["src_id"], edge["dst_id"], weight=1.0 / max(1, int(edge.get("weight") or 1)))

    if source not in graph or target not in graph or not nx.has_path(graph, source, target):
        return []
    path = nx.shortest_path(graph, source, target, weight="weight")
    return [by_pair[(left, right)] for left, right in zip(path, path[1:], strict=False) if (left, right) in by_pair]

