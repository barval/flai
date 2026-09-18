# app/routes/rlm.py
"""RLM deep-analysis routes."""

import json

from flask import Blueprint, current_app, jsonify, request, session
from flask_babel import gettext as _

from app.db import get_db, get_user_documents, save_message, update_session_title, update_session_visit
from app.utils import validate_session_ownership

bp = Blueprint("rlm", __name__, url_prefix="/api/rlm")


@bp.route("/analyze", methods=["POST"])
def analyze():
    if "login" not in session:
        return jsonify({"error": "⚠️ " + _("Not authorized")}), 401
    user_id = session["login"]

    if not current_app.config.get("RLM_ENABLED", True):
        return jsonify({"error": "⚠️ " + _("Deep analysis is disabled")}), 403

    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    doc_ids = data.get("doc_ids") or []
    question = (data.get("question") or "").strip()

    if not session_id or not doc_ids or not question:
        return jsonify({"error": "⚠️ " + _("Select at least one document and enter a question")}), 400

    if not validate_session_ownership(session_id, user_id):
        current_app.logger.warning(f"User {user_id} attempted to use session {session_id} for RLM analysis")
        return jsonify({"error": "⚠️ " + _("Session not found")}), 403

    owned = {d["id"] for d in (get_user_documents(user_id) or [])}
    if not set(doc_ids).issubset(owned):
        current_app.logger.warning(f"User {user_id} attempted to analyze foreign documents in RLM: {doc_ids}")
        return jsonify({"error": "⚠️ " + _("Document not found")}), 403

    task_id, info = current_app.request_queue.add_rlm_task(
        user_id, session_id, doc_ids, question, lang=session.get("language", "ru")
    )

    # Persist the user question so it survives page reload (mirrors the
    # send_message route: same content JSON shape, same first-message title).
    user_content_json = json.dumps([{"type": "text", "text": question}], ensure_ascii=False)
    save_message(session_id, "user", user_content_json)

    update_session_visit(user_id, session_id)

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as cnt FROM messages WHERE session_id = %s", (session_id,))
        message_count = c.fetchone()["cnt"]
        if message_count == 1:
            update_session_title(session_id, question)

    return jsonify({"task_id": task_id, "position": info["position"]}), 202
