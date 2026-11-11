# app/graph_docs.py
"""Document layer of the knowledge graph: chunk centroids and similarity edges.

A document's centroid is the mean of its chunk vectors. Two documents are
linked when their centroids are close, which is the cheapest useful layer of
the graph: no model call, and it is immediately available for the Documents
panel and for hierarchical retrieval.
"""

import math

from app.database import get_db
from app.db import replace_kg_doc_edges


def _vector_of(point):
    if isinstance(point, dict):
        return point.get("vector")
    return getattr(point, "vector", None)


def centroid_from_points(points):
    """Mean vector of a document's chunks, or ``None`` when there are none."""
    vectors = [list(vector) for vector in (_vector_of(point) for point in points) if vector]
    if not vectors:
        return None
    dim = len(vectors[0])
    totals = [0.0] * dim
    for vector in vectors:
        for index, value in enumerate(vector[:dim]):
            totals[index] += float(value)
    count = len(vectors)
    return [total / count for total in totals]


def _cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def record_document_centroid(user_id, doc_id, vector, embedding_model):
    """Store (or refresh) one document's centroid."""
    if not vector:
        return
    with get_db() as conn:
        conn.cursor().execute(
            """
            INSERT INTO kg_doc_vectors (doc_id, user_id, vector, dim, embedding_model, updated_at)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (doc_id) DO UPDATE
            SET user_id = EXCLUDED.user_id, vector = EXCLUDED.vector, dim = EXCLUDED.dim,
                embedding_model = EXCLUDED.embedding_model, updated_at = CURRENT_TIMESTAMP
            """,
            (doc_id, user_id, [float(value) for value in vector], len(vector), embedding_model),
        )


def _centroid_count(user_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT count(*) AS n FROM kg_doc_vectors WHERE user_id = %s", (user_id,))
        return int(c.fetchone()["n"])


def document_layer_ready(user_id):
    """True when the stored document edges match the stored centroids."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT count(*) AS total, count(DISTINCT embedding_model) AS models"
            " FROM kg_doc_vectors WHERE user_id = %s",
            (user_id,),
        )
        row = c.fetchone()
        c.execute("SELECT vectors FROM kg_doc_layer WHERE user_id = %s", (user_id,))
        layer = c.fetchone()
    if not row["total"] or row["models"] != 1:
        return False
    return bool(layer) and int(layer["vectors"]) == int(row["total"])


def backfill_centroids(app, user_id):
    """Compute centroids for documents indexed before the graph existed.

    The vectors are already in Qdrant; only the mean was never stored, so the
    document layer of an existing corpus would otherwise stay empty forever.
    """
    from app.db import get_user_documents

    rag = app.modules.get("rag") if hasattr(app, "modules") else None
    if not rag or not rag.available:
        return 0
    config = {}
    try:
        from app.model_config import get_model_config

        config = get_model_config("embedding") or {}
    except Exception:  # noqa: BLE001 — the model name is cosmetic here
        config = {}
    written = 0
    for document in get_user_documents(user_id) or []:
        if not document.get("id"):
            continue
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT 1 FROM kg_doc_vectors WHERE doc_id = %s", (document["id"],))
            if c.fetchone():
                continue
        try:
            points = rag._scroll_document_points(user_id, document["id"])
        except Exception:  # noqa: BLE001 — one unreadable document is not the layer
            continue
        centroid = centroid_from_points(points)
        if centroid:
            record_document_centroid(user_id, document["id"], centroid, config.get("model_name", ""))
            written += 1
    return written


def ensure_document_layer(user_id, similarity=0.60, neighbours=8):
    """Rebuild the document layer only when a centroid arrived since last time.

    Kept out of the indexing path: at a few hundred documents the exhaustive
    pass is millions of float operations, which has no business running inside
    an upload task, and the layer is only ever read by the graph view.
    """
    if document_layer_ready(user_id):
        return 0
    return rebuild_document_edges(user_id, similarity=similarity, neighbours=neighbours)


def rebuild_document_edges(user_id, similarity=0.75, neighbours=8):
    """Recompute the account's document layer from its stored centroids.

    Exhaustive on purpose: the account holds at most a few hundred documents, so
    an O(n²) pass in plain Python costs nothing and leaves no stale pair behind
    after an upload, a re-index or a deletion.
    """
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT doc_id, vector, embedding_model FROM kg_doc_vectors WHERE user_id = %s"
            " ORDER BY updated_at DESC",
            (user_id,),
        )
        rows = c.fetchall()

    if len(rows) < 2:
        written = replace_kg_doc_edges(user_id, [])
        _mark_layer(user_id, len(rows))
        return written

    # A layer built from two different embedding models is not comparable. The
    # newest model wins and the older centroids are simply not linked.
    newest_model = rows[0]["embedding_model"]
    rows = [row for row in rows if row["embedding_model"] == newest_model]

    floor = 0.35
    edges = []
    for index, row in enumerate(rows):
        scores = []
        for other_index, other in enumerate(rows):
            if index == other_index:
                continue
            score = _cosine(row["vector"], other["vector"])
            if score >= floor:
                scores.append((other["doc_id"], score))
        scores.sort(key=lambda pair: pair[1], reverse=True)
        # The strongest neighbours are kept whether or not they clear the
        # configured threshold: an isolated dot says nothing, and the top pair of
        # a small corpus can sit below any absolute cut.
        chosen = [pair for pair in scores[:neighbours] if pair[1] >= similarity] or scores[:2]
        for neighbour_doc_id, score in chosen:
            edges.append((row["doc_id"], neighbour_doc_id, score))
    written = replace_kg_doc_edges(user_id, edges)
    _mark_layer(user_id, len(rows))
    return written


def _mark_layer(user_id, vectors):
    with get_db() as conn:
        conn.cursor().execute(
            """
            INSERT INTO kg_doc_layer (user_id, vectors, rebuilt_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id) DO UPDATE SET vectors = EXCLUDED.vectors, rebuilt_at = CURRENT_TIMESTAMP
            """,
            (user_id, int(vectors)),
        )
