# app/routes/graph.py
"""Knowledge-graph API for the graph view (v13.2).

Every answer is scoped to the caller: no id from another account can be
resolved here, and no filesystem path leaves the server.
"""

from flask import Blueprint, jsonify, request, session

from app.db import get_kg_document_graph, get_kg_graph, kg_status

bp = Blueprint("graph", __name__, url_prefix="/api")


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
    if layer == "documents":
        return jsonify(get_kg_document_graph(login, limit=limit))

    return jsonify(
        get_kg_graph(login, doc_id=request.args.get("doc", ""), kind=request.args.get("kind", ""), limit=limit)
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

    from app.cli import _graph_documents
    from app.db import count_rag_chunks, get_kg_build_state, set_kg_build_state

    queued = 0
    for document in _graph_documents(login):
        state = get_kg_build_state(document, login)
        total = count_rag_chunks(login, document)
        if total == 0:
            set_kg_build_state(document, login, "empty", 0, 0, "")
            continue
        if state and state.get("status") == "done" and int(state.get("chunks_done") or 0) >= total:
            continue
        start = int((state or {}).get("chunks_done") or 0)
        queue.enqueue_graph_extract(login, document, 0 if start >= total else start)
        queued += 1
    return jsonify({"queued": queued})
