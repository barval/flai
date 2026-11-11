# app/routes/graph.py
"""Knowledge-graph API for the graph view (v13.2).

Every answer is scoped to the caller: no id from another account can be
resolved here, and no filesystem path leaves the server.
"""

import contextlib

from flask import Blueprint, jsonify, request, session

from app.db import get_kg_document_graph, get_kg_graph, kg_status

bp = Blueprint("graph", __name__, url_prefix="/api")


def _graph_scopes(login: str, doc_id: str = "") -> list[tuple[str, list[str] | None]]:
    """The accounts whose graph the caller may read.

    Own account unrestricted, plus the owner of every document shared with the
    caller, restricted to those document ids — the same rule the vector search
    uses, so a recipient sees the entities of a document they can already read.
    """
    from app.db import get_user_documents

    scopes: list[tuple[str, list[str] | None]] = [(login, [doc_id] if doc_id else None)]
    shared: dict[str, list[str]] = {}
    try:
        documents = get_user_documents(login, include_shared=True) or []
    except Exception:  # noqa: BLE001 — sharing is an extra, never a failure
        documents = []
    for document in documents:
        owner = document.get("shared_by")
        if not owner:
            continue
        if doc_id and str(document.get("id")) != doc_id:
            continue
        shared.setdefault(str(owner), []).append(str(document.get("id")))
    scopes.extend(shared.items())
    return scopes


def _limit(default: int) -> int:
    from flask import current_app

    cap = int(current_app.config.get("GRAPH_MAX_NODES", 400))
    try:
        requested = int(request.args.get("limit", "") or 0)
    except (TypeError, ValueError):
        requested = 0
    return min(cap, requested) if requested > 0 else cap


@bp.route("/graph", methods=["GET"])
def graph_data():
    """Nodes and edges of one layer, for the graph view."""
    login = session.get("login")
    if not login:
        return jsonify({"error": "authentication required"}), 401

    limit = _limit(400)
    layer = request.args.get("layer", "entities")
    doc_id = request.args.get("doc", "")
    scopes = _graph_scopes(login, doc_id)

    if layer == "documents":
        # The similarity layer is rebuilt on demand: an upload only stores a
        # centroid, and an exhaustive pass belongs here, not in the upload task.
        from app.graph_docs import ensure_document_layer

        for owner, _owner_doc_ids in scopes:
            # A stale layer is not a failed request: it is simply rebuilt next time.
            with contextlib.suppress(Exception):
                ensure_document_layer(owner)

    merged: dict = {"nodes": [], "edges": []}
    seen: set[str] = set()
    for owner, owner_doc_ids in scopes:
        if layer == "documents":
            part = get_kg_document_graph(owner, limit=limit, doc_ids=owner_doc_ids)
        else:
            part = get_kg_graph(
                owner,
                doc_id=doc_id if owner == login else "",
                doc_ids=owner_doc_ids if owner != login else None,
                kind=request.args.get("kind", ""),
                limit=limit,
            )
        for node in part["nodes"]:
            if node["id"] in seen or len(seen) >= limit:
                continue
            seen.add(node["id"])
            merged["nodes"].append(node)
        merged["edges"].extend(part["edges"])
    merged["edges"] = [
        edge for edge in merged["edges"] if edge["source"] in seen and edge["target"] in seen
    ]
    from app.db import get_kg_counts

    total_nodes = 0
    total_edges = 0
    for owner, owner_doc_ids in scopes:
        counts = get_kg_counts(owner, owner_doc_ids if owner != login else None)
        total_nodes += counts["nodes"]
        total_edges += counts["edges"]
    merged["total_nodes"] = total_nodes
    merged["total_edges"] = total_edges
    return jsonify(merged)


@bp.route("/graph/path", methods=["GET"])
def graph_path():
    """The shortest relation path between two of the caller's nodes."""
    login = session.get("login")
    if not login:
        return jsonify({"error": "authentication required"}), 401

    from app.db import get_kg_node_names
    from app.graph_rag import shortest_path_edges

    source = request.args.get("source", "")
    target = request.args.get("target", "")
    edges: list[dict] = []
    owner = login
    for candidate, _scope in _graph_scopes(login):
        edges = shortest_path_edges(candidate, source, target, None)
        if edges:
            owner = candidate
            break
    names = get_kg_node_names(owner, [edge[key] for edge in edges for key in ("src_id", "dst_id")])
    return jsonify(
        {
            "edges": [
                {
                    "source": edge["src_id"],
                    "target": edge["dst_id"],
                    "predicate": edge["predicate"],
                    "evidence": edge.get("evidence", ""),
                    "source_name": names.get(edge["src_id"], ""),
                    "target_name": names.get(edge["dst_id"], ""),
                }
                for edge in edges
            ]
        }
    )


@bp.route("/graph/status", methods=["GET"])
def graph_status():
    login = session.get("login")
    if not login:
        return jsonify({"error": "authentication required"}), 401
    return jsonify(kg_status(login))


@bp.route("/graph/build", methods=["POST"])
def graph_build():
    """Queue extraction for the caller's documents (owner only)."""
    from flask import current_app

    login = session.get("login")
    if not login:
        return jsonify({"error": "authentication required"}), 401
    queue = getattr(current_app, "request_queue", None)
    if queue is None:
        return jsonify({"error": "queue unavailable"}), 503

    from app.graph_extract import queue_missing_documents

    queued = queue_missing_documents(queue, login)
    return jsonify({"queued": queued})
