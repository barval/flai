# app/routes/chat.py
from flask import Blueprint, current_app, make_response, redirect, render_template, session, url_for

from app.db import get_user_documents, get_user_sessions

bp = Blueprint("chat", __name__)


@bp.route("/")
def index():
    if "login" not in session:
        return redirect(url_for("auth.login"))
    return redirect(url_for("chat.chat"))


@bp.route("/chat")
def chat():
    if "login" not in session:
        return redirect(url_for("auth.login"))
    if session.get("is_admin"):
        return redirect(url_for("admin.admin_panel"))

    user_id = session["login"]
    sessions = get_user_sessions(user_id)
    documents = get_user_documents(user_id)

    if not session.get("current_session"):
        from app.db import get_last_session

        last_id = get_last_session(user_id)
        if last_id and any(s["id"] == last_id for s in sessions):
            session["current_session"] = last_id
        elif sessions:
            session["current_session"] = sessions[0]["id"]
        else:
            from app.db import create_session

            new_id = create_session(user_id, lang=session.get("language", "ru"))
            session["current_session"] = new_id
            sessions = get_user_sessions(user_id)

    embedding_model = current_app.config.get("MODEL_CONFIGS", {}).get("embedding", {}).get(
        "model_name"
    ) or current_app.config.get("EMBEDDING_MODEL", "")

    response = make_response(
        render_template(
            "chat.html",
            sessions=sessions,
            documents=documents,
            current_session=session.get("current_session"),
            embedding_model=embedding_model,
        )
    )
    # The template carries the per-start STATIC_VERSION in every script URL:
    # a cached page would keep loading the OLD chat-tasks.js after a deploy
    # (live: the «1)» request still rendered as a markdown list until Ctrl+F5).
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response
